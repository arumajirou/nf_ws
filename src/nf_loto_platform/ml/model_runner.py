"""
モデル学習・推論実行モジュール (Extended for TSFM & Agents).

NeuralForecast および TSFM (Time Series Foundation Models) を統一的に扱い、
実験の実行、評価、メタデータの生成を行う。
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
import torch

# --- Core Imports ---
from nf_loto_platform.db.loto_repository import load_panel_by_loto, search_similar_patterns
from nf_loto_platform.ml.automodel_builder import build_auto_model, build_neuralforecast
from nf_loto_platform.ml.model_registry import get_model_spec, AutoModelSpec

# --- TSFM Adapters (Dynamic Imports could be better, but static for now) ---
# Note: User is expected to implement these adapter modules in src/nf_loto_platform/tsfm/
try:
    from nf_loto_platform.tsfm.time_moe_adapter import TimeMoEAdapter
    from nf_loto_platform.tsfm.chronos_adapter import ChronosAdapter
    from nf_loto_platform.tsfm.moment_adapter import MomentAdapter
except ImportError:
    # アダプタ未実装時のフォールバック用ダミー
    TimeMoEAdapter = None
    ChronosAdapter = None
    MomentAdapter = None

# --- Setup Logger ---
logger = logging.getLogger(__name__)


@dataclass
class ExperimentResult:
    """実験結果データクラス."""
    preds: pd.DataFrame
    meta: Dict[str, Any]


def _get_system_info() -> Dict[str, Any]:
    """実行環境の情報を収集する."""
    info = {
        "gpu_available": torch.cuda.is_available(),
        "gpu_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
        "torch_version": torch.__version__,
    }
    if info["gpu_available"]:
        info["gpu_name"] = torch.cuda.get_device_name(0)
    return info


def _calculate_uncertainty_score(preds: pd.DataFrame) -> float:
    """
    予測の不確実性スコアを簡易計算する.
    
    区間予測(Quantiles)がある場合: 平均区間幅 / 平均予測値
    点予測のみの場合: 0.0 (またはヒューリスティックな値)
    
    Returns:
        0.0 ~ 1.0+ のスコア (高いほど不確実)
    """
    # NeuralForecastの分位点カラム (例: "AutoNHITS-median", "AutoNHITS-lo-90", "AutoNHITS-hi-90")
    # ここでは簡易的に lo-90 と hi-90 があるか探す
    cols = preds.columns
    lo_cols = [c for c in cols if "-lo-" in c]
    hi_cols = [c for c in cols if "-hi-" in c]
    
    if not lo_cols or not hi_cols:
        return 0.0
    
    # 最初のペアを使用
    lo_col = lo_cols[0]
    hi_col = hi_cols[0]
    # モデル名部分を除去して予測値カラム("AutoNHITS")を特定したいが、
    # 簡易的に median か mean を探す、なければ y_hat 的なものを探す
    
    # 区間幅
    interval_width = (preds[hi_col] - preds[lo_col]).abs().mean()
    
    # 予測値のスケール (0除算回避)
    # y_hat カラムを探す
    y_hat_col = next((c for c in cols if c not in ["unique_id", "ds", "y"] and "-lo-" not in c and "-hi-" not in c), None)
    prediction_scale = preds[y_hat_col].abs().mean() if y_hat_col else 1.0
    if prediction_scale < 1e-6:
        prediction_scale = 1.0
        
    return float(interval_width / prediction_scale)


def _instantiate_tsfm_adapter(spec: AutoModelSpec, config: Dict[str, Any]):
    """TSFMモデルのアダプタをインスタンス化するファクトリー関数."""
    if spec.engine_name == "time_moe":
        if TimeMoEAdapter is None:
            raise ImportError("TimeMoEAdapter not found. Please implement it in tsfm/time_moe_adapter.py")
        return TimeMoEAdapter(model_size=spec.name, **config)
    
    elif spec.engine_name == "chronos2":
        if ChronosAdapter is None:
            raise ImportError("ChronosAdapter not found.")
        return ChronosAdapter(model_name=spec.name, **config)
    
    elif spec.engine_name == "moment":
        if MomentAdapter is None:
            raise ImportError("MomentAdapter not found.")
        return MomentAdapter(model_name=spec.name, **config)
    
    else:
        raise ValueError(f"Unsupported TSFM engine: {spec.engine_name}")


def run_loto_experiment(
    table_name: str,
    loto: str,
    unique_ids: List[str],
    model_name: str,
    backend: str,
    horizon: int,
    objective: str = "mae",
    secondary_metric: str = "val_loss",
    num_samples: int = 10,
    cpus: int = 1,
    gpus: int = 0,
    search_space: Optional[Dict[str, Any]] = None,
    freq: str = "D",
    # --- Advanced Options ---
    local_scaler_type: str = "standard",
    val_size: int = 0,
    refit_with_val: bool = True,
    use_init_models: bool = True,
    early_stop: bool = True,
    early_stop_patience_steps: int = 5,
    # --- New Features ---
    use_rag: bool = False,
    agent_metadata: Optional[Dict[str, Any]] = None,
    mlflow_run_id: Optional[str] = None,
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """
    ロト時系列予測実験を実行するメイン関数.

    Args:
        table_name: データソーステーブル
        loto: ロト種別
        unique_ids: 学習対象系列ID
        model_name: モデル名 (例: "AutoNHITS", "Time-MoE-2.4B")
        backend: "optuna", "ray", "tsfm" (Zero-shot)
        use_rag: RAG (類似パターン検索) を有効にするか
        agent_metadata: AIエージェントからの指示・分析データ

    Returns:
        (predictions_df, metadata_dict)
    """
    start_time = time.time()
    system_info = _get_system_info()
    
    # 1. モデル仕様の取得と検証
    spec = get_model_spec(model_name)
    if spec is None:
        raise ValueError(f"Model {model_name} not found in registry.")
    
    # バックエンドの整合性チェック
    if spec.engine_kind == "tsfm" and backend != "tsfm":
        logger.warning(f"Model {model_name} is a TSFM model. Forcing backend='tsfm'.")
        backend = "tsfm"
    
    # 2. データロード
    logger.info(f"Loading data for {unique_ids} from {table_name}...")
    df_panel = load_panel_by_loto(table_name, loto, unique_ids)
    
    # 3. RAG: 類似パターン検索 (Optional)
    rag_context_ids = []
    rag_metadata = {}
    
    if use_rag:
        logger.info("Executing RAG: Searching for similar historical patterns...")
        # 直近のデータをクエリとして使用 (horizonと同じ長さとする簡易実装)
        # 実運用ではより高度なクエリ生成が必要
        try:
            for uid in unique_ids:
                series_data = df_panel[df_panel["unique_id"] == uid].sort_values("ds")["y"].values
                if len(series_data) > horizon:
                    query_seq = series_data[-horizon:] # 直近 horizon 分
                    similar_df = search_similar_patterns(
                        table_name=table_name,
                        loto=loto,
                        unique_id=uid,
                        query_seq=query_seq,
                        top_k=3
                    )
                    if not similar_df.empty:
                        # IDの記録 (日付をID代わりにする等)
                        rag_context_ids.extend([f"{uid}_{d}" for d in similar_df["ds"].astype(str)])
                        
                        # ここで類似データを学習データに追加する、あるいは
                        # プロンプトとしてLLMに渡す等の処理が可能。
                        # 今回はメタデータ記録に留める。
                        rag_metadata[uid] = similar_df.to_dict(orient="records")
        except Exception as e:
            logger.error(f"RAG search failed: {e}")
            # RAG失敗でも実験は止めない

    # 4. モデル実行 (分岐)
    preds = pd.DataFrame()
    best_params = {}
    model_metrics = {}
    
    try:
        if spec.engine_kind == "neuralforecast":
            # --- 従来パス: NeuralForecast (AutoML) ---
            
            # 4.1 AutoModel 構築
            # 損失関数の設定 (不確実性のためにMQLoss等を使う場合はここで指定)
            loss = None # Default
            
            # 設定辞書作成
            optimization_config = {
                "num_samples": num_samples,
                "cpus": cpus,
                "gpus": gpus,
                "timeout": None,
                "search_space": search_space,
                "early_stop_patience_steps": early_stop_patience_steps,
                "early_stop": early_stop,
                "verbose": True,
                "alias": model_name
            }
            
            auto_model = build_auto_model(
                model_name=model_name,
                h=horizon,
                loss=loss,
                config=optimization_config,
                backend=backend
            )
            
            # 4.2 NeuralForecast インスタンス化 & 学習
            nf = build_neuralforecast(
                models=[auto_model],
                freq=freq,
                local_scaler_type=local_scaler_type
            )
            
            # Cross-validation もしくは 通常fit
            if val_size > 0:
                logger.info(f"Starting cross-validation (val_size={val_size})...")
                # NeuralForecastのcross_validationは時系列CV
                # 簡易的に fit してから predict するフローにするか、frameworkの cv を使うか。
                # ここでは単純化のため fit -> predict を採用し、val_sizeは内部分割用とする。
                nf.fit(df=df_panel, val_size=val_size)
            else:
                logger.info("Starting training (no validation split)...")
                nf.fit(df=df_panel)
            
            # 4.3 予測
            logger.info("Predicting...")
            preds = nf.predict()
            
            # 4.4 結果収集
            # best_params の取得 (AutoModel のresultsから)
            # 注: NeuralForecastの実装バージョンにより取得方法が異なる場合がある
            if hasattr(auto_model, "results_"):
                 # ray/optuna の結果
                 best_params = getattr(auto_model, "best_config", {})
            
        elif spec.engine_kind == "tsfm":
            # --- 新規パス: TSFM (Foundation Models) ---
            logger.info(f"Running TSFM Adapter: {spec.engine_name} ({spec.name})")
            
            tsfm_config = {
                "context_length": spec.context_length,
                "use_gpu": gpus > 0,
                # RAG情報を渡すならここ
                "rag_context": rag_metadata if use_rag else None
            }
            
            adapter = _instantiate_tsfm_adapter(spec, tsfm_config)
            
            # Fit (Zero-shotの場合は何もしない、Few-shotなら学習)
            if not spec.is_zero_shot:
                adapter.fit(df_panel)
            
            # Predict
            preds = adapter.predict(df_panel, horizon=horizon)
            best_params = {"mode": "zero_shot" if spec.is_zero_shot else "fine_tuned"}

        else:
            raise ValueError(f"Unknown engine kind: {spec.engine_kind}")

    except Exception as e:
        logger.error(f"Model execution failed: {traceback.format_exc()}")
        raise e

    # 5. 不確実性スコアの計算 (Post-processing)
    uncertainty_score = _calculate_uncertainty_score(preds)
    uncertainty_metrics = {
        "score": uncertainty_score,
        "method": "quantile_width" if spec.engine_kind == "neuralforecast" else "unknown"
    }

    # 6. メタデータ作成
    end_time = time.time()
    duration = end_time - start_time
    
    meta = {
        "table_name": table_name,
        "loto": loto,
        "unique_ids": unique_ids,
        "model_name": model_name,
        "backend": backend,
        "horizon": horizon,
        "status": "COMPLETED",
        "duration_seconds": duration,
        "system_info": system_info,
        "best_params": best_params,
        # New Fields
        "rag_context_ids": rag_context_ids,
        "rag_metadata": rag_metadata,
        "agent_metadata": agent_metadata or {},
        "uncertainty_score": uncertainty_score,
        "uncertainty_metrics": uncertainty_metrics,
        "mlflow_run_id": mlflow_run_id
    }
    
    logger.info(f"Experiment completed in {duration:.2f}s. Uncertainty: {uncertainty_score:.4f}")
    return preds, meta


def sweep_loto_experiments(
    table_name: str,
    loto: str,
    unique_ids: List[str],
    model_names: List[str],
    backends: List[str],
    param_spec: Dict[str, List[Any]],
    mode: str = "grid",
    **kwargs
) -> List[ExperimentResult]:
    """
    複数のモデル・設定条件で実験を一括実行する (Grid Search Runner).
    
    UI から呼び出されるエントリーポイント。
    """
    import itertools
    
    results = []
    
    # パラメータの組み合わせ生成
    keys = list(param_spec.keys())
    values = list(param_spec.values())
    combinations = list(itertools.product(*values)) if mode == "grid" else zip(*values)
    
    total_runs = len(model_names) * len(backends) * len(combinations)
    logger.info(f"Starting sweep: {total_runs} experiments total.")
    
    for model in model_names:
        for backend in backends:
            for combo in combinations:
                # kwargs の override
                current_params = kwargs.copy()
                for k, v in zip(keys, combo):
                    current_params[k] = v
                
                try:
                    preds, meta = run_loto_experiment(
                        table_name=table_name,
                        loto=loto,
                        unique_ids=unique_ids,
                        model_name=model,
                        backend=backend,
                        **current_params
                    )
                    results.append(ExperimentResult(preds, meta))
                except Exception as e:
                    logger.error(f"Sweep run failed for {model}/{backend}: {e}")
                    # 失敗を記録して続行
                    results.append(ExperimentResult(
                        pd.DataFrame(), 
                        {"status": "FAILED", "error": str(e), "model": model}
                    ))
                    
    return results