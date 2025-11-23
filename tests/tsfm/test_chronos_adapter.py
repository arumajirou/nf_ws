from __future__ import annotations

import pandas as pd

from nf_loto_platform.tsfm.chronos_adapter import Chronos2ZeroShotAdapter


def test_chronos_adapter_returns_last_value_forecast() -> None:
    history = pd.DataFrame(
        [
            {"unique_id": "s1", "ds": "2024-01-01", "y": 1.0},
            {"unique_id": "s1", "ds": "2024-01-02", "y": 2.0},
            {"unique_id": "s2", "ds": "2024-01-01", "y": 5.0},
        ]
    )
    adapter = Chronos2ZeroShotAdapter()
    result = adapter.predict(history=history, horizon=2)

    assert result.yhat["Chronos2-ZeroShot"].nunique() == 2
    assert len(result.yhat) == 4  # 2 series × horizon 2
