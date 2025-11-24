"""
Conformal Prediction (適合予測) モジュール。

時系列予測モデルの不確実性を定量化し、指定された信頼度（Coverage Level）を
満たすような予測区間を計算・補正する機能を提供する。

主な手法:
- Standard Residual: 点予測に対する絶対残差に基づく固定幅の区間推定
- CQR (Conformalized Quantile Regression): 分位点回帰の出力を補正する手法
- Adaptive Residual: 直近の変動(Volatility)に応じて区間幅を動的に調整する手法
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Literal, Optional, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PredictionIntervals:
    """予測区間の計算結果を保持するコンテナ."""
    
    y_hat: np.ndarray           # 点予測 (または中央値)
    y_lower: np.ndarray         # 予測区間下限
    y_upper: np.ndarray         # 予測区間上限
    method: str                 # 使用した手法
    confidence_level: float     # 設定された信頼度 (例: 0.9)
    q_val: float                # 計算された補正値 (non-conformity scoreの分位点)
    
    def to_dataframe(self, unique_ids: Optional[np.ndarray] = None, ds: Optional[np.ndarray] = None) -> pd.DataFrame:
        """結果をDataFrame形式に変換する."""
        df = pd.DataFrame({
            "y_hat": self.y_hat,
            "y_lower": self.y_lower,
            "y_upper": self.y_upper,
        })
        if unique_ids is not None:
            df.insert(0, "unique_id", unique_ids)
        if ds is not None:
            df.insert(1, "ds", ds)
        return df


class BaseConformalPredictor:
    """Conformal Predictorの基底クラス."""

    def __init__(self, alpha: float = 0.1):
        """
        Args:
            alpha: 許容誤差率 (Significance level). 
                   信頼区間 = 1 - alpha. 例: alpha=0.1 なら 90% 信頼区間.
        """
        if not (0 < alpha < 1):
            raise ValueError(f"alpha must be between 0 and 1, got {alpha}")
        self.alpha = alpha
        self.q_val: Optional[float] = None  # Calibrationにより決定される補正値

    def _calculate_quantile(self, scores: np.ndarray) -> float:
        """Non-conformity scores から (1-alpha) 分位点を計算する (有限標本補正付き)."""
        n = len(scores)
        if n == 0:
            return float("inf")
        
        # Finite sample correction: (1 - alpha) * (1 + 1/n)
        # ただし np.quantile は 0~1 の範囲を要求するため、
        # index ベースで計算する: ceil((n+1)(1-alpha)) / n
        
        q_level = np.clip((1.0 - self.alpha) * (1.0 + 1.0 / n), 0.0, 1.0)
        return float(np.quantile(scores, q_level, method="higher"))

    def calibrate(self, y_true: np.ndarray, y_pred: Union[np.ndarray, Tuple[np.ndarray, ...]], **kwargs) -> None:
        """キャリブレーションデータを用いて q_val を決定する (抽象メソッド)."""
        raise NotImplementedError

    def predict(self, y_pred: Union[np.ndarray, Tuple[np.ndarray, ...]], **kwargs) -> PredictionIntervals:
        """新しいデータに対する予測区間を計算する (抽象メソッド)."""
        raise NotImplementedError

    @staticmethod
    def evaluate_coverage(y_true: np.ndarray, y_lower: np.ndarray, y_upper: np.ndarray) -> Dict[str, float]:
        """実測カバレッジと平均区間幅を評価する."""
        covered = (y_true >= y_lower) & (y_true <= y_upper)
        coverage_rate = np.mean(covered)
        mean_width = np.mean(y_upper - y_lower)
        
        return {
            "coverage_rate": float(coverage_rate),
            "mean_width": float(mean_width),
            "covered_count": int(np.sum(covered)),
            "total_count": len(y_true)
        }


class ResidualConformalPredictor(BaseConformalPredictor):
    """
    絶対残差に基づく標準的なConformal Prediction.
    点予測モデルに対して一定幅の区間を付与する。
    """

    def calibrate(self, y_true: np.ndarray, y_pred: np.ndarray, **kwargs) -> None:
        """
        Args:
            y_true: 正解値配列
            y_pred: 点予測値配列
        """
        # Non-conformity score: 絶対誤差 |y - y_hat|
        scores = np.abs(y_true - y_pred)
        self.q_val = self._calculate_quantile(scores)
        logger.info(f"Calibrated Residual CP: q_val (interval half-width) = {self.q_val:.4f}")

    def predict(self, y_pred: np.ndarray, **kwargs) -> PredictionIntervals:
        if self.q_val is None:
            raise RuntimeError("Predictor is not calibrated. Call calibrate() first.")
        
        y_lower = y_pred - self.q_val
        y_upper = y_pred + self.q_val
        
        return PredictionIntervals(
            y_hat=y_pred,
            y_lower=y_lower,
            y_upper=y_upper,
            method="ResidualCP",
            confidence_level=1.0 - self.alpha,
            q_val=self.q_val
        )


class CQRConformalPredictor(BaseConformalPredictor):
    """
    Conformalized Quantile Regression (CQR).
    
    既存の分位点予測（Lower/Upper）をベースに、
    キャリブレーションデータでのカバー率不足分を補正する。
    """

    def calibrate(
        self, 
        y_true: np.ndarray, 
        y_pred_interval: Tuple[np.ndarray, np.ndarray],  # (lower, upper)
        **kwargs
    ) -> None:
        """
        Args:
            y_true: 正解値
            y_pred_interval: (y_lower_hat, y_upper_hat) のタプル。
                             モデルが出力した生の分位点予測。
        """
        y_lower_hat, y_upper_hat = y_pred_interval
        
        # Non-conformity score for CQR:
        # E_i = max(y_lower_hat - y_i, y_i - y_upper_hat)
        # もし y_i が区間内なら E_i は負、区間外なら正（不足距離）となる。
        
        scores = np.maximum(y_lower_hat - y_true, y_true - y_upper_hat)
        self.q_val = self._calculate_quantile(scores)
        logger.info(f"Calibrated CQR: q_val (correction term) = {self.q_val:.4f}")

    def predict(
        self, 
        y_pred_interval: Tuple[np.ndarray, np.ndarray, np.ndarray], # (y_hat, lower, upper)
        **kwargs
    ) -> PredictionIntervals:
        """
        Args:
            y_pred_interval: (y_hat, y_lower_hat, y_upper_hat) のタプル
        """
        y_hat, y_lower_hat, y_upper_hat = y_pred_interval
        
        if self.q_val is None:
            raise RuntimeError("Predictor is not calibrated.")

        # 補正を適用 (区間を広げる、あるいは狭める)
        y_lower_corrected = y_lower_hat - self.q_val
        y_upper_corrected = y_upper_hat + self.q_val
        
        return PredictionIntervals(
            y_hat=y_hat,
            y_lower=y_lower_corrected,
            y_upper=y_upper_corrected,
            method="CQR",
            confidence_level=1.0 - self.alpha,
            q_val=self.q_val
        )


class AdaptiveConformalPredictor(BaseConformalPredictor):
    """
    Adaptive (Locally Weighted) Conformal Prediction.
    
    時系列データのボラティリティ（分散）に応じて区間幅をスケーリングする。
    Score = |y - y_hat| / sigma_hat
    Interval = y_hat ± (Score_quantile * sigma_hat)
    """

    def calibrate(
        self, 
        y_true: np.ndarray, 
        y_pred: np.ndarray, 
        sigma_hat: np.ndarray,
        **kwargs
    ) -> None:
        """
        Args:
            y_true: 正解値
            y_pred: 点予測
            sigma_hat: 推定された局所的な標準偏差や不確実性指標 (例: 残差の移動標準偏差)
        """
        # 0除算防止
        safe_sigma = np.clip(sigma_hat, 1e-6, None)
        
        # Scaled non-conformity score
        scores = np.abs(y_true - y_pred) / safe_sigma
        self.q_val = self._calculate_quantile(scores)
        logger.info(f"Calibrated Adaptive CP: q_val (multiplier) = {self.q_val:.4f}")

    def predict(
        self, 
        y_pred: np.ndarray, 
        sigma_hat: np.ndarray,
        **kwargs
    ) -> PredictionIntervals:
        if self.q_val is None:
            raise RuntimeError("Predictor is not calibrated.")

        safe_sigma = np.clip(sigma_hat, 1e-6, None)
        
        width = self.q_val * safe_sigma
        y_lower = y_pred - width
        y_upper = y_pred + width
        
        return PredictionIntervals(
            y_hat=y_pred,
            y_lower=y_lower,
            y_upper=y_upper,
            method="AdaptiveCP",
            confidence_level=1.0 - self.alpha,
            q_val=self.q_val
        )


def get_conformal_predictor(method: str = "residual", alpha: float = 0.1) -> BaseConformalPredictor:
    """
    Conformal Predictor のファクトリー関数.
    
    Args:
        method: "residual", "cqr", "adaptive"
        alpha: Significance level (default: 0.1 for 90% coverage)
    """
    method = method.lower()
    if method == "residual":
        return ResidualConformalPredictor(alpha=alpha)
    elif method == "cqr":
        return CQRConformalPredictor(alpha=alpha)
    elif method == "adaptive":
        return AdaptiveConformalPredictor(alpha=alpha)
    else:
        raise ValueError(f"Unknown conformal method: {method}. Choose from ['residual', 'cqr', 'adaptive']")