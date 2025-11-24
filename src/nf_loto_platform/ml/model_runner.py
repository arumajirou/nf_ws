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
- run_loto_experiment: データロード、学習、推論、評価を一括実行し、DBへログ保存する
"""

from __future__ import annotations

import logging
import math
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

# ロギング設定
logger = logging.getLogger(__name__)

# リソース監視用 (インストールされていない場合はNoneとして扱う)
try:
    import psutil
except ImportError:
    psutil = None

# NeuralForecast 関連のインポート
from neuralforecast import NeuralForecast
from neuralforecast.models import AutoNHITS, AutoTFT, NBEATS, NHITS

# プロジェクト内モジュールのインポート
# ---------------------------------------------------------------------------
# 厳格なインポートエラーハンドリング (Strict Import Handling)
# ---------------------------------------------------------------------------
try:
    from nf_loto_platform.db import loto_repository
except ImportError as e:
    logger.critical("❌ CRITICAL ERROR: Required module 'loto_repository' not found.")
    raise ImportError(
        "Failed to import 'nf_loto_platform.db.loto_repository'. System cannot start without data layer."
    ) from e

try:
    from nf_loto_platform.ml.automodel_builder import build_automodel
except ImportError:
    logger.warning("⚠️ 'automodel_builder' not found. AutoNHITS fallback will be used.")
    build_automodel = None

# TSFM (Time Series Foundation Models) 関連のインポート
try:
    from nf_loto_platform.tsfm.registry import get_adapter
    TSFM_AVAILABLE = True
except ImportError:
    logger.warning("⚠️ 'nf_loto_platform.tsfm' not found. TSFM backend will be disabled.")
    TSFM_AVAILABLE = False
    get_adapter = None

# DBロガーのインポート（実験記録用）
try:
    from nf_loto_platform.logging_ext.db_logger import (
        log_run_start,
        log_run_end,
        log_run_error
    )
    DB_LOGGING_AVAILABLE = True
except ImportError:
    logger.warning("⚠️ 'db_logger' not found. Experiment runs will NOT be persisted to DB.")
    DB_LOGGING_AVAILABLE = False
    # ダミー関数
    def log_run_start(*args, **kwargs): return int(time.time() * 1000)
    def log_run_end(*args, **kwargs): pass
    def log_run_error(*args, **kwargs): pass


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
    """対称 MAPE (sMAPE) を計算する."""
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
    """分位 q に対する pinball loss を計算する."""
    if not (0.0 < q < 1.0):
        raise ValueError("q must be in (0, 1)")
    y_arr, yhat_arr = _to_numpy(y, yhat)
    diff = yhat_arr - y_arr
    return float(np.mean(np.maximum(q * diff, (q - 1.0) * diff)))


def coverage(y: ArrayLike, y_lower: ArrayLike, y_upper: ArrayLike) -> float:
    """予測区間 [y_lower, y_upper] に対する被覆率 (coverage) を計算する."""
    y_arr, lower = _to_numpy(y, y_lower)
    y_arr2, upper = _to_numpy(y, y_upper)
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
        return np.nan 
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
        return 0.0
    return float((mean_ret - risk_free_rate) / std_ret)


# ---------------------------------------------------------------------------
# Experiment Runner & Types
# ---------------------------------------------------------------------------

@dataclass
class ExperimentResult:
    """実験実行結果を保持するデータクラス."""
    preds: pd.DataFrame
    meta: Dict[str, Any] = field(default_factory=dict)


def _get_resource_snapshot() -> Dict[str, float]:
    """現在のシステムリソース使用状況を取得する."""
    if psutil is None:
        return {}
    try:
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": psutil.virtual_memory().percent,
        }
    except Exception:
        return {}


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
    時系列予測実験を実行し、結果をDBに保存する.

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
    start_time = time.time()
    
    # 1. DBログ: 実験開始 (RUNNING状態)
    resource_start = _get_resource_snapshot()
    loss_name = kwargs.get("loss", "default")
    metric_name = "mae" 

    run_id = log_run_start(
        table_name=table_name,
        loto=loto,
        unique_ids=unique_ids,
        model_name=model_name,
        backend=backend,
        horizon=horizon,
        loss=loss_name,
        metric=metric_name,
        optimization_config={
            "num_samples": num_samples,
            "cpus": cpus,
            "gpus": gpus,
            "use_rag": use_rag
        },
        search_space=kwargs,
        resource_snapshot=resource_start,
        system_info=agent_metadata
    )
    
    logger.info(f"Starting experiment run_id={run_id} for {unique_ids}")

    try:
        # 2. データロード
        if loto_repository is None:
            raise ImportError("loto_repository module is not properly initialized.")

        df = loto_repository.load_panel_data(table_name, loto, unique_ids)
        if df.empty:
            raise ValueError(f"No data found for {unique_ids} in {table_name}")

        # データセット分割 (Train/Test)
        df_test = df.groupby("unique_id").tail(horizon).reset_index(drop=True)
        df_train = df.groupby("unique_id").apply(lambda x: x.iloc[:-horizon]).reset_index(drop=True)

        if df_train.empty or df_test.empty:
            raise ValueError("Data insufficient for the requested horizon.")

        # 3. モデル構築と予測
        models_info = [] # ログ用モデル情報

        if backend == "tsfm":
            # =================================================================
            # TSFM (Time Series Foundation Model) Backend
            # =================================================================
            if not TSFM_AVAILABLE or get_adapter is None:
                raise ImportError("TSFM backend requested but 'nf_loto_platform.tsfm' is not available.")
            
            logger.info(f"Using TSFM backend adapter for: {model_name}")
            
            # アダプタの取得
            try:
                adapter = get_adapter(model_name)
            except ValueError as e:
                raise ValueError(f"TSFM model '{model_name}' not found in registry.") from e
            
            models_info.append(str(adapter))

            # 推論実行 (Zero-shot or Fine-tune)
            # BaseTSFMAdapter.fit は通常Zero-shotでは何もしない
            adapter.fit(df_train, **kwargs)
            
            # 予測
            forecast_result = adapter.predict(
                history=df_train,
                horizon=horizon,
                freq='D', # ロトデータは日次とみなすか、引数で受け取るか
                **kwargs
            )
            
            preds = forecast_result.yhat
            
            # カラム名の整合性を確保 (NeuralForecastとの互換性のため)
            # TSFMアダプタが model_name をカラム名にしていない場合のフォールバック
            if model_name not in preds.columns:
                # "yhat" や "mean" などの数値カラムを探す
                numeric_cols = preds.select_dtypes(include=[np.number]).columns
                exclude = {"ds", "unique_id", "y"}
                candidates = [c for c in numeric_cols if c not in exclude]
                if candidates:
                    preds = preds.rename(columns={candidates[0]: model_name})
        
        else:
            # =================================================================
            # NeuralForecast Backend
            # =================================================================
            models = []
            if build_automodel:
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
                logger.warning("automodel_builder not found. Falling back to default AutoNHITS.")
                models.append(AutoNHITS(h=horizon, config=None, num_samples=num_samples))
            
            models_info = [str(m) for m in models]

            # 学習と予測
            nf = NeuralForecast(
                models=models,
                freq='D'
            )
            
            logger.info("Fitting model...")
            nf.fit(df=df_train)
            
            logger.info("Predicting...")
            preds = nf.predict()
            preds = preds.reset_index()

        # 4. 結果の統合 (Testデータとの結合)
        # preds は [unique_id, ds, model_name] を持っている前提
        preds = preds.merge(df_test[["unique_id", "ds", "y"]], on=["unique_id", "ds"], how="left")

        # 5. 評価メトリクスの計算
        metric_results = {}
        model_col = model_name
        
        if model_col in preds.columns and "y" in preds.columns:
            valid_preds = preds.dropna(subset=["y", model_col])
            if not valid_preds.empty:
                y_true = valid_preds["y"].values
                y_hat = valid_preds[model_col].values
                
                metric_results = {
                    "mae": mae(y_true, y_hat),
                    "rmse": rmse(y_true, y_hat),
                    "smape": smape(y_true, y_hat),
                    "mape": mape(y_true, y_hat),
                    "directional_accuracy": directional_accuracy(y_true, y_hat),
                    "max_drawdown": max_drawdown(y_hat)
                }
                
                # 分位予測の評価 (例: 90%区間)
                if f"{model_col}-lo-90" in valid_preds.columns and f"{model_col}-hi-90" in valid_preds.columns:
                    metric_results["coverage_90"] = coverage(
                        y_true, 
                        valid_preds[f"{model_col}-lo-90"].values,
                        valid_preds[f"{model_col}-hi-90"].values
                    )

        logger.info(f"Experiment finished. Metrics: {metric_results}")

        # 6. DBログ: 正常終了 (SUCCESS状態)
        resource_end = _get_resource_snapshot()
        best_params = {} 
        # 必要に応じて adapter.kwargs や nf models の results_ からパラメータ抽出

        log_run_end(
            run_id=run_id,
            status="success",
            metrics=metric_results,
            best_params=best_params,
            model_properties={"models": models_info},
            resource_after=resource_end
        )

        # メタデータの構築
        duration = time.time() - start_time
        meta = {
            "run_id": run_id,
            "model_name": model_name,
            "backend": backend,
            "duration_seconds": duration,
            "metrics": metric_results,
            "status": "success",
            "agent_metadata": agent_metadata or {},
            "params": kwargs
        }

        return preds, meta

    except Exception as e:
        # 7. DBログ: エラー発生 (FAILED状態)
        logger.error(f"Experiment failed with error: {e}")
        log_run_error(run_id=run_id, exc=e)
        raise e