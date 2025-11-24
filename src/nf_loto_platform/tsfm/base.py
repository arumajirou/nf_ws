"""
TSFM (Time Series Foundation Models) アダプタの基底クラス定義。

異なる基盤モデル（Time-MoE, Chronos, MOMENT, TimeGPTなど）を
同一のインターフェースで扱えるようにするための抽象化レイヤー。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TSFMCapabilities:
    """TSFM モデルの能力・制約に関するメタデータ."""
    
    model_name: str
    context_length: int = 512           # モデルが一度に読める最大系列長
    supports_exogenous: bool = False    # 外生変数をサポートするか
    supports_multivariate: bool = False # 多変量予測をサポートするか
    is_zero_shot: bool = True           # Zero-shot推論が可能か
    can_fine_tune: bool = False         # 追加学習(Fine-tuning)が可能か
    requires_gpu: bool = False          # GPU推奨/必須か


class BaseTSFMAdapter(ABC):
    """すべての TSFM アダプタが継承すべき基底クラス."""

    def __init__(
        self, 
        model_name: str,
        context_length: Optional[int] = None,
        use_gpu: bool = False,
        rag_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any
    ):
        """
        Args:
            model_name: モデルの識別子 (例: "Time-MoE-2.4B")
            context_length: 入力コンテキスト長
            use_gpu: GPUを使用するかどうか
            rag_context: RAGにより検索された類似パターン情報 (Optional)
        """
        self.model_name = model_name
        self.context_length = context_length or 512
        self.use_gpu = use_gpu
        self.rag_context = rag_context
        self.kwargs = kwargs
        
        # モデルのロード状態
        self._is_loaded = False
        
        logger.info(f"Initializing TSFM Adapter: {model_name} (GPU={use_gpu}, Context={self.context_length})")

    def fit(self, df: pd.DataFrame, **kwargs) -> 'BaseTSFMAdapter':
        """
        モデルの学習またはファインチューニングを行う。
        Zero-shotモデルの場合は、何もしないか、内部状態の更新のみを行う。

        Args:
            df: 学習用データ (columns: unique_id, ds, y, ...)
        
        Returns:
            self
        """
        # デフォルトはZero-shotとし、何もしない
        logger.debug(f"{self.model_name} is running in zero-shot mode (skipping fit).")
        return self

    @abstractmethod
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
            df: 直近の履歴データ (columns: unique_id, ds, y, ...)
            horizon: 予測期間 (h)
            confidence_level: 予測区間の信頼度 (例: 0.9). Noneの場合は点予測のみ。

        Returns:
            pd.DataFrame: 以下のカラムを含むデータフレーム
                - unique_id
                - ds
                - {model_name} (点予測値)
                - {model_name}-lo-{level} (オプション: 予測区間下限)
                - {model_name}-hi-{level} (オプション: 予測区間上限)
        """
        raise NotImplementedError

    def _preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        """共通の前処理 (必要に応じてオーバーライド)."""
        # 必須カラムのチェック
        required = {"unique_id", "ds", "y"}
        if not required.issubset(df.columns):
            raise ValueError(f"Input dataframe must contain {required} columns.")
        
        # dsのソート
        df = df.sort_values(["unique_id", "ds"]).reset_index(drop=True)
        return df

    def _format_output(
        self, 
        preds: pd.DataFrame, 
        model_col_name: str
    ) -> pd.DataFrame:
        """
        出力フォーマットをNeuralForecast標準に合わせるヘルパー。
        
        Args:
            preds: 予測結果DF (unique_id, ds, y_hat を含むと仮定)
            model_col_name: 出力カラム名 (例: "Time-MoE")
        """
        # カラム名のリネーム (y_hat -> model_name)
        if "y_hat" in preds.columns:
            preds = preds.rename(columns={"y_hat": model_col_name})
            
        return preds


class TSFMHub:
    """複数のTSFMアダプタを管理・供給するファクトリークラス (簡易版)."""
    
    _adapters: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str, adapter_cls: type):
        cls._adapters[name] = adapter_cls

    @classmethod
    def get_adapter_class(cls, name: str) -> type:
        if name not in cls._adapters:
            raise ValueError(f"Adapter '{name}' not registered. Available: {list(cls._adapters.keys())}")
        return cls._adapters[name]