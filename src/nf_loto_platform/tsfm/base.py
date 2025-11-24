"""
TSFM (Time Series Foundation Models) アダプタの基底クラス定義。

異なる基盤モデル（Time-MoE, Chronos, MOMENT, TimeGPTなど）を
同一のインターフェースで扱えるようにするための抽象化レイヤー。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union, Mapping

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TSFMCapabilities:
    """TSFM モデルの能力・制約に関するメタデータ."""
    
    provider: str                       # プロバイダ (例: "amazon", "google")
    model_id: str                       # モデル識別子
    task_types: List[str]               # 対応タスク (forecasting, embedding, etc.)
    input_arity: str = "univariate"     # univariate, multivariate, both
    context_length: int = 512           # モデルが一度に読める最大系列長
    max_horizon: Optional[int] = None   # 最大予測期間
    supports_exogenous: bool = False    # 外生変数をサポートするか
    is_zero_shot: bool = True           # Zero-shot推論が可能か
    finetuneable: bool = False          # 追加学習(Fine-tuning)が可能か
    max_context_length: int = 512       # context_lengthのエイリアス（互換性のため）
    license: str = "unknown"
    commercial_allowed: bool = False
    hardware_pref: str = "cpu"          # cpu, gpu-recommended, gpu-required


@dataclass
class ForecastResult:
    """予測結果を保持するコンテナ."""
    yhat: pd.DataFrame                  # 予測結果 (unique_id, ds, {model_name}, ...)
    raw_output: Optional[Any] = None    # モデル固有の生の出力 (np.arrayなど)
    meta: Dict[str, Any] = field(default_factory=dict) # 推論にかかった時間やトークン数などのメタデータ


class BaseTSFMAdapter(ABC):
    """すべての TSFM アダプタが継承すべき基底クラス."""

    def __init__(
        self, 
        name: str,
        capabilities: TSFMCapabilities,
        **kwargs: Any
    ):
        """
        Args:
            name: アダプタの表示名 (例: "Chronos", "Time-MoE")
            capabilities: モデルの能力定義
            **kwargs: その他、初期化時に保存しておきたいパラメータ
        """
        self.name = name
        self.capabilities = capabilities
        self.kwargs = kwargs
        
        # モデルのロード状態
        self._is_loaded = False
        
        logger.info(f"Initializing TSFM Adapter: {name} ({capabilities.model_id})")

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
        if self.capabilities.is_zero_shot and not self.capabilities.finetuneable:
            logger.debug(f"{self.name} is running in zero-shot mode (skipping fit).")
        else:
            logger.warning(f"{self.name} fit method is not implemented or not supported.")
        return self

    @abstractmethod
    def predict(
        self, 
        history: pd.DataFrame, 
        horizon: int, 
        freq: str | None = None,
        exogenous: Mapping[str, pd.DataFrame] | None = None,
        **kwargs
    ) -> ForecastResult:
        """
        予測を実行する。

        Args:
            history: 直近の履歴データ (columns: unique_id, ds, y, ...)
            horizon: 予測期間 (h)
            freq: データの頻度 (例: "D", "H")。Noneの場合は推論を試みる。
            exogenous: 外生変数 (オプション)

        Returns:
            ForecastResult: 予測結果とメタデータ
        """
        raise NotImplementedError

    def validate_input(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        共通の入力検証と前処理を行う。
        
        - 必須カラム (unique_id, ds, y) の存在確認
        - ds カラムの datetime 型への変換
        - データのソート
        """
        df = df.copy()
        
        # 必須カラムのチェック
        required = {"unique_id", "ds", "y"}
        if not required.issubset(df.columns):
            raise ValueError(f"Input dataframe must contain {required} columns. Found: {df.columns.tolist()}")
        
        # dsの型変換
        if not pd.api.types.is_datetime64_any_dtype(df["ds"]):
            try:
                df["ds"] = pd.to_datetime(df["ds"])
            except Exception as e:
                raise ValueError(f"Failed to convert 'ds' column to datetime: {e}")

        # yの型変換 (数値型であることを保証)
        df["y"] = pd.to_numeric(df["y"], errors='coerce')
        if df["y"].isnull().any():
            logger.warning("Found NaN values in 'y' column. Drop or fill them before passing to models.")
        
        # ソート
        df = df.sort_values(["unique_id", "ds"]).reset_index(drop=True)
        
        return df

    def format_output(
        self, 
        preds: pd.DataFrame, 
        target_col: str = "yhat"
    ) -> pd.DataFrame:
        """
        出力フォーマットを整形するヘルパー。
        
        Args:
            preds: 予測結果DF
            target_col: 予測値が入っているカラム名 (デフォルトはモデル名にリネームされる)
        """
        # カラム名のリネーム (target_col -> self.name)
        # これにより、NeuralForecastなどの他のツールと結合した際にモデル名で区別できる
        if target_col in preds.columns and target_col != self.name:
            preds = preds.rename(columns={target_col: self.name})
            
        return preds


class TSFMHub:
    """複数のTSFMアダプタを管理・供給するファクトリークラス (簡易版)."""
    
    _adapters: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str, adapter_cls: type):
        """アダプタクラスを登録する."""
        cls._adapters[name] = adapter_cls
        logger.info(f"Registered TSFM adapter: {name}")

    @classmethod
    def get_adapter(cls, name: str, **kwargs) -> BaseTSFMAdapter:
        """登録されたアダプタのインスタンスを生成して返す."""
        if name not in cls._adapters:
            available = list(cls._adapters.keys())
            raise ValueError(f"Adapter '{name}' not registered. Available: {available}")
        
        adapter_cls = cls._adapters[name]
        # ここではモデルIDなどはデフォルトまたはkwargsで指定されることを想定
        return adapter_cls(**kwargs)

    @classmethod
    def list_adapters(cls) -> List[str]:
        return list(cls._adapters.keys())