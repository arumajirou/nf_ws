"""
AI Agent Orchestrator.

複数の専門エージェント (Analyst, RAG, Planner, Reflection) を協調させ、
時系列予測タスクの自律的な実行・改善ループ（PDCA）を制御する。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

from nf_loto_platform.core.settings import get_config_path
from nf_loto_platform.ml.model_runner import run_loto_experiment, ExperimentResult

# 各エージェントクラスのインポート（実装は別ファイル）
# ※ 現段階ではモックやプレースホルダとして扱う場合もあるため、
#    インポートエラー時は警告を出してダミーを使用する設計とする。
try:
    from nf_loto_platform.agents.analyst_agent import AnalystAgent
    from nf_loto_platform.agents.rag_agent import RagAgent
    from nf_loto_platform.agents.planner_agent import PlannerAgent
    from nf_loto_platform.agents.reflection_agent import ReflectionAgent
except ImportError:
    logging.getLogger(__name__).warning("Agent modules not found. Using mock agents for orchestration.")
    AnalystAgent = None
    RagAgent = None
    PlannerAgent = None
    ReflectionAgent = None

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorContext:
    """オーケストレーターの実行コンテキスト（共有メモリ）."""
    
    session_id: str
    table_name: str
    loto: str
    unique_ids: List[str]
    horizon: int
    
    # ステート
    iteration: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)
    best_run_id: Optional[int] = None
    best_score: float = float("inf")  # Lower is better (e.g. MAE)
    
    # エージェントからの出力蓄積
    analysis_report: Optional[str] = None
    rag_patterns: Optional[Dict[str, Any]] = None
    current_plan: Optional[Dict[str, Any]] = None
    last_critique: Optional[str] = None


class AgentOrchestrator:
    """自律分析ループを制御するオーケストレーター."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = self._load_config(config_path)
        self.agents = self._initialize_agents()

    def _load_config(self, path: Optional[str]) -> Dict[str, Any]:
        """エージェント設定(agent_config.yaml)をロード."""
        if path is None:
            # デフォルトパスの探索
            import os
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            path = os.path.join(base_dir, "config", "agent_config.yaml")
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Failed to load agent config from {path}: {e}. Using defaults.")
            return {}

    def _initialize_agents(self) -> Dict[str, Any]:
        """各エージェントを初期化する."""
        # ここではDI（依存性注入）的にエージェントを生成
        # 実際の実装では、LangChainのAgentExecutor等をラップしたクラスになる想定
        agents = {}
        
        # LLM設定の取得
        llm_config = self.config.get("llm", {})
        
        if AnalystAgent:
            agents["analyst"] = AnalystAgent(config=llm_config)
        if RagAgent:
            agents["rag"] = RagAgent(config=llm_config)
        if PlannerAgent:
            agents["planner"] = PlannerAgent(config=llm_config)
        if ReflectionAgent:
            agents["reflection"] = ReflectionAgent(config=llm_config)
            
        return agents

    def run_autonomous_loop(
        self,
        table_name: str,
        loto: str,
        unique_ids: List[str],
        horizon: int,
        goal_metric: str = "mae",
        max_iterations: int = 3,
        human_in_the_loop: bool = False
    ) -> List[ExperimentResult]:
        """
        自律的な改善ループを実行するメインメソッド.
        
        Flow:
            1. Analyst: データ分析
            2. RAG: 類似事例検索
            3. Loop:
                a. Planner: 分析結果と過去の履歴から設定を立案
                b. Execution: 実験実行
                c. Reflection: 結果評価と改善案提示
                d. 終了判定
        """
        session_id = str(uuid.uuid4())[:8]
        logger.info(f"Starting autonomous loop session={session_id} for {unique_ids}")
        
        ctx = OrchestratorContext(
            session_id=session_id,
            table_name=table_name,
            loto=loto,
            unique_ids=unique_ids,
            horizon=horizon
        )
        
        results = []

        # --- Step 1: Analysis Phase ---
        if "analyst" in self.agents:
            logger.info("🤖 Analyst Agent working...")
            ctx.analysis_report = self.agents["analyst"].analyze(
                table_name, loto, unique_ids
            )
        else:
            ctx.analysis_report = "Analyst agent not available. Assuming standard time series."

        # --- Step 2: Retrieval Phase ---
        if "rag" in self.agents:
            logger.info("🤖 RAG Agent searching...")
            ctx.rag_patterns = self.agents["rag"].search(
                table_name, loto, unique_ids, horizon
            )

        # --- Step 3: Optimization Loop ---
        for i in range(max_iterations):
            ctx.iteration = i + 1
            logger.info(f"=== Iteration {ctx.iteration}/{max_iterations} ===")
            
            # 3a. Planning
            if "planner" in self.agents:
                logger.info("🤖 Planner Agent deciding strategy...")
                # 前回の反省(last_critique)を含めてプランニング
                plan = self.agents["planner"].create_plan(
                    context=ctx,
                    analysis=ctx.analysis_report,
                    feedback=ctx.last_critique
                )
            else:
                # フォールバック: 初回はデフォルト、2回目以降は少しパラメータを変える簡易ロジック
                plan = self._fallback_planning(ctx)
            
            ctx.current_plan = plan
            logger.info(f"Plan: {plan.get('model_name')} (backend={plan.get('backend')})")

            # Human Approval (Optional)
            if human_in_the_loop:
                # 実運用ではここでUIからの入力を待つ実装になる
                # input("Approve plan? [y/n]: ")
                pass

            # 3b. Execution
            logger.info("🚀 Executing experiment...")
            try:
                preds, meta = run_loto_experiment(
                    table_name=table_name,
                    loto=loto,
                    unique_ids=unique_ids,
                    horizon=horizon,
                    agent_metadata={
                        "session_id": session_id,
                        "iteration": ctx.iteration,
                        "analyst_report": ctx.analysis_report,
                        "planner_rationale": plan.get("rationale", "")
                    },
                    # Planからパラメータを展開
                    model_name=plan.get("model_name", "AutoNHITS"),
                    backend=plan.get("backend", "optuna"),
                    num_samples=plan.get("num_samples", 10),
                    use_rag=(ctx.rag_patterns is not None),
                    # その他のパラメータ
                    **plan.get("model_params", {})
                )
                
                result = ExperimentResult(preds=preds, meta=meta)
                results.append(result)
                
                # ベストスコア更新チェック
                current_score = self._get_metric(meta, goal_metric)
                if current_score < ctx.best_score:
                    ctx.best_score = current_score
                    ctx.best_run_id = meta.get("run_id")
                    logger.info(f"🌟 New best score: {current_score:.4f}")

            except Exception as e:
                logger.error(f"Execution failed: {e}")
                ctx.last_critique = f"Execution failed with error: {str(e)}. Try a simpler model or reduce resource usage."
                continue

            # 3c. Reflection
            if "reflection" in self.agents:
                logger.info("🤖 Reflection Agent evaluating...")
                critique, is_satisfied = self.agents["reflection"].evaluate(
                    result=result,
                    goal_metric=goal_metric,
                    history=ctx.history
                )
                ctx.last_critique = critique
                
                # 履歴に保存
                ctx.history.append({
                    "iteration": ctx.iteration,
                    "plan": plan,
                    "score": current_score,
                    "critique": critique
                })

                if is_satisfied:
                    logger.info("✅ Reflection Agent is satisfied. Stopping loop.")
                    break
            else:
                ctx.last_critique = f"Score was {current_score}. Try to improve."

        logger.info(f"Autonomous loop finished. Best Run ID: {ctx.best_run_id}")
        return results

    def _fallback_planning(self, ctx: OrchestratorContext) -> Dict[str, Any]:
        """エージェント不在時の簡易プランニングロジック."""
        # イテレーションごとにモデルを変えるだけの単純なロジック
        models = ["AutoNHITS", "AutoTFT", "Time-MoE-50M"]
        idx = (ctx.iteration - 1) % len(models)
        model_name = models[idx]
        
        backend = "tsfm" if "Time-MoE" in model_name else "optuna"
        
        return {
            "model_name": model_name,
            "backend": backend,
            "num_samples": 5 if backend == "optuna" else 1,
            "rationale": "Fallback round-robin selection"
        }

    def _get_metric(self, meta: Dict[str, Any], metric_name: str) -> float:
        """メタデータから評価指標を抽出するヘルパー."""
        # nf_model_runs の metrics カラムは JSONB
        metrics = meta.get("metrics", {})
        if not metrics:
            return float("inf")
        
        # mae, mse, val_loss などを探索
        val = metrics.get(metric_name)
        if val is not None:
            return float(val)
            
        # 見つからない場合、test_loss や loss を探す
        for k in ["test_loss", "loss", "mae"]:
            if k in metrics:
                return float(metrics[k])
        
        return float("inf")