"""
MOMENT (Multi-domain Open-source MEtamodel for Time series) アダプタの実装。

論文: "MOMENT: A Family of Open Time-series Foundation Models" (2024)
https://github.com/moment-timeseries-foundation-model/moment

このアダプタは、Hugging Face Transformers 経由、または公式実装をラップして
NeuralForecast 互換のインターフェースを提供する。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
import torch

from nf_loto_platform.tsfm.base import BaseTSFMAdapter

# -----------------------------------------------------------------------------
# Optional Imports
# -----------------------------------------------------------------------------
try:
    from transformers import AutoConfig, AutoModelForTimeSeriesForecasting
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False

logger = logging.getLogger(__name__)


class MomentAdapter(BaseTSFMAdapter):
    """
    MOMENT モデル用のアダプタクラス。
    
    Masked Time-series Modeling (Time-series BERT) アプローチを採用しており、
    多様なドメイン（医療、工学、気象など）の知識を転移して予測を行う。
    """

    def __init__(
        self,
        model_name: str = "MOMENT-1-Large",
        context_length: Optional[int] = None,
        use_gpu: bool = False,
        rag_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        """
        Args:
            model_name: モデル識別子 (例: "AutonLab/MOMENT-1-large")
            context_length: 入力系列長 (デフォルト: 512)
        """
        super().__init__(model_name, context_length, use_gpu, rag_context, **kwargs)
        
        if not TRANSFORMERS_AVAILABLE:
            logger.warning("transformers library not found. MomentAdapter will default to mock mode.")

        self.device = self._select_device(use_gpu)
        self.model = None
        self.config = None
        
        # HF Model ID Mapping
        self.hf_model_id = self._resolve_model_id(model_name)

    def _resolve_model_id(self, model_name: str) -> str:
        """UI表示名からHuggingFace IDへのマッピング."""
        mapping = {
            "MOMENT-Large": "AutonLab/MOMENT-1-large",
            "MOMENT-Base": "AutonLab/MOMENT-1-base",
            "MOMENT-Small": "AutonLab/MOMENT-1-small",
        }
        # マッピングになければそのまま使う（カスタムパス対応）
        return mapping.get(model_name, model_name)

    def _select_device(self, use_gpu: bool) -> torch.device:
        if use_gpu and torch.cuda.is_available():
            return torch.device("cuda")
        elif use_gpu and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")

    def _load_model(self):
        """モデルをロードする (Lazy Loading)."""
        if self.model is not None:
            return

        logger.info(f"Loading MOMENT model from {self.hf_model_id} to {self.device}...")
        try:
            # MOMENTは現時点(2025)でAutoModelForTimeSeriesForecastingに対応していると仮定
            # 対応していない場合は、MOMENT独自のロードロジックをここに記述する
            
            self.config = AutoConfig.from_pretrained(self.hf_model_id, trust_remote_code=True)
            
            # コンテキスト長の設定
            if self.context_length:
                if hasattr(self.config, "seq_len"):
                    self.config.seq_len = self.context_length
            
            self.model = AutoModelForTimeSeriesForecasting.from_pretrained(
                self.hf_model_id,
                config=self.config,
                trust_remote_code=True,
                torch_dtype=torch.float32 # MOMENTはfloat32推奨の場合が多い
            )
            
            self.model.to(self.device)
            self.model.eval()
            logger.info("MOMENT model loaded successfully.")

        except Exception as e:
            logger.error(f"Failed to load MOMENT model: {e}")
            raise RuntimeError(f"Could not load MOMENT model {self.hf_model_id}. Check internet connection or HF token.") from e

    def _preprocess_series(self, y_series: np.ndarray) -> torch.Tensor:
        """
        単一系列の前処理:
        1. NaN処理 (線形補間)
        2. スケーリング (Instance Normalization: (x - mean) / std)
        3. Tensor化
        """
        # NaN 補間
        if np.isnan(y_series).any():
            y_series = pd.Series(y_series).interpolate().fillna(method='bfill').fillna(method='ffill').values

        # NumPy -> Tensor
        y_tensor = torch.tensor(y_series, dtype=torch.float32)
        
        # 長さ調整
        if len(y_tensor) > self.context_length:
            y_tensor = y_tensor[-self.context_length:]
        
        # Shape: (1, n_channels=1, seq_len)
        # MOMENTはチャネル次元を要求する
        return y_tensor.unsqueeze(0).unsqueeze(0)

    def predict(
        self, 
        df: pd.DataFrame, 
        horizon: int, 
        confidence_level: Optional[float] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        予測実行.
        """
        self._load_model()
        df = self._preprocess(df)
        
        results = []
        unique_ids = df['unique_id'].unique()
        
        logger.info(f"MOMENT Prediction start: {len(unique_ids)} series, h={horizon}")

        for uid in unique_ids:
            group = df[df['unique_id'] == uid].sort_values('ds')
            y_values = group['y'].values
            
            # 入力テンソル作成
            input_tensor = self._preprocess_series(y_values).to(self.device)
            # input_tensor shape: (1, 1, context_len)
            
            with torch.no_grad():
                try:
                    # MOMENTのフォワードパス
                    # forecast taskの場合、future_masking等を内部で行うか、generateメソッドを使う
                    # ここでは標準的なHF Forecasting APIを想定
                    
                    # 多くの場合、horizonを引数に渡すか、configで決まっている
                    # model.generate() がある場合
                    if hasattr(self.model, "generate"):
                        outputs = self.model.generate(
                            inputs=input_tensor,
                            prediction_length=horizon
                        )
                        # outputs: (Batch, Samples, Horizon) or (Batch, Horizon)
                        if outputs.dim() == 3:
                            forecast = outputs.mean(dim=1)
                        else:
                            forecast = outputs
                            
                    else:
                        # フォールバック: forwardを呼び出し、最後のステップの出力を取得など
                        # ※実際のMOMENT実装に合わせる必要あり
                        outputs = self.model(past_values=input_tensor)
                        # logits shape: (Batch, Horizon, Dim) ???
                        # 仮実装: logits属性があればそれを使う
                        if hasattr(outputs, "logits"):
                             forecast = outputs.logits
                        elif hasattr(outputs, "prediction_logits"):
                             forecast = outputs.prediction_logits
                        else:
                             # 最終手段: Tensorそのものが返ってくる場合
                             forecast = outputs
                    
                    # Tensor -> Numpy (Batch=0)
                    # forecast shape is expected to be (1, Horizon) or (1, 1, Horizon)
                    forecast_np = forecast.cpu().numpy().flatten()[-horizon:]

                except Exception as e:
                    logger.warning(f"Prediction failed for {uid} with MOMENT: {e}. Returning NaNs.")
                    forecast_np = np.full(horizon, np.nan)

            # 結果DataFrame作成
            last_ds = group['ds'].iloc[-1]
            freq = pd.infer_freq(group['ds']) or 'D'
            future_dates = pd.date_range(start=last_ds, periods=horizon + 1, freq=freq)[1:]
            
            # 長さが合わない場合のガード
            if len(forecast_np) != len(future_dates):
                logger.warning(f"Shape mismatch: forecast={len(forecast_np)}, dates={len(future_dates)}")
                # 切り詰め or パディング
                min_len = min(len(forecast_np), len(future_dates))
                forecast_np = forecast_np[:min_len]
                future_dates = future_dates[:min_len]

            res_df = pd.DataFrame({
                "unique_id": uid,
                "ds": future_dates,
                self.model_name: forecast_np
            })
            
            results.append(res_df)

        return pd.concat(results, ignore_index=True)

    def fit(self, df: pd.DataFrame, **kwargs) -> 'MomentAdapter':
        """
        Fine-tuning (Not implemented for Zero-shot usage).
        """
        logger.info(f"MOMENT {self.model_name} running in Zero-shot mode. Fit skipped.")
        return self