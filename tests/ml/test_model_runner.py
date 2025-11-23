from __future__ import annotations

import pandas as pd
import pytest

from nf_loto_platform.ml import model_runner


def test_build_param_grid_defaults_mode_uses_single_value():
    grid = model_runner._build_param_grid(
        user_spec={"objective": ["mae", "mse"], "h": 7, "early_stop": [True, False]},
        mode="defaults",
    )
    assert len(grid) == 1
    params = grid[0]
    assert params["objective"] in {"mae", "mse"}
    assert params["h"] == 7
    # リスト指定の最初の値を採用する
    assert params["early_stop"] in (True, False)


def test_build_param_grid_grid_mode_generates_cartesian_product():
    grid = model_runner._build_param_grid(
        user_spec={"objective": ["mae", "mse"], "h": [7, 14]},
        mode="grid",
    )
    assert len(grid) == 4
    combos = {(item["objective"], item["h"]) for item in grid}
    assert combos == {("mae", 7), ("mae", 14), ("mse", 7), ("mse", 14)}


def test_prepare_dataset_returns_exog_lists():
    df = pd.DataFrame(
        {
            "unique_id": ["N1"],
            "ds": pd.to_datetime(["2024-01-01"]),
            "y": [1.0],
            "hist_temp": [0.1],
            "stat_region": ["tokyo"],
            "futr_event": [1],
        }
    )

    panel, futr_exog, hist_exog, stat_exog = model_runner._prepare_dataset(df)

    pd.testing.assert_frame_equal(panel, df)
    assert futr_exog == ["futr_event"]
    assert hist_exog == ["hist_temp"]
    assert stat_exog == ["stat_region"]


def test_evaluate_metrics_returns_objective_and_secondary_values():
    panel_df = pd.DataFrame(
        {
            "unique_id": ["A", "A"],
            "ds": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "y": [10.0, 12.0],
        }
    )
    preds = pd.DataFrame(
        {
            "unique_id": ["A", "A"],
            "ds": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "MyModel": [9.0, 15.0],
        }
    )

    objective, metric = model_runner._evaluate_metrics(
        panel_df=panel_df,
        preds=preds,
        model_name="MyModel",
        objective="mae",
        secondary_metric="mse",
    )

    assert pytest.approx(objective) == 2.0  # (1 + 3) / 2 = 2
    assert pytest.approx(metric) == 5.0  # (1 + 9) / 2 = 5


def test_sweep_loto_experiments_invokes_run_for_each_combo(monkeypatch):
    calls = []

    def fake_run_loto_experiment(**kwargs):
        calls.append(kwargs)
        preds = pd.DataFrame({"unique_id": ["A"], "ds": [pd.Timestamp("2024-01-01")], kwargs["model_name"]: [1.0]})
        meta = {"run_id": len(calls)}
        return preds, meta

    monkeypatch.setattr(model_runner, "run_loto_experiment", fake_run_loto_experiment)

    results = model_runner.sweep_loto_experiments(
        table_name="nf_loto_panel",
        loto="loto6",
        unique_ids=["N1"],
        model_names=["AutoNHITS"],
        backends=["local", "ray"],
        param_spec={"h": [7, 14]},
        mode="grid",
        objective="mae",
        num_samples=1,
    )

    assert len(calls) == 4  # 2 backends × 2 h 値
    assert len(results) == 4
    horizons = {kwargs["horizon"] for kwargs in calls}
    backends = {kwargs["backend"] for kwargs in calls}
    assert horizons == {7, 14}
    assert backends == {"local", "ray"}
