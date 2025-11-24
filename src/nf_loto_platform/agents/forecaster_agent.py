from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Sequence, Optional

import pandas as pd

from nf_loto_platform.ml import model_runner
from nf_loto_platform.ml.model_registry import get_model_spec
from nf_loto_platform.db import loto_repository

# TSFMアダプタの取得（オプション）
try:
    from nf_loto_platform.tsfm.registry import get_adapter as get_tsfm_adapter
except ImportError:
    get_tsfm_adapter = None

from .domain import ExperimentOutcome, ExperimentRecipe, TimeSeriesTaskSpec

logger = logging.getLogger(__name__)

def _extract_metric_value(meta: Dict[str, Any], metric_name: str) -> float:
    """
    メタデータ(metrics辞書)から指定されたメトリクスの値を抽出する.
    
    model_runner は meta['metrics'] = {'mae': 0.123, ...} の形式で返すことを想定。
    """
    metrics = meta.get("metrics", {})
    if not isinstance(metrics, dict):
        return float("nan")
    
    # 直接キーがある場合
    if metric_name in metrics:
        val = metrics[metric_name]
        return float(val) if val is not None else float("nan")
        
    return float("nan")


class ForecasterAgent:
    """
    実験実行エージェント.
    
    model_runner を使用して NeuralForecast モデルや TSFM モデルの学習・推論を行い、
    結果を ExperimentOutcome として整形して返す役割を持つ。
    """

    def __init__(self, runner_module=None) -> None:
        self._runner = runner_module or model_runner

    def run_single(
        self,
        task: TimeSeriesTaskSpec,
        recipe: ExperimentRecipe,
        table_name: str,
        loto: str,
        unique_ids: Sequence[str],
        model_name: str,
    ) -> ExperimentOutcome:
        """
        単一モデルで実験を実行する.
        
        model_runner.run_loto_experiment を呼び出し、結果をパースする。
        """
        
        # model_runner への引数を準備
        # kwargs として渡すパラメータ
        runner_kwargs = recipe.extra_params.copy()
        runner_kwargs.update({
            "search_space": recipe.extra_params.get("search_space"),
            "objective": task.objective_metric,
            "secondary_metric": recipe.extra_params.get("secondary_metric")
        })

        try:
            preds, meta = self._runner.run_loto_experiment(
                table_name=table_name,
                loto=loto,
                unique_ids=list(unique_ids),
                model_name=model_name,
                backend=recipe.search_backend,
                horizon=task.target_horizon,
                num_samples=recipe.num_samples,
                **runner_kwargs
            )
        except Exception as e:
            logger.error(f"Model execution failed for {model_name}: {e}")
            # 失敗時のフォールバック
            preds = pd.DataFrame()
            meta = {
                "status": "failed", 
                "error": str(e), 
                "run_id": "failed",
                "metrics": {}
            }

        # メトリクス抽出
        target_metric = task.objective_metric  # e.g. "mae"
        metric_val = _extract_metric_value(meta, target_metric)
        
        collected_metrics = {target_metric: metric_val}
        all_model_metrics = {model_name: collected_metrics}
        
        return ExperimentOutcome(
            best_model_name=model_name,
            metrics=collected_metrics,
            all_model_metrics=all_model_metrics,
            run_ids=[str(meta.get("run_id"))],
            meta={"single_run_meta": meta},
        )

    def run_sweep(
        self,
        task: TimeSeriesTaskSpec,
        recipe: ExperimentRecipe,
        table_name: str,
        loto: str,
        unique_ids: Sequence[str],
    ) -> ExperimentOutcome:
        """
        レシピに含まれる全モデルを実行し、最良の結果を返す (Sweep実行).
        
        runner 側に sweep 関数がない場合でも、ここでループ実行して結果を集約する。
        """
        results_meta: List[Dict[str, Any]] = []
        best_model = None
        best_score = float("inf")
        best_run_id = None
        
        all_metrics = {}
        run_ids = []

        for model_name in recipe.models:
            logger.info(f"ForecasterAgent sweeping: {model_name}")
            
            # TSFM系モデルの場合は backend を 'tsfm' に切り替える判定ロジック
            # (model_registry 等の情報があればそれを使うが、ここでは簡易判定)
            current_recipe = recipe
            if "Time-MoE" in model_name or "Chronos" in model_name:
                # backend を一時的に上書きしたコピーを作成しても良いが、
                # 今回は run_single 内で model_runner がよしなに処理することを期待する、
                # あるいは明示的に backend を渡す設計にする
                pass 

            # run_single を再利用して実行
            outcome = self.run_single(
                task=task,
                recipe=current_recipe,
                table_name=table_name,
                loto=loto,
                unique_ids=unique_ids,
                model_name=model_name
            )
            
            # 結果の集計
            score = outcome.metrics.get(task.objective_metric, float("nan"))
            all_metrics.update(outcome.all_model_metrics)
            run_ids.extend(outcome.run_ids)
            
            # ベストモデル更新 (最小化問題を仮定: MAE, RMSE等)
            if not math.isnan(score):
                if score < best_score:
                    best_score = score
                    best_model = outcome.best_model_name
                    if outcome.run_ids:
                        best_run_id = outcome.run_ids[0]
            
            results_meta.append(outcome.meta)

        # 全モデル失敗、あるいはメトリクス取得不可の場合のフォールバック
        if best_model is None:
            best_model = recipe.models[0] if recipe.models else "unknown"
            best_score = float("nan")

        return ExperimentOutcome(
            best_model_name=best_model,
            metrics={task.objective_metric: best_score},
            all_model_metrics=all_metrics,
            run_ids=run_ids,
            meta={"sweep_results": results_meta, "best_run_id": best_run_id},
        )