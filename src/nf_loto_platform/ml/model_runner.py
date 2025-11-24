"""汎用的な時系列予測メトリクス群および実験実行ランナー.

基本指標:
- smape
- mape
- mae
- rmse

分位・確率的指標:
- pinball_loss
- coverage
- coverage_error

ビジネス・トレンド指標:
- directional_accuracy
- max_drawdown
- sharpe_ratio

実験ランナー:
- run_loto_experiment: データロード、学習、推論、評価を一括実行する
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

# NeuralForecast 関連のインポート
# 実行環境によってはインストールされていない可能性があるため、try-exceptで保護するか、
# 必須依存として扱う。ここでは必須とする。
from neuralforecast import NeuralForecast
from neuralforecast.models import AutoNHITS, AutoTFT, NBEATS, NHITS

# プロジェクト内モジュールのインポート
# 循環参照を避けるため、必要に応じて関数内でインポートする場合もあるが、
# ここではトップレベルで解決できる前提とする。
try:
    from nf_loto_platform.db import loto_repository
except ImportError:
    loto_repository = None  # テスト時などにモックされることを想定

try:
    from nf_loto_platform.ml.automodel_builder import build_automodel
except ImportError:
    build_automodel = None

logger = logging.getLogger(__name__)

ArrayLike = Sequence[float] | np.ndarray | Iterable[float]


# ---------------------------------------------------------------------------
# Metrics Functions (Existing)
# ---------------------------------------------------------------------------

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
    """目標とする被覆率との乖離 (符号付き) を計算する."""
    actual_cov = coverage(y, y_lower, y_upper)
    return float(actual_cov - target)


def directional_accuracy(y: ArrayLike, yhat: ArrayLike) -> float:
    """方向正解率 (Directional Accuracy) を計算する."""
    y_arr, yhat_arr = _to_numpy(y, yhat)
    
    if len(y_arr) < 2:
        return np.nan # データ不足
        
    # 階差 (t+1 - t) をとる
    diff_y = np.diff(y_arr)
    diff_yhat = np.diff(yhat_arr)
    
    matches = (np.sign(diff_y) == np.sign(diff_yhat))
    
    return float(np.mean(matches.astype(float)))


def max_drawdown(y: ArrayLike) -> float:
    """最大ドローダウン (Max Drawdown) を計算する."""
    arr = np.asarray(list(y), dtype=float)
    if len(arr) == 0:
        return 0.0
    
    running_max = np.maximum.accumulate(arr)
    valid_mask = running_max > 1e-9
    
    if not np.any(valid_mask):
        return 0.0
        
    drawdowns = np.zeros_like(arr)
    drawdowns[valid_mask] = (running_max[valid_mask] - arr[valid_mask]) / running_max[valid_mask]
    
    return float(np.max(drawdowns))


def sharpe_ratio(y: ArrayLike, risk_free_rate: float = 0.0) -> float:
    """(簡易版) シャープレシオを計算する."""
    arr = np.asarray(list(y), dtype=float)
    if len(arr) < 2:
        return 0.0
    
    with np.errstate(divide='ignore', invalid='ignore'):
        returns = np.diff(arr) / arr[:-1]
    
    returns = returns[np.isfinite(returns)]
    
    if len(returns) == 0:
        return 0.0
        
    mean_ret = np.mean(returns)
    std_ret = np.std(returns)
    
    if std_ret < 1e-9:
        return 0.0 # 変動なし
        
    return float((mean_ret - risk_free_rate) / std_ret)


# ---------------------------------------------------------------------------
# Experiment Runner & Types
# ---------------------------------------------------------------------------

@dataclass
class ExperimentResult:
    """実験実行結果を保持するデータクラス."""
    preds: pd.DataFrame
    meta: Dict[str, Any] = field(default_factory=dict)


def run_loto_experiment(
    table_name: str,
    loto: str,
    unique_ids: List[str],
    model_name: str = "AutoNHITS",
    backend: str = "optuna",
    horizon: int = 28,
    num_samples: int = 10,
    cpus: int = 1,
    gpus: int = 0,
    use_rag: bool = False,
    agent_metadata: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    時系列予測実験を実行するメインエントリーポイント.

    Args:
        table_name: DBテーブル名 (例: 'nf_loto_panel')
        loto: ロトの種類 (例: 'loto6')
        unique_ids: 対象IDリスト (例: ['loto6', 'mini_loto'])
        model_name: モデル名 (NeuralForecast AutoModels or TSFM)
        backend: 最適化バックエンド ('optuna', 'ray', 'tsfm', 'local')
        horizon: 予測期間
        num_samples: ハイパーパラメータ探索の試行回数
        cpus: CPU数
        gpus: GPU数
        use_rag: RAGを使用するかどうか
        agent_metadata: エージェントからのコンテキスト情報 (ログ用)
        **kwargs: その他のモデルパラメータ

    Returns:
        Tuple[pd.DataFrame, Dict[str, Any]]:
            - preds: 予測結果 (unique_id, ds, y, model_name...)
            - meta: 実験メタデータ (metrics, run_id, config等)
    """
    run_start_time = time.time()
    run_id = int(run_start_time * 1000)
    logger.info(f"Starting experiment run_id={run_id} for {unique_ids}")

    # 1. データロード
    # Repositoryからデータを取得する。
    # 実際のRepository実装に合わせて修正が必要だが、ここでは一般的なインターフェースを想定。
    if loto_repository is None:
        raise ImportError("loto_repository module is not available.")

    df = loto_repository.load_panel_data(table_name, loto, unique_ids)
    if df.empty:
        raise ValueError(f"No data found for {unique_ids} in {table_name}")

    # データセット分割 (Train/Test)
    # 単純に最後の horizon 期間をテストデータとする
    df_test = df.groupby("unique_id").tail(horizon).reset_index(drop=True)
    df_train = df.groupby("unique_id").apply(lambda x: x.iloc[:-horizon]).reset_index(drop=True)

    if df_train.empty or df_test.empty:
        raise ValueError("Data insufficient for the requested horizon.")

    # 2. モデル構築
    # TSFMバックエンドの場合とNeuralForecastの場合で分岐
    models = []
    
    if backend == "tsfm":
        # TSFM (Time Series Foundation Model) アダプタの使用
        # ※ automodel_builder 側で吸収するか、ここで直接呼ぶか設計次第だが、
        #    現状は未実装のため NotImplementedError になる可能性がある。
        logger.info(f"Using TSFM backend for model: {model_name}")
        # TODO: TSFM アダプタの実装と統合
        raise NotImplementedError("TSFM backend is not yet fully implemented.")
        
    else:
        # NeuralForecast AutoModel
        if build_automodel:
            # Builderがある場合はそれを使う
            model = build_automodel(
                model_name=model_name,
                backend=backend,
                num_samples=num_samples,
                cpus=cpus,
                gpus=gpus,
                **kwargs
            )
            models.append(model)
        else:
            # フォールバック: 直接クラスをインスタンス化 (AutoNHITSのみ対応の簡易版)
            logger.warning("automodel_builder not found. Falling back to default AutoNHITS.")
            models.append(AutoNHITS(h=horizon, config=None, num_samples=num_samples))

    # 3. 学習と予測
    nf = NeuralForecast(
        models=models,
        freq='D'  # ロトデータの頻度に合わせて調整が必要 (例: 'D', 'W')
    )
    
    logger.info("Fitting model...")
    nf.fit(df=df_train)
    
    logger.info("Predicting...")
    preds = nf.predict()
    
    # 予測結果に実測値 (y) を結合して評価しやすくする
    preds = preds.reset_index()
    # df_test と結合 (unique_id, ds をキーに)
    # Note: predsのds型とdf_testのds型が一致していること前提
    preds = preds.merge(df_test[["unique_id", "ds", "y"]], on=["unique_id", "ds"], how="left")

    # 4. 評価メトリクスの計算
    # モデル名は preds のカラムから特定 (unique_id, ds, y 以外)
    metric_results = {}
    model_col = model_name  # 基本的にモデル名がカラム名になる
    
    if model_col in preds.columns and "y" in preds.columns:
        # NaNを除外して計算
        valid_preds = preds.dropna(subset=["y", model_col])
        if not valid_preds.empty:
            y_true = valid_preds["y"].values
            y_hat = valid_preds[model_col].values
            
            metric_results = {
                "mae": mae(y_true, y_hat),
                "rmse": rmse(y_true, y_hat),
                "smape": smape(y_true, y_hat),
                "mape": mape(y_true, y_hat),
            }
            
            # 分位予測がある場合の評価 (例: -lo-90, -hi-90)
            # ここでは簡易的なチェックのみ
            cols = valid_preds.columns
            if f"{model_col}-lo-90" in cols and f"{model_col}-hi-90" in cols:
                metric_results["coverage_90"] = coverage(
                    y_true, 
                    valid_preds[f"{model_col}-lo-90"].values,
                    valid_preds[f"{model_col}-hi-90"].values
                )

    logger.info(f"Experiment finished. Metrics: {metric_results}")

    # 5. メタデータの構築
    duration = time.time() - run_start_time
    meta = {
        "run_id": run_id,
        "model_name": model_name,
        "backend": backend,
        "duration_seconds": duration,
        "metrics": metric_results,  # 必須: ここに計算結果を格納
        "status": "success",
        "agent_metadata": agent_metadata or {},
        "params": kwargs,
        # 必要に応じてRAG情報なども追加
        "rag_metadata": None
    }

    # TODO: DBへの実験結果保存 (nf_model_runsテーブル等)
    # save_run_to_db(meta, ...)

    return preds, meta