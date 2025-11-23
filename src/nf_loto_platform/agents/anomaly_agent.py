from __future__ import annotations

"""AnomalyAgent: 時系列パネルに対する簡易異常検知エージェント.

- まずは z-score ベースの軽量な実装のみを持つ。
- 将来的に Merlion / kats / anomaly-agent 等へのブリッジを追加できるよう、
  実装はシンプルなインターフェースで切ってある。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd


@dataclass
class AnomalyRecord:
    ts: Any
    series_id: str
    score: float
    is_anomaly: bool
    method: str
    meta: Dict[str, Any] = field(default_factory=dict)


class AnomalyAgent:
    """異常検知を担当するエージェント."""

    def __init__(
        self,
        method: str = "zscore",
        z_threshold: float = 3.0,
    ) -> None:
        self._method = method
        self._z_threshold = z_threshold

    def detect(
        self,
        panel_df: pd.DataFrame,
        value_column: str = "y",
        ts_column: str = "ds",
        id_columns: Optional[Sequence[str]] = None,
    ) -> List[AnomalyRecord]:
        """パネルデータから異常候補点を抽出する.

        現時点では z-score による単純なしきい値判定のみを実装している。
        groupby(id_columns) ごとに平均・標準偏差を計算し、|z| >= threshold を異常とみなす。
        """  # noqa: D401
        if self._method != "zscore":
            # 将来 Merlion 等を統合する余地を残す
            raise ValueError(f"unsupported anomaly method: {self._method!r}")

        if id_columns is None:
            id_columns = []

        required_cols = list(id_columns) + [ts_column, value_column]
        missing = [c for c in required_cols if c not in panel_df.columns]
        if missing:
            raise ValueError(f"必要なカラムが不足しています: {missing}")

        records: List[AnomalyRecord] = []

        if not id_columns:
            # ID なしの場合、全体で z-score 計算
            y = panel_df[value_column].to_numpy()
            mean = float(np.mean(y))
            std = float(np.std(y, ddof=1))
            if std < 1e-9:
                # 標準偏差がほぼ 0 なら異常なし
                return records
            z_scores = (y - mean) / std
            mask = np.abs(z_scores) >= self._z_threshold
            for idx in np.where(mask)[0]:
                row = panel_df.iloc[idx]
                records.append(
                    AnomalyRecord(
                        ts=row[ts_column],
                        series_id="__global__",
                        score=float(z_scores[idx]),
                        is_anomaly=True,
                        method="zscore",
                        meta={"mean": mean, "std": std, "threshold": self._z_threshold},
                    )
                )
        else:
            # ID ごとにグループ化して z-score 計算
            grouped = panel_df.groupby(list(id_columns), dropna=False, sort=False)
            for group_key, group_df in grouped:
                series_id_str = str(group_key) if isinstance(group_key, tuple) else str(group_key)
                y = group_df[value_column].to_numpy()
                if len(y) < 2:
                    continue
                mean = float(np.mean(y))
                std = float(np.std(y, ddof=1))
                if std < 1e-9:
                    continue
                z_scores = (y - mean) / std
                mask = np.abs(z_scores) >= self._z_threshold
                for local_idx in np.where(mask)[0]:
                    row = group_df.iloc[local_idx]
                    records.append(
                        AnomalyRecord(
                            ts=row[ts_column],
                            series_id=series_id_str,
                            score=float(z_scores[local_idx]),
                            is_anomaly=True,
                            method="zscore",
                            meta={"mean": mean, "std": std, "threshold": self._z_threshold},
                        )
                    )
        return records

    def to_rows(self, records: Sequence[AnomalyRecord]) -> List[Dict[str, Any]]:
        """ts_research_store.bulk_insert_anomalies 向けの辞書リストに変換する."""
        rows: List[Dict[str, Any]] = []
        for rec in records:
            rows.append(
                {
                    "ts": rec.ts,
                    "series_id": rec.series_id,
                    "score": rec.score,
                    "is_anomaly": rec.is_anomaly,
                    "method": rec.method,
                    "meta": rec.meta,
                }
            )
        return rows
