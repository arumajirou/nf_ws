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

import yaml

# 実装されたエージェントクラスのインポート
from nf_loto_platform.agents.analyst_agent import AnalystAgent
from nf_loto_platform.agents.rag_agent import RagAgent
from nf_loto_platform.agents.planner_agent import PlannerAgent
from nf_loto_platform.agents.reflection_agent import ReflectionAgent
from nf_loto_platform.agents.domain import TimeSeriesTaskSpec, CuratorOutput

from nf_loto_platform.ml.model_runner import run_loto_experiment, ExperimentResult

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
    current_plan: Optional[Any] = None # ExperimentRecipe
    last_critique: Optional[str] = None


class AgentOrchestrator:
    """自律分析ループを制御するオーケストレーター."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = self._load_config(config_path)
        self.agents = self._initialize_agents()

    def _load_config(self, path: Optional[str]) -> Dict[str, Any]:
        """エージェント設定をロード."""
        if path is None:
            # デフォルトパス等は省略、空の場合は空辞書で続行
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Failed to load agent config: {e}")
            return {}

    def _initialize_agents(self) -> Dict[str, Any]:
        """各エージェントを初期化する."""
        agents = {}
        
        # 全体の設定を渡す (各エージェント内で必要なセクションを取得させる)
        try:
            agents["analyst"] = AnalystAgent(config=self.config)
            agents["rag"] = RagAgent(config=self.config)
            agents["planner"] = PlannerAgent() # PlannerはRegistry依存(デフォルト使用)
            agents["reflection"] = ReflectionAgent(config=self.config)
            logger.info("All agents initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize agents: {e}")
            # 必要に応じてraiseするか、一部のみで続行する
            
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
            try:
                ctx.analysis_report = self.agents["analyst"].analyze(
                    table_name, loto, unique_ids
                )
            except Exception as e:
                logger.error(f"Analyst agent failed: {e}")
                ctx.analysis_report = "Analysis failed."

        # --- Step 2: Retrieval Phase ---
        if "rag" in self.agents:
            logger.info("🤖 RAG Agent searching...")
            try:
                ctx.rag_patterns = self.agents["rag"].search(
                    table_name, loto, unique_ids, horizon
                )
            except Exception as e:
                logger.error(f"RAG agent failed: {e}")
                ctx.rag_patterns = None

        # --- Step 3: Optimization Loop ---
        for i in range(max_iterations):
            ctx.iteration = i + 1
            logger.info(f"=== Iteration {ctx.iteration}/{max_iterations} ===")
            
            # 3a. Planning
            plan = None
            if "planner" in self.agents:
                logger.info("🤖 Planner Agent deciding strategy...")
                
                # Planner用の入力オブジェクト構築
                # 簡易的なCuratorOutputの作成 (本来はCuratorAgentが行う)
                curator_out = CuratorOutput(
                    dataset_properties={"n_obs": 2000} # 仮の値
                )
                
                task_spec = TimeSeriesTaskSpec(
                    target_horizon=horizon,
                    max_training_time_minutes=30
                )
                
                try:
                    plan = self.agents["planner"].plan(task_spec, curator_out)
                except Exception as e:
                    logger.error(f"Planner failed: {e}")

            if not plan:
                plan = self._fallback_planning(ctx)
            
            ctx.current_plan = plan
            
            # Plan (ExperimentRecipe) から情報を抽出
            model_name = plan.get("model_name") if hasattr(plan, "get") else plan.models[0]
            backend = plan.get("backend") if hasattr(plan, "get") else plan.search_backend
            
            logger.info(f"Plan: {model_name} (backend={backend})")

            # 3b. Execution
            logger.info("🚀 Executing experiment...")
            try:
                # Recipeからパラメータを展開
                params = plan.get("model_params", {}) if hasattr(plan, "get") else plan.extra_params
                
                preds, meta = run_loto_experiment(
                    table_name=table_name,
                    loto=loto,
                    unique_ids=unique_ids,
                    horizon=horizon,
                    agent_metadata={
                        "session_id": session_id,
                        "iteration": ctx.iteration,
                        "analyst_report": ctx.analysis_report,
                    },
                    model_name=model_name,
                    backend=backend,
                    # use_rag=True if ctx.rag_patterns else False,
                    **params
                )
                
                result = ExperimentResult(preds=preds, meta=meta)
                results.append(result)
                
                current_score = self._get_metric(meta, goal_metric)
                if current_score < ctx.best_score:
                    ctx.best_score = current_score
                    ctx.best_run_id = meta.get("run_id")
                    logger.info(f"🌟 New best score: {current_score:.4f}")

            except Exception as e:
                logger.error(f"Execution failed: {e}")
                ctx.last_critique = f"Execution error: {str(e)}"
                continue

            # 3c. Reflection
            if "reflection" in self.agents:
                logger.info("🤖 Reflection Agent evaluating...")
                try:
                    critique, is_satisfied = self.agents["reflection"].evaluate(
                        result=result,
                        goal_metric=goal_metric,
                        history=ctx.history
                    )
                    ctx.last_critique = critique
                    
                    ctx.history.append({
                        "iteration": ctx.iteration,
                        "score": current_score,
                        "critique": critique
                    })

                    if is_satisfied:
                        logger.info("✅ Reflection Agent is satisfied. Stopping loop.")
                        break
                except Exception as e:
                    logger.error(f"Reflection failed: {e}")

        logger.info(f"Autonomous loop finished. Best Run ID: {ctx.best_run_id}")
        return results

    def _fallback_planning(self, ctx: OrchestratorContext) -> Dict[str, Any]:
        """フォールバック用の簡易プラン."""
        return {
            "model_name": "AutoNHITS",
            "backend": "optuna",
            "num_samples": 5,
            "model_params": {}
        }

    def _get_metric(self, meta: Dict[str, Any], metric_name: str) -> float:
        """メタデータから評価指標を抽出するヘルパー."""
        metrics = meta.get("metrics", {})
        if not metrics:
            return float("inf")
        return float(metrics.get(metric_name, metrics.get("mae", float("inf"))))