from __future__ import annotations

"""CausalAgent: 因果グラフを推定して要約する軽量エージェント.

- 依存ライブラリが未インストールの場合でも安全に動作するよう、
  ImportError はキャッチして「空のグラフ + 説明文」でフォールバックする。
- 本番環境では ``causallearn`` などをインストールして有効化することを想定している。
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

import pandas as pd


@dataclass
class CausalGraphResult:
    algorithm: str
    graph_json: Dict[str, Any]
    adjacency_matrix: Optional[Dict[str, Any]]
    interpretation: Optional[str]


class CausalAgent:
    """因果グラフ推定を担当するエージェント.

    現時点では簡易版として PC アルゴリズムベースの実装だけを持ち、
    それ以外のアルゴリズム指定時はフォールバックで空グラフを返す。
    """

    def __init__(self, algorithm: str = "pc", alpha: float = 0.05) -> None:
        self._algorithm = algorithm
        self._alpha = alpha

    def run(self, panel_df: pd.DataFrame, target_column: Optional[str] = None) -> CausalGraphResult:
        """与えられたパネルデータから因果グラフを 1 つ推定する.

        Parameters
        ----------
        panel_df:
            long-format でも wide-format でもよいが、内部では単純に
            数値カラムのみを抽出して用いる。
        target_column:
            特に重視したい目的変数名（現状は説明テキストにのみ使用）。
        """  # noqa: D401
        if self._algorithm.lower() == "pc":
            return self._run_pc(panel_df, target_column=target_column)
        return self._fallback(panel_df, reason=f"unsupported algorithm: {self._algorithm!r}")

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _run_pc(self, panel_df: pd.DataFrame, target_column: Optional[str]) -> CausalGraphResult:
        try:
            from causallearn.search.ConstraintBased.PC import pc  # type: ignore[import]
            from causallearn.utils.cit import fisherz  # type: ignore[import]
        except Exception:
            return self._fallback(panel_df, reason="causallearn is not installed")

        num_df = panel_df.select_dtypes(include="number").dropna()
        if num_df.shape[1] < 2:
            return self._fallback(num_df, reason="not enough numeric columns for causal discovery")

        data = num_df.to_numpy()
        try:
            cg = pc(data, fisherz, self._alpha)
        except Exception as exc:  # pragma: no cover - safety net
            return self._fallback(num_df, reason=f"pc() failed: {exc}")

        # ライブラリ固有の Graph 構造にはあまり依存せず、テキスト表現を保存する。
        nodes = list(num_df.columns)
        graph_json: Dict[str, Any] = {
            "nodes": nodes,
            "raw_graph_str": str(getattr(cg, "G", cg)),
        }

        interpretation = (
            "PC アルゴリズムにより数値特徴間の因果候補グラフを推定しました。"
            f" ノード数={len(nodes)}。"
        )
        if target_column and target_column in nodes:
            interpretation += f" 目的変数 {target_column!r} に入る矢印に着目すると、候補の原因変数を絞り込めます。"

        return CausalGraphResult(
            algorithm="pc",
            graph_json=graph_json,
            adjacency_matrix=None,
            interpretation=interpretation,
        )

    def _fallback(self, df: pd.DataFrame, reason: str) -> CausalGraphResult:
        nodes = list(df.columns)
        graph_json: Dict[str, Any] = {
            "nodes": nodes,
            "edges": [],
            "note": f"causal graph not estimated: {reason}",
        }
        interpretation = (
            "因果推定ライブラリが利用できない、あるいはデータ条件を満たさなかったため、"
            "空のグラフを返します。"
        )
        return CausalGraphResult(
            algorithm=self._algorithm,
            graph_json=graph_json,
            adjacency_matrix=None,
            interpretation=interpretation,
        )
