"""
Analyst Agent implementation.

時系列データの統計的特性（トレンド、季節性、定常性、異常値）を分析し、
LLMを用いて洞察（Insight）を含むレポートを生成するエージェント。

主な役割:
1. 生データから客観的な統計指標を計算する (Tools)
2. 統計指標をコンテキストとしてLLMに渡し、データの性質を言語化する
3. Planner Agent が適切なモデルを選択するための根拠を提供する
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

# statsmodels は重い依存関係なので、importエラー時は機能を制限する
try:
    from statsmodels.tsa.stattools import adfuller, acf
    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False

from nf_loto_platform.db.loto_repository import load_panel_by_loto
from nf_loto_platform.agents.llm_client import LLMClient

logger = logging.getLogger(__name__)


class AnalystAgent:
    """
    Senior Data Analyst Agent specialized in Time Series.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: agent_config.yaml の 'llm' セクションおよび 'agents.analyst' セクション
        """
        self.config = config
        self.agent_config = config.get("agents", {}).get("analyst", {})
        self.llm = LLMClient(config)
        
        self.name = self.agent_config.get("name", "Analyst")
        self.system_prompt = self.agent_config.get(
            "system_prompt",
            "You are an expert Time Series Analyst. Provide quantitative insights."
        )

    def analyze(
        self,
        table_name: str,
        loto: str,
        unique_ids: List[str]
    ) -> str:
        """
        指定された系列のデータを分析し、レポートを返す。

        Args:
            table_name: データソーステーブル
            loto: ロト種別
            unique_ids: 分析対象の系列IDリスト

        Returns:
            str: LLMによって生成された分析レポート
        """
        logger.info(f"[{self.name}] Starting analysis for {unique_ids}...")

        # 1. データ取得
        df_panel = load_panel_by_loto(table_name, loto, unique_ids)
        if df_panel.empty:
            return "No data found for analysis."

        # 2. 統計指標の計算 (Tools execution)
        stats_summary = self._compute_statistics(df_panel)

        # 3. プロンプト構築
        user_prompt = self._build_analysis_prompt(loto, unique_ids, stats_summary)

        # 4. LLM実行
        report = self.llm.chat_completion(
            system_prompt=self.system_prompt,
            user_prompt=user_prompt
        )
        
        logger.info(f"[{self.name}] Analysis completed.")
        return report

    def _compute_statistics(self, df: pd.DataFrame) -> Dict[str, Any]:
        """データフレームから各種統計量を計算する."""
        summary = {}
        
        # 全体の基本統計量
        summary["global_stats"] = df["y"].describe().to_dict()
        summary["n_series"] = df["unique_id"].nunique()
        summary["n_obs"] = len(df)
        summary["time_range"] = {
            "start": str(df["ds"].min()),
            "end": str(df["ds"].max())
        }

        # 系列ごとの詳細分析 (先頭数件のみ、または集約)
        series_details = {}
        unique_ids = df["unique_id"].unique()
        
        # トークン節約のため、最大3系列までを詳細分析
        for uid in unique_ids[:3]:
            series = df[df["unique_id"] == uid].sort_values("ds")["y"]
            series_details[uid] = self._analyze_single_series(series)
            
        summary["series_analysis"] = series_details
        return summary

    def _analyze_single_series(self, series: pd.Series) -> Dict[str, Any]:
        """単一系列の詳細分析を行う."""
        # 欠損値チェック
        n_missing = series.isna().sum()
        
        # 異常値検知 (IQR法)
        q1 = series.quantile(0.25)
        q3 = series.quantile(0.75)
        iqr = q3 - q1
        outliers = ((series < (q1 - 1.5 * iqr)) | (series > (q3 + 1.5 * iqr))).sum()
        
        stats_dict = {
            "length": len(series),
            "missing_count": int(n_missing),
            "outlier_count": int(outliers),
            "mean": float(series.mean()),
            "std": float(series.std()),
            "trend": "unknown",
            "seasonality": "unknown",
            "stationarity": "unknown"
        }
        
        if not STATSMODELS_AVAILABLE:
            return stats_dict

        # Series cleaning (for statsmodels)
        clean_series = series.dropna()
        if len(clean_series) < 20:
            return stats_dict  # データ不足

        # 1. 定常性検定 (ADF Test)
        try:
            adf_result = adfuller(clean_series)
            p_value = adf_result[1]
            stats_dict["stationarity"] = "Stationary" if p_value < 0.05 else "Non-stationary (Unit Root)"
            stats_dict["adf_p_value"] = float(p_value)
        except Exception:
            stats_dict["stationarity"] = "Error in ADF"

        # 2. トレンド・季節性推定
        # 簡易的なトレンド判定 (相関係数)
        x = np.arange(len(clean_series))
        slope, _, _, _, _ = stats.linregress(x, clean_series.values)
        if abs(slope) < 0.01 * clean_series.mean(): # 閾値はヒューリスティック
            stats_dict["trend"] = "Flat"
        else:
            stats_dict["trend"] = "Upward" if slope > 0 else "Downward"

        # 3. 自己相関による周期性ヒント
        try:
            # ラグ 1~30 の自己相関を確認
            acf_vals = acf(clean_series, nlags=30, fft=True)
            # ラグ0を除いて最大の相関を持つラグを探す
            max_lag = np.argmax(acf_vals[1:]) + 1
            max_corr = acf_vals[max_lag]
            
            if max_corr > 0.3: # 閾値
                stats_dict["seasonality"] = f"Potential cycle at lag {max_lag} (corr={max_corr:.2f})"
            else:
                stats_dict["seasonality"] = "No strong seasonality detected"
        except Exception:
            pass

        return stats_dict

    def _build_analysis_prompt(
        self, 
        loto: str, 
        unique_ids: List[str], 
        stats_summary: Dict[str, Any]
    ) -> str:
        """分析結果をLLM向けのプロンプトに変換する."""
        
        # JSONシリアライズして構造化データとして渡す
        stats_json = json.dumps(stats_summary, indent=2, default=str)
        
        # 安全なコードブロック生成のための変数
        code_block = "```"
        
        prompt = f"""
## Analysis Target
- Loto Type: {loto}
- Series IDs: {unique_ids}

## Computed Statistics (JSON)
{code_block}json
{stats_json}
{code_block}

## Task
Based on the statistics above, please analyze the time series characteristics.
Focus on:
1. **Trend & Stationarity**: Is the data stationary? Is there a clear trend?
2. **Seasonality**: Are there any cyclic patterns?
3. **Anomalies**: Are there significantly outliers or noise?
4. **Complexity**: Is this a simple dataset (solvable by MLP/ARIMA) or complex (requires Transformer/TFT)?

Your output will be used by the "Planner Agent" to select the best forecasting model.
Provide a concise "Analyst Report".
"""
        return prompt