from __future__ import annotations

import pandas as pd
import pytest

from nf_loto_platform.ml import model_runner


def test_evaluate_metrics_returns_mae_and_mse() -> None:
    panel_df = pd.DataFrame(
        [
            {"unique_id": "s1", "ds": "2024-01-01", "y": 1.0},
            {"unique_id": "s1", "ds": "2024-01-02", "y": 2.0},
        ]
    )
    preds = pd.DataFrame(
        [
            {"unique_id": "s1", "ds": "2024-01-01", "AutoNHITS": 1.5},
            {"unique_id": "s1", "ds": "2024-01-02", "AutoNHITS": 1.0},
        ]
    )

    objective, metric = model_runner._evaluate_metrics(
        panel_df=panel_df,
        preds=preds,
        model_name="AutoNHITS",
        objective="mae",
        secondary_metric="mse",
    )

    assert objective == pytest.approx(0.75)
    assert metric == pytest.approx(0.625)


def test_evaluate_metrics_handles_missing_overlap() -> None:
    panel_df = pd.DataFrame(
        [
            {"unique_id": "s1", "ds": "2024-01-01", "y": 1.0},
        ]
    )
    preds = pd.DataFrame(
        [
            {"unique_id": "s1", "ds": "2024-02-01", "AutoNHITS": 1.5},
        ]
    )

    objective, metric = model_runner._evaluate_metrics(
        panel_df=panel_df,
        preds=preds,
        model_name="AutoNHITS",
        objective="mae",
        secondary_metric="rmse",
    )

    assert objective is None
    assert metric is None
