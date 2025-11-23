"""Adapter between nf_loto_platform panel format and generic TSLib-style datasets.

TSLib 本体の API はここでは前提とせず、「時系列ベンチマーク向けの一般的な構造」を
表現するための軽量なデータクラスを提供します。

- NfPanel: nf_loto_platform でよく使われる ``unique_id, ds, y`` 形式の DataFrame を前提
- TSLibDatasetSpec: TSLib 側に渡すためのメタ情報 (名前・周波数・horizon 等)

将来的には、Deep Time Series Models の TSLib 実装にあわせてここを差し替えることで、
nf_loto_platform から TSLib への橋渡しを行います。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pandas as pd


@dataclass
class NfPanel:
    """nf_loto_platform 標準のパネル形式を束ねるラッパー."""

    df: pd.DataFrame
    id_col: str = "unique_id"
    ts_col: str = "ds"
    target_col: str = "y"

    def validate(self) -> None:
        for col in (self.id_col, self.ts_col, self.target_col):
            if col not in self.df.columns:
                raise ValueError(f"required column {col!r} is missing")


@dataclass
class TSLibDatasetSpec:
    """TSLib 向けのデータセット仕様."""

    name: str
    freq: str
    horizon: int
    input_length: int
    extra_metadata: Mapping[str, Any] | None = None


def nfpanel_from_dataframe(df: pd.DataFrame, id_col: str = "unique_id", ts_col: str = "ds", target_col: str = "y") -> NfPanel:
    """汎用 DataFrame から :class:`NfPanel` を構築して validate する."""
    panel = NfPanel(df=df.copy(), id_col=id_col, ts_col=ts_col, target_col=target_col)
    panel.validate()
    return panel


def to_tslib_long_format(panel: NfPanel) -> pd.DataFrame:
    """NfPanel を「長い形式」の DataFrame として返す.

    ここでは単に列名を TSLib 側が好みそうな ``[dataset, unique_id, time, target]`` などに
    揃えるのではなく、「nf_loto_platform 側の列名をそのまま保持」します。

    その理由:
        - 既存の特徴量列 (``hist_*``/``futr_*``/``stat_*``) を壊さない
        - TSLib への実装がまだ流動的なため、今は情報を削らない方針とする
    """
    panel.validate()
    # いまは単なるコピーだが、将来的に index や dtype をそろえる処理をここに追加する
    return panel.df.copy()
