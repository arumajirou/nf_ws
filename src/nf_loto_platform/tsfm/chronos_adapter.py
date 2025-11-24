from __future__ import annotations

from typing import Mapping

import pandas as pd
import numpy as np

from .base import BaseTSFMAdapter, ForecastResult, TSFMCapabilities


class ChronosAdapter(BaseTSFMAdapter):
    """amazon/chronos-2 ファミリ向けの軽量アダプタ.

    テスト環境では外部ライブラリを要求しない last-value コピー戦略で
    予測を生成し、本番環境ではこのクラスを差し替えるだけで Chronos2 の
    実モデルを呼び出せるようにしている。
    """

    def __init__(self, model_id: str = "amazon/chronos-t5-tiny") -> None:
        # モデルIDはインスタンス変数として保持
        self.model_id = model_id
        
        super().__init__(
            name="Chronos", # 表示名
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
        """
        Chronosモデル (またはそのモック) を使用して予測を実行する.
        
        Args:
            history (pd.DataFrame): 履歴データ (unique_id, ds, y)
            horizon (int): 予測期間
            freq (str | None): 頻度 (例: 'D', 'H')
            exogenous (Mapping[str, pd.DataFrame] | None): 外生変数 (現状は未使用)
            
        Returns:
            ForecastResult: 予測結果オブジェクト
        """
        if history.empty:
            raise ValueError("history dataframe must not be empty")

        df = history.copy()
        # ds列をdatetime型に変換
        if "ds" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["ds"]):
            df["ds"] = pd.to_datetime(df["ds"])
            
        # 頻度の推論 (指定がない場合)
        inferred_freq = freq
        if inferred_freq is None:
            if len(df) > 1:
                inferred_freq = pd.infer_freq(df.sort_values("ds")["ds"])
            
            # 推論できなかった場合のデフォルト
            if inferred_freq is None:
                inferred_freq = "D"

        forecasts = []
        grouped = df.sort_values("ds").groupby("unique_id")
        
        for uid, group in grouped:
            # 本来はここで Chronos モデルに推論を投げる
            # 今回はモックとして、最後の値を水平に延ばす (Naive Forecast) 
            # または、単純なトレンドを加味するロジックを実装
            
            last_row = group.iloc[-1]
            last_value = float(last_row["y"])
            start_ds = last_row["ds"]
            
            # 予測期間の日付インデックスを作成
            future_index = pd.date_range(
                start=start_ds, 
                periods=horizon + 1, 
                freq=inferred_freq
            )[1:] # start_ds (=historyの最後) は含めない
            
            # 簡易ロジック: 直近の変動を少し加味したランダムウォーク (モック用)
            # ※ 実際のChronos統合時はここを pipeline(context=group["y"]) に置き換える
            np.random.seed(42) # 再現性のため固定
            random_walk = np.random.normal(0, last_value * 0.01, size=horizon)
            predictions = last_value + np.cumsum(random_walk)
            
            for ts, pred in zip(future_index, predictions):
                forecasts.append({
                    "unique_id": uid, 
                    "ds": ts, 
                    self.name: float(pred), # モデル名 (Chronos) をカラム名にする
                    # 信頼区間 (モック)
                    f"{self.name}-lo-90": float(pred * 0.95),
                    f"{self.name}-hi-90": float(pred * 1.05)
                })

        yhat = pd.DataFrame(forecasts)
        
        return ForecastResult(
            yhat=yhat, 
            raw_output=None, 
            meta={
                "strategy": "mock_chronos_random_walk", 
                "model_id": self.model_id,
                "horizon": horizon
            }
        )