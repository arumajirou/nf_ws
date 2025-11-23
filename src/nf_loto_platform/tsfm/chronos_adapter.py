from __future__ import annotations

from typing import Mapping

import pandas as pd

from .base import BaseTSFMAdapter, ForecastResult, TSFMCapabilities


class Chronos2ZeroShotAdapter(BaseTSFMAdapter):
    """amazon/chronos-2 ファミリ向けの軽量アダプタ.

    テスト環境では外部ライブラリを要求しない last-value コピー戦略で
    予測を生成し、本番環境ではこのクラスを差し替えるだけで Chronos2 の
    実モデルを呼び出せるようにしている。
    """

    def __init__(self, model_id: str = "amazon/chronos-2") -> None:
        super().__init__(
            name="Chronos2-ZeroShot",
            capabilities=TSFMCapabilities(
                provider="amazon",
                model_id=model_id,
                task_types=["forecasting"],
                input_arity="both",
                supports_exogenous=True,
                zero_shot=True,
                finetuneable=False,
                max_context_length=512,
                max_horizon=None,
                license="Apache-2.0",
                commercial_allowed=True,
                hardware_pref="gpu-recommended",
            ),
        )

    def predict(
        self,
        history: pd.DataFrame,
        horizon: int,
        freq: str | None = None,
        exogenous: Mapping[str, pd.DataFrame] | None = None,
    ) -> ForecastResult:
        if history.empty:
            raise ValueError("history dataframe must not be empty")

        df = history.copy()
        df["ds"] = pd.to_datetime(df["ds"])
        inferred_freq = freq or pd.infer_freq(df.sort_values("ds")["ds"]) or "D"

        forecasts = []
        grouped = df.sort_values("ds").groupby("unique_id")
        for uid, group in grouped:
            last_row = group.iloc[-1]
            last_value = float(last_row["y"])
            start_ds = last_row["ds"]
            future_index = pd.date_range(start=start_ds, periods=horizon + 1, freq=inferred_freq)[1:]
            for ts in future_index:
                forecasts.append({"unique_id": uid, "ds": ts, self.name: last_value})

        yhat = pd.DataFrame(forecasts)
        return ForecastResult(yhat=yhat, raw_output=None, meta={"strategy": "last_value"})
