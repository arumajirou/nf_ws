"""汎用的な時系列予測メトリクス群.

基本指標:
- smape
- mape
- mae
- rmse

分位・確率的指標:
- pinball_loss
- coverage
- coverage_error (New)

ビジネス・トレンド指標:
- directional_accuracy (New)
- max_drawdown (New)
- sharpe_ratio (New)

実運用では NeuralForecast 側の実装や既存の分析基盤と整合を取る必要があるが、
ここではテストしやすい最小限の純粋関数として定義する。
"""

from __future__ import annotations

from typing import Iterable, Sequence

import math

import numpy as np


ArrayLike = Sequence[float] | np.ndarray | Iterable[float]


def _to_numpy(y: ArrayLike, yhat: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
    y_arr = np.asarray(list(y), dtype=float)
    yhat_arr = np.asarray(list(yhat), dtype=float)
    if y_arr.shape != yhat_arr.shape:
        raise ValueError(f"shape mismatch: y={y_arr.shape}, yhat={yhat_arr.shape}")
    return y_arr, yhat_arr


def smape(y: ArrayLike, yhat: ArrayLike, eps: float = 1e-8) -> float:
    """対称 MAPE (sMAPE) を計算する.

    定義:
        200 / N * Σ |ŷ_t - y_t| / (|y_t| + |ŷ_t| + eps)
    """
    y_arr, yhat_arr = _to_numpy(y, yhat)
    num = np.abs(yhat_arr - y_arr)
    denom = np.abs(y_arr) + np.abs(yhat_arr) + eps
    return float(200.0 * np.mean(num / denom))


def mape(y: ArrayLike, yhat: ArrayLike, eps: float = 1e-8) -> float:
    """Mean Absolute Percentage Error (百分率)."""
    y_arr, yhat_arr = _to_numpy(y, yhat)
    num = np.abs(yhat_arr - y_arr)
    denom = np.maximum(np.abs(y_arr), eps)
    return float(100.0 * np.mean(num / denom))


def mae(y: ArrayLike, yhat: ArrayLike) -> float:
    """Mean Absolute Error."""
    y_arr, yhat_arr = _to_numpy(y, yhat)
    return float(np.mean(np.abs(yhat_arr - y_arr)))


def rmse(y: ArrayLike, yhat: ArrayLike) -> float:
    """Root Mean Squared Error."""
    y_arr, yhat_arr = _to_numpy(y, yhat)
    return float(math.sqrt(np.mean((yhat_arr - y_arr) ** 2)))


def pinball_loss(y: ArrayLike, yhat: ArrayLike, q: float) -> float:
    """分位 q に対する pinball loss を計算する.

    0 < q < 1 を想定。
    """
    if not (0.0 < q < 1.0):
        raise ValueError("q must be in (0, 1)")
    y_arr, yhat_arr = _to_numpy(y, yhat)
    # NOTE: y < y_hat のケースで q を大きくすると loss も増える挙動
    diff = yhat_arr - y_arr
    return float(np.mean(np.maximum(q * diff, (q - 1.0) * diff)))


def coverage(y: ArrayLike, y_lower: ArrayLike, y_upper: ArrayLike) -> float:
    """予測区間 [y_lower, y_upper] に対する被覆率 (coverage) を計算する."""
    y_arr, lower = _to_numpy(y, y_lower)
    y_arr2, upper = _to_numpy(y, y_upper)
    # 上で shape チェック済み
    _ = y_arr2
    inside = (y_arr >= lower) & (y_arr <= upper)
    return float(np.mean(inside.astype(float)))


def coverage_error(y: ArrayLike, y_lower: ArrayLike, y_upper: ArrayLike, target: float = 0.9) -> float:
    """目標とする被覆率との乖離 (符号付き) を計算する.
    
    Returns:
        float: (実際のCoverage - 目標Coverage)
               負の値: 過小評価 (区間が狭すぎる/リスク過小)
               正の値: 過大評価 (区間が広すぎる/無駄が多い)
    """
    actual_cov = coverage(y, y_lower, y_upper)
    return float(actual_cov - target)


def directional_accuracy(y: ArrayLike, yhat: ArrayLike) -> float:
    """方向正解率 (Directional Accuracy) を計算する.
    
    時系列の1期先への変化方向(上がり/下がり)が一致している割合。
    
    定義:
        mean( sign(y_{t+1} - y_t) == sign(ŷ_{t+1} - ŷ_t) )
    """
    y_arr, yhat_arr = _to_numpy(y, yhat)
    
    if len(y_arr) < 2:
        return np.nan # データ不足
        
    # 階差 (t+1 - t) をとる
    diff_y = np.diff(y_arr)
    diff_yhat = np.diff(yhat_arr)
    
    # 符号の一致を確認
    # signが一致するかどうか。0 (変化なし) の扱いについては、
    # 両方0ならTrue、片方だけ0ならFalseとなる np.sign の比較を採用。
    matches = (np.sign(diff_y) == np.sign(diff_yhat))
    
    return float(np.mean(matches.astype(float)))


def max_drawdown(y: ArrayLike) -> float:
    """最大ドローダウン (Max Drawdown) を計算する.
    
    系列内での「最大値からの最大下落率」。
    予測された軌道が大きな下落リスクを含んでいるかの評価などに使用。
    y は価格や資産価値のような「レベル」を表す系列を想定。
    """
    arr = np.asarray(list(y), dtype=float)
    if len(arr) == 0:
        return 0.0
    
    # 累積最大値 (Running Max)
    # ex: [100, 105, 102, 110, 90] -> [100, 105, 105, 110, 110]
    running_max = np.maximum.accumulate(arr)
    
    # ゼロ除算回避: running_max が 0 以下の区間は計算対象外とする
    valid_mask = running_max > 1e-9
    
    if not np.any(valid_mask):
        return 0.0
        
    # ドローダウン率 = (累積最大 - 現在値) / 累積最大
    drawdowns = np.zeros_like(arr)
    drawdowns[valid_mask] = (running_max[valid_mask] - arr[valid_mask]) / running_max[valid_mask]
    
    return float(np.max(drawdowns))


def sharpe_ratio(y: ArrayLike, risk_free_rate: float = 0.0) -> float:
    """(簡易版) シャープレシオを計算する.
    
    定義: 平均リターン / リターンの標準偏差
    y は「価格」や「累積値」の系列を想定し、内部で変化率(リターン)に変換して計算する。
    """
    arr = np.asarray(list(y), dtype=float)
    if len(arr) < 2:
        return 0.0
    
    # 変化率 (Returns)
    with np.errstate(divide='ignore', invalid='ignore'):
        returns = np.diff(arr) / arr[:-1]
    
    # 無効値(inf, nan)を除去
    returns = returns[np.isfinite(returns)]
    
    if len(returns) == 0:
        return 0.0
        
    mean_ret = np.mean(returns)
    std_ret = np.std(returns)
    
    if std_ret < 1e-9:
        return 0.0 # 変動なし
        
    return float((mean_ret - risk_free_rate) / std_ret)