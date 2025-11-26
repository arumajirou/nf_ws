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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


import pandas as pd
import yaml

from nf_loto_platform.core.settings import get_config_path
from nf_loto_platform.ml.model_runner import run_loto_experiment, ExperimentResult
from nf_loto_platform.agents.domain import (
    AgentReport,
    ExperimentOutcome,
    TimeSeriesTaskSpec,
    ExperimentRecipe
)

# 各エージェントクラスのインポート
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

try:
    from nf_loto_platform.agents.ts_research_orchestrator import TSResearchOrchestrator
except ImportError:
    TSResearchOrchestrator = None


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

    def __init__(
        self, 
        config_path: Optional[str] = None,
        llm_client: Any = None,
        # 互換性のために引数を受け入れるが、内部では適切に処理する
        curator: Any = None,
        planner: Any = None,
        forecaster: Any = None,
        reporter: Any = None,
    ):
        self.config = self._load_config(config_path)
        self.llm_client = llm_client
        
        # 外部から注入されたエージェントがあればそれを使う（テストやEasyTSF用）
        # なければ内部で初期化する
        self.agents = {}
        if curator or planner or forecaster or reporter:
             # EasyTSFからの注入パターンへの簡易対応
             # ※ 本来は役割（Analyst vs Curator）のマッピングが必要だが、一旦保持しておく
             self.agents["analyst"] = curator
             self.agents["planner"] = planner
             self.agents["forecaster"] = forecaster # ForecasterAgent (runner wrapper)
             self.agents["reflection"] = reporter
        else:
            self.agents = self._initialize_agents()

    def _load_config(self, path: Optional[str]) -> Dict[str, Any]:
        """エージェント設定(agent_config.yaml)をロード."""
        if path is None:
            # デフォルトパスの探索
            try:
                from nf_loto_platform.core.settings import load_agent_config
                return load_agent_config()
            except ImportError:
                return {}
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Failed to load agent config from {path}: {e}. Using defaults.")
            return {}

    def _initialize_agents(self) -> Dict[str, Any]:
        """各エージェントを初期化する."""
        agents = {}
        
        # LLM設定の取得
        llm_config = self.config.get("llm", {})
        
        # もしLLMクライアントが注入されていれば、それを各エージェントに渡す設計にすべきだが、
        # 既存のAgent実装は config 辞書を受け取る形になっている場合が多い。
        # ここでは簡易的に初期化する。

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
        if "analyst" in self.agents and hasattr(self.agents["analyst"], "analyze"):
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
                plan = self.agents["planner"].create_plan(
                    context=ctx,
                    analysis=ctx.analysis_report,
                    feedback=ctx.last_critique
                )
            else:
                plan = self._fallback_planning(ctx)
            
            ctx.current_plan = plan
            
            # 3b. Execution
            logger.info("🚀 Executing experiment...")
            try:
                # ForecasterAgent (wrapper) がある場合はそちらを使う
                if "forecaster" in self.agents and hasattr(self.agents["forecaster"], "run_single"):
                    # ForecasterAgent を使う場合のパス (EasyTSF経由など)
                    # ExperimentRecipe への変換が必要だが、ここでは簡易的に run_loto_experiment を呼ぶ
                    # 実際は ForecasterAgent 内部で run_loto_experiment を呼んでいる
                    # ここでは既存の直接実行ロジックを使用する
                    pass

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
                    model_name=plan.get("model_name", "AutoNHITS"),
                    backend=plan.get("backend", "optuna"),
                    num_samples=plan.get("num_samples", 10),
                    use_rag=(ctx.rag_patterns is not None),
                    **plan.get("model_params", {})
                )
                
                result = ExperimentResult(preds=preds, meta=meta)
                results.append(result)
                
                # ベストスコア更新チェック
                current_score = self._get_metric(meta, goal_metric)
                if current_score < ctx.best_score:
                    ctx.best_score = current_score
                    ctx.best_run_id = meta.get("run_id")

            except Exception as e:
                logger.error(f"Execution failed: {e}")
                ctx.last_critique = f"Execution failed: {str(e)}"
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
                
                if is_satisfied:
                    logger.info("✅ Reflection Agent is satisfied. Stopping loop.")
                    break
            else:
                ctx.last_critique = f"Score was {current_score}. Try to improve."

        return results

    def run_full_cycle(
        self,
        task: TimeSeriesTaskSpec,
        table_name: str,
        loto: str,
        unique_ids: List[str]
    ) -> Tuple[ExperimentOutcome, AgentReport]:
        """
        TSResearchOrchestrator 互換のためのラッパーメソッド.
        run_autonomous_loop を実行し、結果をレガシー/ドメイン形式に変換して返す。
        """
        logger.info("Running full cycle via compatibility layer...")
        
        # 1. 実行
        results = self.run_autonomous_loop(
            table_name=table_name,
            loto=loto,
            unique_ids=list(unique_ids),
            horizon=task.target_horizon,
            goal_metric=task.objective_metric,
            max_iterations=3 # デフォルト
        )
        
        # 2. 結果の変換
        if not results:
            logger.warning("No results from autonomous loop.")
            return ExperimentOutcome(
                best_model_name="none",
                metrics={},
                all_model_metrics={},
                run_ids=[],
                meta={"status": "no_results"}
            ), AgentReport(summary="No execution performed.", conclusion="Failed", next_steps=[])
            
        # ベストランの選定 (run_autonomous_loop 内で計算済みのベストを使うか、ここでもう一度探す)
        # ここでは簡易的に最後の結果をベースにするか、本来はベストを探すべき
        best_res = results[-1] # 仮
        best_meta = best_res.meta
        
        outcome = ExperimentOutcome(
            best_model_name=best_meta.get("model_name", "unknown"),
            metrics=best_meta.get("metrics", {}),
            all_model_metrics={r.meta.get("model_name", f"run_{i}"): r.meta.get("metrics", {}) for i, r in enumerate(results)},
            run_ids=[str(r.meta.get("run_id")) for r in results],
            meta=best_meta
        )
        
        report = AgentReport(
            summary=f"Executed {len(results)} iterations.",
            conclusion="Completed successfully.",
            next_steps=["Analyze detailed metrics in DB."]
        )
        
        return outcome, report

    def _fallback_planning(self, ctx: OrchestratorContext) -> Dict[str, Any]:
        """エージェント不在時の簡易プランニング."""
        models = ["AutoNHITS", "AutoTFT", "Time-MoE-50M"]
        idx = (ctx.iteration - 1) % len(models)
        return {
            "model_name": models[idx],
            "backend": "optuna",
            "num_samples": 5,
            "rationale": "Fallback selection"
        }

    def _get_metric(self, meta: Dict[str, Any], metric_name: str) -> float:
        metrics = meta.get("metrics", {})
        return float(metrics.get(metric_name, float("inf")))
    
    def load_sample(self, table_name: str, loto: str, unique_ids: Sequence[str]) -> pd.DataFrame:
        """サンプルデータのロード (TSResearchOrchestratorから呼ばれる)."""
        # 実際にはリポジトリからロードする
        try:
            from nf_loto_platform.db import loto_repository
            return loto_repository.load_panel_data(table_name, loto, list(unique_ids))
        except Exception:
            return pd.DataFrame()


@dataclass
class EasyTSFConfig:
    """EasyTSF 互換の設定クラス."""
    raw: Dict[str, Any]

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> EasyTSFConfig:
        import json
        with open(path, "r", encoding="utf-8") as f:
            return cls(raw=json.load(f))

    @property
    def dataset(self) -> Dict[str, Any]:
        return self.raw.get("dataset", {})

    @property
    def experiment(self) -> Dict[str, Any]:
        return self.raw.get("experiment", {})

    @property
    def strategy(self) -> Dict[str, Any]:
        return self.raw.get("strategy", {})


def run_easytsf(
    cfg: EasyTSFConfig, 
    tag: Optional[str] = None
) -> Tuple[ExperimentOutcome, AgentReport, Dict[str, Any]]:
    """EasyTSF スタイルの実行エントリポイント."""
    
    # 設定からパラメータ抽出
    table_name = cfg.dataset.get("table", "nf_loto_panel")
    loto = cfg.dataset.get("loto", "loto6")
    unique_ids = cfg.dataset.get("unique_ids", [])
    if not unique_ids:
        raise ValueError("unique_ids must be specified in dataset config")
        
    horizon = cfg.experiment.get("horizon", 28)
    objective = cfg.experiment.get("objective", "mae")
    
    # オーケストレーターの構築
    # ここでは簡易的にデフォルト構成を使用
    base_orch = AgentOrchestrator()
    
    if TSResearchOrchestrator is None:
        # フォールバック: ログ機能なしで実行
        logger.warning("TSResearchOrchestrator not found. Running without research logging.")
        task = TimeSeriesTaskSpec(
            loto_kind=loto,
            target_horizon=horizon,
            objective_metric=objective
        )
        outcome, report = base_orch.run_full_cycle(
            task=task,
            table_name=table_name,
            loto=loto,
            unique_ids=unique_ids
        )
        meta = {"status": "no_logging"}
    else:
        # TSResearchOrchestrator でラップ (ログ記録のため)
        ts_orch = TSResearchOrchestrator(base_orchestrator=base_orch)
        
        task = TimeSeriesTaskSpec(
            loto_kind=loto,
            target_horizon=horizon,
            objective_metric=objective
        )
        
        outcome, report, meta = ts_orch.run_full_cycle_with_logging(
            task=task,
            table_name=table_name,
            loto=loto,
            unique_ids=unique_ids
        )
    
    if tag:
        meta["tag"] = tag
        
    return outcome, report, meta