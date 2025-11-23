from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import pandas as pd

from nf_loto_platform.agents import AgentReport, ExperimentOutcome, TimeSeriesTaskSpec
from nf_loto_platform.agents.anomaly_agent import AnomalyAgent
from nf_loto_platform.agents.ts_research_orchestrator import TSResearchOrchestrator


@dataclass
class _DummyOutcome:
    outcome: ExperimentOutcome
    report: AgentReport


class DummyAgentOrchestrator:
    def __init__(self, sample_df: pd.DataFrame, outcome: ExperimentOutcome, report: AgentReport) -> None:
        self._sample_df = sample_df
        self._outcome = outcome
        self._report = report

    def load_sample(self, table_name: str, loto: str, unique_ids):
        return self._sample_df

    def run_full_cycle(self, task: TimeSeriesTaskSpec, table_name: str, loto: str, unique_ids):
        return self._outcome, self._report


class DummyTSResearchStore:
    def __init__(self) -> None:
        self.datasets: List[Dict[str, Any]] = []
        self.anomaly_rows: List[Dict[str, Any]] = []
        self.metric_logs: List[Dict[str, Any]] = []

    def ensure_schema(self) -> None:
        return None

    def ensure_dataset(self, **kwargs) -> int:
        self.datasets.append(kwargs)
        return 10

    def create_experiment(self, **kwargs) -> int:
        return 20

    def create_trial(self, **kwargs) -> int:
        return 30

    def insert_model_metric(self, **kwargs) -> None:
        self.metric_logs.append(kwargs)

    def update_trial_status(self, trial_id: int, status: str) -> None:
        return None

    def update_experiment_status(self, experiment_id: int, status: str) -> None:
        return None

    def bulk_insert_anomalies(self, dataset_id: int, anomaly_rows, trial_id=None) -> None:
        self.anomaly_rows = list(anomaly_rows)


def test_ts_research_orchestrator_registers_dataset_and_anomalies():
    panel_df = pd.DataFrame(
        [
            {"unique_id": "s1", "ds": "2024-01-01", "y": 1.0},
            {"unique_id": "s1", "ds": "2024-01-02", "y": 10.0},
            {"unique_id": "s1", "ds": "2024-01-03", "y": 1.0},
        ]
    )
    outcome = ExperimentOutcome(
        best_model_name="AutoNHITS",
        metrics={"mae": 0.1},
        all_model_metrics={"AutoNHITS": {"mae": 0.1}},
        run_ids=["1"],
        meta={},
    )
    report = AgentReport(summary="ok", details_markdown="md")

    base_orchestrator = DummyAgentOrchestrator(panel_df, outcome, report)
    store = DummyTSResearchStore()
    anomaly_agent = AnomalyAgent(z_threshold=0.5)

    ts_orchestrator = TSResearchOrchestrator(
        base_orchestrator=base_orchestrator,
        store=store,
        anomaly_agent=anomaly_agent,
    )

    task = TimeSeriesTaskSpec(loto_kind="loto6", target_horizon=3)

    result_outcome, result_report, meta = ts_orchestrator.run_full_cycle_with_logging(
        task=task,
        table_name="nf_loto_panel",
        loto="loto6",
        unique_ids=["s1"],
    )

    assert result_outcome.best_model_name == "AutoNHITS"
    assert result_report.summary == "ok"
    assert meta["experiment_id"] == 20

    assert store.datasets[0]["id_columns"] == ["unique_id"]
    assert store.metric_logs, "metrics should be logged to ts_research_store"
    assert store.anomaly_rows, "anomaly rows should be recorded"
    first_row = store.anomaly_rows[0]
    assert {"ts", "series_id", "score", "is_anomaly", "method"}.issubset(first_row.keys())
