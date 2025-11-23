"""AI データサイエンティスト層 (agents パッケージ) の軽量テスト.

- TimeSeriesTaskSpec / CuratorOutput / ExperimentRecipe / ExperimentOutcome / AgentReport の整合性
- CuratorAgent -> PlannerAgent -> ForecasterAgent -> ReporterAgent の最小フロー
- AgentOrchestrator.run_full_cycle が例外なく完走すること
"""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pandas as pd
import pytest

pytest.importorskip("neuralforecast")

from nf_loto_platform.agents import (
    AgentOrchestrator,
    CuratorAgent,
    EchoLLMClient,
    ForecasterAgent,
    ExperimentRecipe,
    PlannerAgent,
    ReporterAgent,
    TimeSeriesTaskSpec,
)


@pytest.fixture
def simple_panel_df() -> pd.DataFrame:
    data = []
    for uid in ["s1", "s2"]:
        for i in range(10):
            data.append({"unique_id": uid, "ds": f"2024-01-{i+1:02d}", "y": float(i)})
    return pd.DataFrame.from_records(data)


def test_curator_agent_produces_reasonable_output(simple_panel_df: pd.DataFrame) -> None:
    task = TimeSeriesTaskSpec(loto_kind="loto6", target_horizon=4)
    curator = CuratorAgent(EchoLLMClient())
    out = curator.run(task, simple_panel_df)

    assert out.recommended_h == task.target_horizon
    assert out.recommended_validation_scheme in {"holdout", "rolling_cv"}
    assert out.candidate_feature_sets  # 非空
    assert out.data_profile["rows"] == len(simple_panel_df)


def test_planner_agent_uses_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    # レジストリを最小限のダミーで差し替え
    class DummySpec:
        def __init__(self, name: str, engine_kind: str, enabled: bool = True) -> None:
            self.name = name
            self.engine_kind = engine_kind
            self.enabled = enabled
            self.family = "TSFM"
            self.univariate = True
            self.multivariate = True
            self.forecast_type = "direct"
            self.exogenous = None

    dummy_registry = {
        "Chronos2-ZeroShot": DummySpec("Chronos2-ZeroShot", "tsfm"),
        "AutoNHITS": DummySpec("AutoNHITS", "neuralforecast"),
        "ClassicalARIMA": DummySpec("ClassicalARIMA", "classical"),
    }

    from nf_loto_platform import agents as agents_pkg

    planner = agents_pkg.PlannerAgent(registry=dummy_registry)
    task = TimeSeriesTaskSpec(loto_kind="loto6", target_horizon=4, allow_classical=False)
    curator_out = agents_pkg.CuratorOutput(
        recommended_h=4,
        recommended_validation_scheme="holdout",
        candidate_feature_sets=["NO_EXOG"],
        data_profile={},
        messages=[],
    )

    recipe = planner.plan(task, curator_out)
    # TSFM + NeuralForecast が選ばれ、classical は除外されるはず
    assert set(recipe.models) == {"Chronos2-ZeroShot", "AutoNHITS"}


def test_agent_orchestrator_full_cycle(monkeypatch: pytest.MonkeyPatch, simple_panel_df: pd.DataFrame) -> None:
    """DB・モデルランナーをスタブしてフルサイクルが完走することだけ検証する."""
    # DB スタブ
    def _fake_load_panel_by_loto(table_name: str, loto: str, unique_ids):
        return simple_panel_df

    monkeypatch.setattr(
        "nf_loto_platform.db.loto_repository.load_panel_by_loto",
        _fake_load_panel_by_loto,
    )

    # model_runner.sweep_loto_experiments をスタブ
    class DummyResult:
        def __init__(self, model_name: str, run_id: int, metric: float) -> None:
            self.meta = {
                "model_name": model_name,
                "run_id": run_id,
                "objective_name": "mae",
                "objective_value": metric,
            }
            self.preds = None

    def _fake_sweep(*args, **kwargs):
        return [
            DummyResult("AutoNHITS", 2, 0.8),
        ]

    monkeypatch.setattr(
        "nf_loto_platform.ml.model_runner.sweep_loto_experiments",
        _fake_sweep,
    )

    curator = CuratorAgent(EchoLLMClient())
    planner = PlannerAgent(
        registry={
            "Chronos2-ZeroShot": type("S", (), {"engine_kind": "tsfm", "family": "TSFM", "univariate": True, "multivariate": True, "forecast_type": "direct", "exogenous": None, "enabled": True})(),
            "AutoNHITS": type("S", (), {"engine_kind": "neuralforecast", "family": "MLP", "univariate": True, "multivariate": False, "forecast_type": "direct", "exogenous": None, "enabled": True})(),
        }
    )
    forecaster = ForecasterAgent()
    reporter = ReporterAgent(EchoLLMClient())

    orchestrator = AgentOrchestrator(
        curator=curator,
        planner=planner,
        forecaster=forecaster,
        reporter=reporter,
    )

    task = TimeSeriesTaskSpec(loto_kind="loto6", target_horizon=4)
    outcome, report = orchestrator.run_full_cycle(
        task=task,
        table_name="nf_loto_panel",
        loto="loto6",
        unique_ids=["s1", "s2"],
    )

    assert outcome.best_model_name == "AutoNHITS"
    assert "Chronos2-ZeroShot" in outcome.all_model_metrics
    assert "loto6" in report.summary
    assert report.details_markdown  # EchoLLMClient 由来の文字列


def test_forecaster_agent_uses_objective_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    """ForecasterAgent が objective_value に基づいてベストモデルを選べることを確認."""

    class DummyResult:
        def __init__(self, name: str, value: float, run_id: int) -> None:
            self.preds = None
            self.meta = {
                "model_name": name,
                "run_id": run_id,
                "objective_name": "mae",
                "objective_value": value,
            }

    class DummyRunner:
        def run_loto_experiment(self, *args, **kwargs):
            return None, {
                "run_id": 1,
                "model_name": "AutoNHITS",
                "objective_name": "mae",
                "objective_value": 0.1,
            }

        def sweep_loto_experiments(self, *args, **kwargs):
            return [DummyResult("A", 0.2, 1), DummyResult("B", 0.05, 2)]

    agent = ForecasterAgent(runner_module=DummyRunner())
    recipe = ExperimentRecipe(models=["A", "B"], feature_sets=[])
    task = TimeSeriesTaskSpec(loto_kind="loto6", target_horizon=4)
    outcome = agent.run_sweep(task, recipe, table_name="nf", loto="loto6", unique_ids=["s1"])

    assert outcome.best_model_name == "B"
    assert outcome.metrics["mae"] == pytest.approx(0.05)


def test_forecaster_agent_runs_tsfm_models(monkeypatch: pytest.MonkeyPatch, simple_panel_df: pd.DataFrame) -> None:
    class DummyAdapter:
        def __init__(self, name: str) -> None:
            self.name = name

        def predict(self, history, horizon, freq, exogenous=None):
            # 既存の ds に合わせた予測を返し、メトリクス計算が可能な状態にする
            df = simple_panel_df.tail(1).copy()
            df = df.rename(columns={"y": self.name})
            return SimpleNamespace(yhat=df[["unique_id", "ds", self.name]], raw_output=None, meta={})

    class DummyRunner:
        def sweep_loto_experiments(self, *args, **kwargs):
            return []

    monkeypatch.setattr(
        "nf_loto_platform.db.loto_repository.load_panel_by_loto",
        lambda *args, **kwargs: simple_panel_df,
    )
    monkeypatch.setattr(
        "nf_loto_platform.tsfm.registry.get_adapter",
        lambda name: DummyAdapter(name),
    )
    monkeypatch.setattr(
        "nf_loto_platform.ml.model_registry.get_model_spec",
        lambda name: type("Spec", (), {"engine_kind": "tsfm"})(),
    )

    agent = ForecasterAgent(runner_module=DummyRunner())
    recipe = ExperimentRecipe(models=["Chronos2-ZeroShot"], feature_sets=[])
    task = TimeSeriesTaskSpec(loto_kind="loto6", target_horizon=1)

    outcome = agent.run_sweep(task, recipe, table_name="nf_loto_panel", loto="loto6", unique_ids=["s1"])

    assert outcome.best_model_name == "Chronos2-ZeroShot"
    assert "Chronos2-ZeroShot" in outcome.all_model_metrics
