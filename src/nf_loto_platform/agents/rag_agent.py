"""
RAG (Retrieval Augmented Generation) Agent implementation.

過去の時系列データから、現在の入力（クエリ）と類似したパターンを検索し、
「過去の類似状況下で何が起きたか」という文脈情報（Context）を提供するエージェント。

主な役割:
1. 直近の系列データを受け取り、過去の膨大な履歴から類似区間を検索する (Retrieval)
2. 検索結果（類似度、その後の挙動）を構造化データとしてまとめる
3. 必要に応じて、検索結果の要約（「過去の類似例では8割が上昇トレンドだった」等）を生成する
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from nf_loto_platform.db.loto_repository import load_panel_by_loto, search_similar_patterns
from nf_loto_platform.agents.llm_client import LLMClient

logger = logging.getLogger(__name__)


class RagAgent:
    """
    Historical Pattern Matcher Agent.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: agent_config.yaml の内容
        """
        self.config = config
        self.agent_config = config.get("agents", {}).get("rag", {})
        
        # LLMは検索結果の要約生成（サマリー）に使用する
        self.llm = LLMClient(config)
        
        self.name = self.agent_config.get("name", "PatternRetriever")
        
        # 検索設定
        rag_conf = self.agent_config.get("config", {})
        self.top_k = rag_conf.get("top_k", 5)
        self.similarity_threshold = rag_conf.get("similarity_threshold", 0.6)

    def search(
        self,
        table_name: str,
        loto: str,
        unique_ids: List[str],
        horizon: int
    ) -> Dict[str, Any]:
        """
        類似パターンを検索し、コンテキスト情報を返す。

        Args:
            table_name: データソーステーブル
            loto: ロト種別
            unique_ids: 対象系列ID
            horizon: 予測ホライゾン（クエリとして使う直近データの長さにも影響）

        Returns:
            Dict: 系列ごとの検索結果と、全体の要約を含む辞書
        """
        logger.info(f"[{self.name}] Starting retrieval for {unique_ids}...")

        # 1. 直近データの取得（クエリ用）
        # 検索クエリの長さは horizon の 1.5倍 程度確保するとパターンマッチしやすい
        lookback = int(horizon * 1.5)
        if lookback < 10: lookback = 10 # 最低限の長さ
        
        # load_panel_by_loto は全期間を取ってくる仕様なので、ここでフィルタリングする
        # ※ 実運用では lookback 分だけ取得する効率的なクエリメソッドを作るべきだが、
        #    現状はメモリ上でスライスする。
        df_panel = load_panel_by_loto(table_name, loto, unique_ids)
        if df_panel.empty:
            return {"error": "No data found"}

        results = {}
        retrieved_counts = 0

        # 2. 系列ごとに類似検索
        for uid in unique_ids:
            # その系列のデータを取得・ソート
            series_data = df_panel[df_panel["unique_id"] == uid].sort_values("ds")
            y_values = series_data["y"].values
            
            # データが短すぎる場合はスキップ
            if len(y_values) < lookback * 2:
                logger.debug(f"Series {uid} is too short for RAG.")
                continue

            # 直近 lookback 分をクエリとする
            query_seq = y_values[-lookback:]
            
            # DBリポジトリの検索メソッドを呼び出し
            # ※ search_similar_patterns は自分自身(直近)を除外するロジックが
            #    DB層またはここで必要になるが、今回はDB層が単純実装なので
            #    結果から ds が直近のものをフィルタアウトする等の処理が必要。
            similar_df = search_similar_patterns(
                table_name=table_name,
                loto=loto,
                unique_id=uid,
                query_seq=query_seq,
                top_k=self.top_k * 2  # フィルタリング用に多めに取得
            )
            
            if not similar_df.empty:
                # 自分自身（直近データ）を除外する簡易フィルタ
                # (類似度 1.0 かつ 日付が最新に近いものは除外)
                latest_ds = series_data["ds"].max()
                similar_df = similar_df[similar_df["ds"] < latest_ds]
                
                # top_k に絞る
                similar_df = similar_df.head(self.top_k)
                
                if not similar_df.empty:
                    # 結果をリスト化
                    matches = []
                    for _, row in similar_df.iterrows():
                        matches.append({
                            "date": str(row["ds"]),
                            "similarity": float(row["similarity"]),
                            "next_value": float(row["next_value"]) if "next_value" in row else None,
                            # "window_values": row["window_values"] # データ量が多すぎる場合は省略
                        })
                    
                    # 統計的傾向の計算
                    # 「類似パターンの後は上がる？下がる？」
                    if matches and matches[0]["next_value"] is not None:
                        next_vals = [m["next_value"] for m in matches]
                        current_val = query_seq[-1]
                        up_count = sum(1 for v in next_vals if v > current_val)
                        down_count = sum(1 for v in next_vals if v < current_val)
                        direction_prob = up_count / len(next_vals)
                        
                        trend_hint = "Bullish" if direction_prob > 0.6 else ("Bearish" if direction_prob < 0.4 else "Neutral")
                        
                        results[uid] = {
                            "matches": matches,
                            "trend_hint": trend_hint,
                            "up_probability": direction_prob,
                            "mean_next_val": float(np.mean(next_vals))
                        }
                        retrieved_counts += 1

        # 3. 検索結果の要約 (LLMには渡さず、メタデータとして返す)
        # 必要であればここで LLM を呼んで "Rag Report" を作ることも可能
        summary = {
            "retrieved_series_count": retrieved_counts,
            "details": results,
            "description": f"Found similar patterns for {retrieved_counts} series using window size {lookback}."
        }
        
        logger.info(f"[{self.name}] Retrieval completed. Matches found for {retrieved_counts} series.")
        return summary

    def get_context_for_model(self, search_results: Dict[str, Any], unique_id: str) -> Optional[np.ndarray]:
        """
        モデル(TSFMAdapter)に渡すための形式（numpy array等）に変換するヘルパー。
        
        Args:
            search_results: search() の戻り値
            unique_id: 対象系列ID
        
        Returns:
            Optional[np.ndarray]: 類似パターンの配列 (Shape: [n_matches, seq_len])
        """
        if unique_id not in search_results.get("details", {}):
            return None
            
        details = search_results["details"][unique_id]
        # もし window_values を保持していればここで array にして返す
        # 現状の実装では省略しているので None
        return None