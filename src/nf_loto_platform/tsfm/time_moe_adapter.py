"""
Time-MoE (Time Series Foundation Models with Mixture of Experts) アダプタの実装。

論文: "Time-MoE: Billion-Scale Time Series Foundation Models with Mixture of Experts" (2024)
Hugging Face Transformers 形式、または専用ライブラリ形式のモデルをラップし、
NeuralForecast 互換の入出力インターフェースを提供する。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import torch

from nf_loto_platform.tsfm.base import BaseTSFMAdapter, TSFMCapabilities

# -----------------------------------------------------------------------------
# Optional Imports (ライブラリがインストールされていない場合の対策)
# -----------------------------------------------------------------------------
try:
    from transformers import AutoConfig, AutoModelForTimeSeriesForecasting
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False

# Time-MoE が独自のライブラリとして提供されている場合の想定
# from time_moe import TimeMoEForPrediction などの import をここに記述

logger = logging.getLogger(__name__)


class TimeMoEAdapter(BaseTSFMAdapter):
    """
    Time-MoE モデル用のアダプタクラス。
    
    Mixture of Experts アーキテクチャを活用し、スパースな活性化により
    大規模なパラメータ数（2.4Bなど）でも効率的な推論を行う。
    """

    def __init__(
        self,
        model_name: str = "Time-MoE-50M",
        context_length: Optional[int] = None,
        use_gpu: bool = False,
        rag_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        """
        Args:
            model_name: Hugging FaceのモデルID または ローカルパス
            context_length: 入力系列の最大長 (モデルの仕様に合わせる)
            use_gpu: GPUを利用するかどうか
            rag_context: RAGで検索された類似パターン情報 (現在はプロンプトとして未活用だがIFとして保持)
        """
        super().__init__(model_name, context_length, use_gpu, rag_context, **kwargs)
        
        if not TRANSFORMERS_AVAILABLE:
            logger.warning("transformers library is not installed. TimeMoEAdapter will run in mock mode or fail.")

        self.device = self._select_device(use_gpu)
        self.model = None
        self.config = None
        
        # モデルの遅延ロード (メモリ節約のため predict/fit 時にロード)
        # ここでは設定だけ確認
        self.hf_model_id = self._resolve_model_id(model_name)

    def _resolve_model_id(self, model_name: str) -> str:
        """UI表示名から実際のHuggingFace IDへのマッピング."""
        # 仮のマッピング（実際には公開されているIDを使用）
        mapping = {
            "Time-MoE-50M": "maple77/Time-MoE-50M",
            "Time-MoE-200M": "maple77/Time-MoE-200M",
            "Time-MoE-2.4B": "maple77/Time-MoE-2.4B",
        }
        return mapping.get(model_name, model_name)

    def _select_device(self, use_gpu: bool) -> torch.device:
        if use_gpu and torch.cuda.is_available():
            return torch.device("cuda")
        elif use_gpu and torch.backends.mps.is_available():
            return torch.device("mps")  # For Mac M-series
        return torch.device("cpu")

    def _load_model(self):
        """モデルをメモリにロードする."""
        if self.model is not None:
            return

        logger.info(f"Loading Time-MoE model from {self.hf_model_id} to {self.device}...")
        
        try:
            # Time-MoEの実装形態に依存するが、HF標準インターフェースを想定
            self.config = AutoConfig.from_pretrained(self.hf_model_id)
            
            # コンテキスト長のオーバーライドがあれば適用
            if self.context_length:
                # モデルによっては context_length, input_size, prediction_length など名称が異なる
                if hasattr(self.config, "context_length"):
                    self.config.context_length = self.context_length
            
            # モデルロード
            self.model = AutoModelForTimeSeriesForecasting.from_pretrained(
                self.hf_model_id,
                config=self.config,
                trust_remote_code=True, # 多くの最新研究モデルで必要
                torch_dtype=torch.bfloat16 if self.device.type == "cuda" else torch.float32
            )
            
            self.model.to(self.device)
            self.model.eval()
            
            # コンパイルによる高速化 (PyTorch 2.0+)
            if self.kwargs.get("compile", False) and hasattr(torch, "compile"):
                try:
                    logger.info("Compiling model with torch.compile()...")
                    self.model = torch.compile(self.model)
                except Exception as e:
                    logger.warning(f"Model compilation failed: {e}")

            logger.info("Model loaded successfully.")

        except Exception as e:
            logger.error(f"Failed to load Time-MoE model: {e}")
            # フォールバックやモック用の処理をここに追加することも可能
            raise e

    def fit(self, df: pd.DataFrame, **kwargs) -> 'TimeMoEAdapter':
        """
        Fine-tuning (Few-shot learning) を実行する。
        注意: 基盤モデルのフルパラメータ学習は重いため、ここではPEFT (LoRA) 等を想定するか
        あるいは単純な最終層のみの学習を行う。
        """
        # 今回のスコープでは Zero-shot を主とするため、ログ出力のみでパスする
        logger.info(f"Fit requested for {self.model_name}. Running in Zero-shot mode (skipping weight update).")
        
        # もしFine-tuningを実装する場合:
        # 1. データセットクラスの作成 (Sliding Window)
        # 2. Optimizerの設定
        # 3. Training Loop の実行
        
        return self

    def predict(
        self, 
        df: pd.DataFrame, 
        horizon: int, 
        confidence_level: Optional[float] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        予測を実行する。
        
        Args:
            df: 入力データフレーム (unique_id, ds, y)
            horizon: 予測期間
            confidence_level: (Time-MoEが確率的出力に対応している場合のみ有効)
        """
        self._load_model()
        df = self._preprocess(df)
        
        results = []
        unique_ids = df['unique_id'].unique()
        
        logger.info(f"Predicting {len(unique_ids)} series with horizon={horizon}...")
        
        for uid in unique_ids:
            group = df[df['unique_id'] == uid]
            
            # 1. データ準備
            # モデルの context_length に合わせて過去データを切り出す
            past_values = group['y'].values
            if len(past_values) > self.context_length:
                past_values = past_values[-self.context_length:]
            
            # Tensor化
            past_values_tensor = torch.tensor(past_values, dtype=torch.float32).unsqueeze(0).to(self.device)
            # (Batch, Time) -> (1, T)
            
            # 外生変数があればここで処理 (Time-MoEが対応していれば)
            # future_values = ...
            
            # 2. 推論
            with torch.no_grad():
                try:
                    # generate メソッドを持つ生成モデルの場合
                    if hasattr(self.model, "generate"):
                        outputs = self.model.generate(
                            inputs=past_values_tensor,
                            prediction_length=horizon,
                            num_return_sequences=1  # 決定論的予測
                        )
                        # output shape: (Batch, Samples, Horizon) or (Batch, Horizon)
                        
                        if outputs.dim() == 3:
                            forecasts = outputs.mean(dim=1) # サンプル平均
                        else:
                            forecasts = outputs
                            
                        forecast_np = forecasts.cpu().numpy()[0] # (Horizon,)
                    
                    # 標準的な forward メソッドの場合
                    else:
                        # ダミーの未来入力が必要な場合がある
                        outputs = self.model(past_values_tensor)
                        # モデル仕様に合わせて logits や prediction を取得
                        if hasattr(outputs, "logits"):
                            forecast_np = outputs.logits[:, -horizon:, :].mean(dim=-1).cpu().numpy()[0]
                        else:
                            # Fallback logic
                            raise NotImplementedError("Output parsing logic needed for this specific model architecture")

                except Exception as e:
                    logger.error(f"Prediction failed for {uid}: {e}")
                    # 失敗時は NaNs または 最後の値を埋める等のフォールバック
                    forecast_np = np.full(horizon, np.nan)

            # 3. 結果整形
            last_ds = group['ds'].iloc[-1]
            
            # 日付生成 (Pandas の freq 推定に依存、あるいは D をデフォルトに)
            freq = pd.infer_freq(group['ds']) or 'D'
            future_dates = pd.date_range(start=last_ds, periods=horizon + 1, freq=freq)[1:]
            
            res_df = pd.DataFrame({
                "unique_id": uid,
                "ds": future_dates,
                self.model_name: forecast_np
            })
            
            # 分位点予測ロジック (Time-MoEが確率分布を返す場合)
            if confidence_level:
                # ここでは簡易的に不確実性を付与する例（実際はモデルのquantile出力を使う）
                # Time-MoE が分布を出さない場合は空のまま、または conformal.py で後付けする
                pass

            results.append(res_df)
        
        # 全系列の結果を結合
        final_df = pd.concat(results, ignore_index=True)
        return final_df

    def supports(
        self,
        horizon: int,
        num_series: int,
        num_features: int,
    ) -> bool:
        """このアダプタがタスクをサポートしているか判定."""
        # Time-MoE は基本的に単変量(Univariate)モデルとして各系列に適用可能
        return True