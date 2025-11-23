from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any, Dict, List, Sequence

import pandas as pd

from nf_loto_platform.db import loto_repository
from nf_loto_platform.ml import model_runner
from nf_loto_platform.ml.model_registry import get_model_spec
from nf_loto_platform.tsfm.registry import get_adapter as get_tsfm_adapter

from .domain import ExperimentOutcome, ExperimentRecipe, TimeSeriesTaskSpec


def _collect_metrics_from_meta(meta: Dict[str, Any]) -> Dict[str, float]:
    metrics: Dict[str, float] = {}
    for name_key, value_key in (
        ("objective_name", "objective_value"),
        ("secondary_metric_name", "secondary_metric_value"),
    ):
        name = meta.get(name_key)
        value = meta.get(value_key)
        if isinstance(name, str) and isinstance(value, (int, float)):
            value_float = float(value)
            if math.isnan(value_float):
                continue
            metrics[name] = value_float
    return metrics


class ForecasterAgent:
    """run_loto_experiment / sweep_loto_experiments を呼び出す実験実行エージェント."""

    def __init__(self, runner_module=None) -> None:
        self._runner = runner_module or model_runner
        self._tsfm_metrics_helper = model_runner._evaluate_metrics

    def run_single(
        self,
        task: TimeSeriesTaskSpec,
        recipe: ExperimentRecipe,
        table_name: str,
        loto: str,
        unique_ids: Sequence[str],
        model_name: str,
    ) -> ExperimentOutcome:
        """単一モデルで run_loto_experiment を実行するヘルパー."""
        secondary_metric = recipe.extra_params.get("secondary_metric")

        preds, meta = self._runner.run_loto_experiment(
            table_name=table_name,
            loto=loto,
            unique_ids=unique_ids,
            model_name=model_name,
            backend=recipe.search_backend,
            horizon=task.target_horizon,
            objective=task.objective_metric,
            secondary_metric=secondary_metric,
            num_samples=recipe.num_samples,
            cpus=1,
            gpus=0,
            search_space=recipe.extra_params.get("search_space"),
            freq=recipe.extra_params.get("freq", "D"),
            local_scaler_type=recipe.extra_params.get("local_scaler_type", "robust"),
            val_size=recipe.extra_params.get("val_size"),
            refit_with_val=bool(recipe.extra_params.get("refit_with_val", True)),
            use_init_models=bool(recipe.extra_params.get("use_init_models", False)),
            early_stop=recipe.extra_params.get("early_stop"),
            early_stop_patience_steps=int(recipe.extra_params.get("early_stop_patience_steps", 3)),
        )

        collected_metrics = _collect_metrics_from_meta(meta)
        if not collected_metrics:
            collected_metrics = {task.objective_metric: float("nan")}

        all_model_metrics = {model_name: dict(collected_metrics)}
        run_ids: List[str] = [str(meta.get("run_id"))]

        return ExperimentOutcome(
            best_model_name=model_name,
            metrics=collected_metrics,
            all_model_metrics=all_model_metrics,
            run_ids=run_ids,
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
        """sweep_loto_experiments で複数モデルの実験をまとめて走らせる."""
        secondary_metric = recipe.extra_params.get("secondary_metric")

        nf_models: List[str] = []
        tsfm_models: List[str] = []
        for name in recipe.models:
            spec = get_model_spec(name)
            if spec is not None and getattr(spec, "engine_kind", "") == "tsfm":
                tsfm_models.append(name)
            else:
                nf_models.append(name)

        results: List[Any] = []

        if nf_models:
            nf_results = self._runner.sweep_loto_experiments(
                table_name=table_name,
                loto=loto,
                unique_ids=list(unique_ids),
                model_names=nf_models,
                backends=[recipe.search_backend],
                param_spec=recipe.extra_params,
                mode="defaults",
                objective=task.objective_metric,
                secondary_metric=secondary_metric,
                num_samples=recipe.num_samples,
                cpus=1,
                gpus=0,
            )
            results.extend(nf_results)

        if tsfm_models:
            results.extend(
                self._run_tsfm_models(
                    model_names=tsfm_models,
                    task=task,
                    table_name=table_name,
                    loto=loto,
                    unique_ids=unique_ids,
                    secondary_metric=secondary_metric,
                )
            )

        all_model_metrics: Dict[str, Dict[str, float]] = {}
        run_ids: List[str] = []
        best_model = None
        best_metric = None
        best_objective_name = task.objective_metric

        for r in results:
            name = r.meta.get("model_name") or "unknown"
            metrics = _collect_metrics_from_meta(r.meta)
            all_model_metrics[name] = metrics
            objective_name = r.meta.get("objective_name") or task.objective_metric
            metric_val = metrics.get(objective_name)
            if metric_val is not None:
                if best_metric is None or metric_val < best_metric:
                    best_metric = metric_val
                    best_model = name
                    best_objective_name = objective_name
            run_ids.append(str(r.meta.get("run_id")))

        if best_model is None:
            # メトリクスが記録されていない場合はとりあえず先頭をベストとする
            best_model = recipe.models[0] if recipe.models else "unknown"
            best_metric = float("nan")  # type: ignore[assignment]
            best_objective_name = task.objective_metric

        metrics = {best_objective_name: best_metric}

        return ExperimentOutcome(
            best_model_name=best_model,
            metrics=metrics,
            all_model_metrics=all_model_metrics,
            run_ids=run_ids,
            meta={"raw_results_len": len(results)},
        )

    def _run_tsfm_models(
        self,
        model_names: Sequence[str],
        task: TimeSeriesTaskSpec,
        table_name: str,
        loto: str,
        unique_ids: Sequence[str],
        secondary_metric: str | None,
    ) -> List[Any]:
        if not model_names:
            return []

        panel_df = loto_repository.load_panel_by_loto(table_name=table_name, loto=loto, unique_ids=unique_ids)
        panel_df = panel_df.copy()
        panel_df["ds"] = pd.to_datetime(panel_df["ds"])

        results: List[Any] = []
        for model_name in model_names:
            adapter = get_tsfm_adapter(model_name)
            forecast = adapter.predict(history=panel_df, horizon=task.target_horizon, freq=task.frequency)
            preds = forecast.yhat.copy()
            if model_name not in preds.columns and adapter.name in preds.columns:
                preds = preds.rename(columns={adapter.name: model_name})

            objective_value, metric_value = self._tsfm_metrics_helper(
                panel_df=panel_df,
                preds=preds,
                model_name=model_name,
                objective=task.objective_metric,
                secondary_metric=secondary_metric,
            )

            meta = {
                "run_id": f"tsfm:{model_name}",
                "model_name": model_name,
                "engine_kind": "tsfm",
                "family": "TSFM",
                "objective_name": task.objective_metric,
                "objective_value": objective_value,
                "secondary_metric_name": secondary_metric,
                "secondary_metric_value": metric_value,
            }
            results.append(SimpleNamespace(meta=meta, preds=preds))
        return results
