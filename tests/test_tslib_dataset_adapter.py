"""TSLib dataset adapter の基本挙動テスト."""

from __future__ import annotations

import pandas as pd

from nf_loto_platform.ml import (
    NfPanel,
    TSLibDatasetSpec,
    nfpanel_from_dataframe,
    to_tslib_long_format,
)


def test_nfpanel_validate_and_long_format() -> None:
    df = pd.DataFrame(
        {
            "unique_id": ["a", "a", "b"],
            "ds": [1, 2, 1],
            "y": [10.0, 11.0, 5.0],
        }
    )
    panel = nfpanel_from_dataframe(df)
    assert isinstance(panel, NfPanel)

    long_df = to_tslib_long_format(panel)
    assert list(long_df.columns) == ["unique_id", "ds", "y"]
    assert len(long_df) == 3


def test_tslib_dataset_spec_basic() -> None:
    spec = TSLibDatasetSpec(
        name="dummy",
        freq="D",
        horizon=24,
        input_length=48,
        extra_metadata={"source": "unit-test"},
    )
    assert spec.name == "dummy"
    assert spec.extra_metadata["source"] == "unit-test"
