"""Orchestrator wrapper that logs experiment outcomes into ts_research.* tables.

This does not change existing behaviour of AgentOrchestrator; it simply
wraps ``run_full_cycle`` and persists high‑level metadata into the new
experiment schema so that large‑scale research and comparison becomes
easier.

Usage example (pseudo code)::

    from nf_loto_platform.agents import (
        AgentOrchestrator,
        CuratorAgent,
        PlannerAgent,
        ForecasterAgent,
        ReporterAgent,
    )
    from nf_loto_platform.agents.ts_research_orchestrator import TSResearchOrchestrator

    orchestrator = AgentOrchestrator(...)
    ts_orch = TSResearchOrchestrator(orchestrator)
    outcome, report, meta = ts_orch.run_full_cycle_with_logging(task, table, loto, ids)

"""

from __future__ import annotations

from typing import Mapping, Sequence, Tuple

from nf_loto_platform.agents.domain import AgentReport, ExperimentOutcome, TimeSeriesTaskSpec
from nf_loto_platform.agents.orchestrator import AgentOrchestrator
from nf_loto_platform.agents.causal_agent import CausalAgent
from nf_loto_platform.agents.anomaly_agent import AnomalyAgent
from nf_loto_platform.db.db_config import DB_CONFIG
from nf_loto_platform.db.ts_research_store import TSResearchStore


class TSResearchOrchestrator:
    """Wrap an AgentOrchestrator and mirror outcomes into ts_research tables."""

    def __init__(
        self,
        base_orchestrator: AgentOrchestrator,
        store: TSResearchStore | None = None,
        default_schema: str = "public",
        default_ts_column: str = "ds",
        default_target_column: str = "y",
        default_freq: str = "D",
        causal_agent: CausalAgent | None = None,
        anomaly_agent: AnomalyAgent | None = None,
    ) -> None:
        self._base = base_orchestrator
        self._store = store or TSResearchStore(DB_CONFIG)
        self._default_schema = default_schema
        self._default_ts_column = default_ts_column
        self._default_target_column = default_target_column
        self._default_freq = default_freq
        self._causal_agent = causal_agent
        self._anomaly_agent = anomaly_agent
        self._default_freq = default_freq

    def run_full_cycle_with_logging(
        self,
        task: TimeSeriesTaskSpec,
        table_name: str,
        loto: str,
        unique_ids: Sequence[str],
    ) -> Tuple[ExperimentOutcome, AgentReport, Mapping[str, int]]:
        """Run the orchestrator and write summary rows into ts_research.*.

        Returns the original (outcome, report) plus a small dict containing
        ``experiment_id`` and per‑model ``trial_id`` mappings so that
        downstream tooling can easily join against extra artefacts.
        """
        # Ensure schema exists; callers may choose to catch errors here.
        self._store.ensure_schema()

        # Register dataset (very lightweight, does not touch the raw data).
        dataset_id = self._store.ensure_dataset(
            schema_name=self._default_schema,
            table_name=table_name,
            ts_column=self._default_ts_column,
            target_column=self._default_target_column,
            id_columns=["unique_id"],
            freq=self._default_freq,
            horizon_default=task.target_horizon,
            statistics=None,
        )

        # Optionally run causal / anomaly analysis on a sample panel.
        try:
            panel_df = self._base.load_sample(table_name=table_name, loto=loto, unique_ids=unique_ids)
        except Exception:
            panel_df = None

        if self._causal_agent is not None and panel_df is not None:
            try:
                cg_result = self._causal_agent.run(panel_df, target_column=self._default_target_column)
                self._store.insert_causal_graph(
                    dataset_id=dataset_id,
                    algorithm=cg_result.algorithm,
                    graph_json=cg_result.graph_json,
                    adjacency_matrix=cg_result.adjacency_matrix,
                    interpretation=cg_result.interpretation,
                )
            except Exception:
                # 因果推定は解析用途なので、失敗してもメインフローは止めない
                pass

        if self._anomaly_agent is not None and panel_df is not None:
            try:
                anomaly_records = self._anomaly_agent.detect(
                    panel_df=panel_df,
                    value_column=self._default_target_column,
                    ts_column=self._default_ts_column,
                    id_columns=["unique_id"],
                )
                anomaly_rows = self._anomaly_agent.to_rows(anomaly_records)
                self._store.bulk_insert_anomalies(dataset_id=dataset_id, anomaly_rows=anomaly_rows)
            except Exception:
                # 異常検知も補助的なため、失敗してもメインフローは継続
                pass

        experiment_name = f"{task.loto_kind}:{table_name}:{task.target_horizon}"
        experiment_id = self._store.create_experiment(
            dataset_id=dataset_id,
            experiment_name=experiment_name,
            objective="forecast",
            horizon=task.target_horizon,
            config_json={
                "objective_metric": task.objective_metric,
                "loto_kind": task.loto_kind,
            },
            agent_reasoning=None,
        )

        # Call the original orchestrator.
        outcome, report = self._base.run_full_cycle(
            task=task,
            table_name=table_name,
            loto=loto,
            unique_ids=unique_ids,
        )

        # Persist trial‑level metrics using the aggregated outcome.
        trial_ids: dict[str, int] = {}
        framework_name = "neuralforecast"  # from current backend design

        for model_name, metrics in outcome.all_model_metrics.items():
            trial_id = self._store.create_trial(
                experiment_id=experiment_id,
                framework=framework_name,
                model_name=model_name,
                hyperparameters={},  # can be populated later from meta
                ensemble_strategy="none",
                seed=None,
                status="SUCCESS",
            )
            trial_ids[model_name] = trial_id

            for metric_name, metric_value in metrics.items():
                if metric_value is None:
                    continue
                self._store.insert_model_metric(
                    trial_id=trial_id,
                    metric_name=metric_name,
                    metric_value=float(metric_value),
                    split="val",
                    step=None,
                )

            # Mark trial as finished; more detailed resource logging can be
            # added later using bulk_insert_resource_logs.
            self._store.update_trial_status(trial_id, status="SUCCESS")

        # Experiments that reach this point are considered DONE.
        self._store.update_experiment_status(experiment_id, status="DONE")

        meta_ids = {"experiment_id": experiment_id}
        # We intentionally keep this small; caller can always query back if
        # they need richer metadata.
        return outcome, report, {**meta_ids, **{f"trial:{k}": v for k, v in trial_ids.items()}}
