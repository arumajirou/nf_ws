"""時系列スタック拡張機能のスモークテスト.

- ml.tslib_backend の基本挙動
- pipelines.easytsf_runner の設定読み込み
- agents.TimeSeriesScientistAgent の最小挙動

を確認することで、拡張設計書に沿った骨格が破綻していないことをチェックする。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest
import pandas as pd

from nf_loto_platform.ml import (
    TSLibExperimentConfig,
    TSLibExperimentResult,
    run_tslib_experiment,
)
from nf_loto_platform.pipelines import EasyTSFConfig
import nf_loto_platform.pipelines.easytsf_runner as easytsf_runner
from nf_loto_platform.agents import TimeSeriesScientistAgent


def test_tslib_backend_basic_roundtrip(tmp_path: Path) -> None:
    cfg = TSLibExperimentConfig(
        dataset_name="dummy_dataset",
        model_name="dummy_model",
        horizon=12,
        input_length=24,
        metrics=["mae", "mape"],
    )

    result = run_tslib_experiment(cfg, dataset=pd.DataFrame({"y": [1, 2, 3]}))
    assert isinstance(result, TSLibExperimentResult)
    # ダミーメトリクスだが、キー構造は将来も維持されることを期待
    assert set(result.metrics.keys()) == {"mae", "mape", "rmse"}


def test_easytsf_config_from_json(tmp_path: Path) -> None:
    payload = {
        "dataset": {"source": "loto", "table": "nf_loto_dummy"},
        "experiment": {"framework": ["NeuralForecast", "TSLIB"]},
        "strategy": {"forecast_mode": ["recursive"]},
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(__import__("json").dumps(payload), encoding="utf-8")

    cfg = EasyTSFConfig.from_file(config_path)
    assert cfg.dataset["source"] == "loto"
    assert "framework" in cfg.experiment
    assert cfg.strategy["forecast_mode"] == ["recursive"]


class DummyLLMClient:
    def __init__(self) -> None:
        self.last_prompt: str | None = None

    def generate(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        prompt = f"{system_prompt}\n{user_prompt}"
        self.last_prompt = prompt
        return f"SUMMARY: {user_prompt[:50]}..."


class DummyStore:
    def __init__(self, metrics: Mapping[str, float]) -> None:
        self._metrics = metrics

    def get_metrics_for_experiment(self, experiment_id: int):
        # 実験 ID はここでは未使用だが、インターフェースを模倣
        _ = experiment_id
        return [self._metrics]


def test_time_series_scientist_agent_analyze() -> None:
    llm = DummyLLMClient()
    store = DummyStore(metrics={"mae": 1.0, "rmse": 2.0})
    agent = TimeSeriesScientistAgent(llm_client=llm, store=store)

    report = agent.analyze_experiment(experiment_id=123)
    assert "SUMMARY:" in report.summary_markdown
    assert llm.last_prompt is not None
    # 推薦は現段階では空でよい
    assert report.recommended_trials == []


def test_run_easytsf_invokes_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = EasyTSFConfig(
        raw={
            "dataset": {"table": "nf_loto_panel", "loto": "loto6", "unique_ids": ["S1", "S2"]},
            "experiment": {"horizon": 16, "objective": "mae"},
        }
    )

    dummy_outcome = type("Outcome", (), {"best_model_name": "AutoNHITS"})()
    dummy_report = object()
    captured: dict[str, Any] = {}

    class DummyTSOrchestrator:
        def __init__(self, base_orchestrator, store):
            captured["base"] = base_orchestrator
            captured["store"] = store

        def run_full_cycle_with_logging(self, task, table_name, loto, unique_ids):
            captured["task"] = task
            captured["table"] = table_name
            captured["loto"] = loto
            captured["unique_ids"] = list(unique_ids)
            return dummy_outcome, dummy_report, {"experiment_id": 7}

    monkeypatch.setattr(easytsf_runner, "TSResearchOrchestrator", DummyTSOrchestrator)
    monkeypatch.setattr(easytsf_runner, "build_agent_orchestrator", lambda: "AGENT")
    monkeypatch.setattr(easytsf_runner, "get_ts_research_client", lambda: "STORE")

    outcome, report, meta = easytsf_runner.run_easytsf(cfg, tag="run-1")

    assert outcome is dummy_outcome
    assert report is dummy_report
    assert meta["experiment_id"] == 7
    assert meta["tag"] == "run-1"
    assert captured["table"] == "nf_loto_panel"
    assert captured["unique_ids"] == ["S1", "S2"]
    assert captured["task"].objective_metric == "mae"


def test_run_easytsf_requires_unique_ids() -> None:
    cfg = EasyTSFConfig(
        raw={
            "dataset": {"table": "nf_loto_panel", "loto": "loto6", "unique_ids": []},
            "experiment": {},
        }
    )
    with pytest.raises(ValueError):
        easytsf_runner.run_easytsf(cfg)
