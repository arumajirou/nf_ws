"""
RAG (Retrieval Augmented Generation) Agent implementation.

過去の時系列データから、現在の入力（クエリ）と類似したパターンを検索し、
「過去の類似状況下で何が起きたか」という文脈情報（Context）を提供するエージェント。

主な役割:
1. 直近の系列データを受け取り、過去の膨大な履歴から類似区間を検索する (Retrieval)
2. 検索結果（類似度、その後の挙動）を構造化データとしてまとめる
3. LLMのContext Window制限に対応するため、時系列データの圧縮(Pooling)や、
   検索結果の自然言語要約(Summarization)を行う機能を提供する。
"""

from __future__ import annotations

import logging
import json
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from nf_loto_platform.db.loto_repository import load_panel_by_loto, search_similar_patterns
from nf_loto_platform.agents.llm_client import LLMClient

logger = logging.getLogger(__name__)


class RagAgent:
    """
    Historical Pattern Matcher Agent with Context Compression.
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
        
        # 圧縮設定: 長い系列をこの長さに圧縮(Pooling)する
        self.compressed_sequence_length = rag_conf.get("compressed_sequence_length", 64)

    def _compress_sequence(self, sequence: np.ndarray, target_len: int) -> List[float]:
        """
        長い時系列データを平均プーリングにより指定の長さに圧縮する。
        トークン節約のための損失あり圧縮。
        """
        seq_len = len(sequence)
        if seq_len <= target_len:
            return sequence.tolist()
        
        # シンプルな平均プーリング
        # split array into target_len chunks
        split_indices = np.linspace(0, seq_len, target_len + 1, dtype=int)
        compressed = []
        for i in range(target_len):
            start, end = split_indices[i], split_indices[i+1]
            if start == end: # avoid empty slice if target_len > seq_len (not possible due to if above)
                continue
            chunk_mean = np.mean(sequence[start:end])
            compressed.append(float(chunk_mean))
            
        return compressed

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
            # top_k * 2 件取得し、後処理でフィルタリングする
            similar_df = search_similar_patterns(
                table_name=table_name,
                loto=loto,
                unique_id=uid,
                query_seq=query_seq,
                top_k=self.top_k * 2
            )
            
            if not similar_df.empty:
                # 自分自身（直近データ）を除外する簡易フィルタ
                latest_ds = series_data["ds"].max()
                similar_df = similar_df[similar_df["ds"] < latest_ds]
                
                # top_k に絞る
                similar_df = similar_df.head(self.top_k)
                
                if not similar_df.empty:
                    # 結果をリスト化
                    matches = []
                    next_values_collection = []

                    for _, row in similar_df.iterrows():
                        # window_values (一致した過去の区間データ) があれば圧縮して保持
                        window_vals = row.get("window_values", [])
                        compressed_window = []
                        if isinstance(window_vals, (list, np.ndarray)) and len(window_vals) > 0:
                            compressed_window = self._compress_sequence(np.array(window_vals), self.compressed_sequence_length)

                        match_info = {
                            "date": str(row["ds"]),
                            "similarity": float(row["similarity"]),
                            "next_value": float(row["next_value"]) if "next_value" in row else None,
                            "compressed_window": compressed_window  # 圧縮済みデータ
                        }
                        matches.append(match_info)
                        if "next_value" in row and row["next_value"] is not None:
                            next_values_collection.append(float(row["next_value"]))
                    
                    # 統計的傾向の計算
                    trend_stats = {}
                    if next_values_collection:
                        current_val = query_seq[-1]
                        up_count = sum(1 for v in next_values_collection if v > current_val)
                        direction_prob = up_count / len(next_values_collection)
                        mean_next = float(np.mean(next_values_collection))
                        
                        trend_hint = "Bullish" if direction_prob > 0.6 else ("Bearish" if direction_prob < 0.4 else "Neutral")
                        trend_stats = {
                            "trend_hint": trend_hint,
                            "up_probability": direction_prob,
                            "mean_next_val": mean_next
                        }

                    results[uid] = {
                        "matches": matches,
                        "stats": trend_stats
                    }
                    retrieved_counts += 1

        # 3. 検索結果の要約 (LLMを使って自然言語レポートを生成)
        rag_report = self.generate_rag_report(results, retrieved_counts)
        
        summary = {
            "retrieved_series_count": retrieved_counts,
            "details": results,
            "report": rag_report, # 自然言語要約
            "description": f"Found similar patterns for {retrieved_counts} series using window size {lookback}."
        }
        
        logger.info(f"[{self.name}] Retrieval completed. Matches found for {retrieved_counts} series.")
        return summary

    def generate_rag_report(self, results: Dict[str, Any], count: int) -> str:
        """
        検索結果の統計情報をLLMに渡し、予測に役立つ短い要約文を生成させる。
        これにより、数値データを羅列するよりも少ないトークンで文脈をモデルに伝えられる。
        """
        if count == 0:
            return "No historical similar patterns found."

        # LLMへの入力用サマリーデータを作成（軽量化）
        summary_for_llm = {}
        for uid, res in results.items():
            stats = res.get("stats", {})
            if stats:
                summary_for_llm[uid] = {
                    "trend": stats.get("trend_hint"),
                    "up_prob": f"{stats.get('up_probability', 0):.2f}",
                    "matches_found": len(res.get("matches", []))
                }
        
        # プロンプト構築
        system_prompt = (
            "You are a Time Series Analyst. Summarize the retrieved historical patterns "
            "into a concise hint for forecasting. Focus on the dominant trend (Bullish/Bearish) "
            "observed in similar past situations."
        )
        user_msg = f"Analyze these retrieval stats and provide a 1-sentence forecast hint:\n{json.dumps(summary_for_llm)}"
        
        try:
            # LLM呼び出し (チャット形式を想定)
            # ※ LLMClientの実装に合わせて適宜調整してください
            response = self.llm.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg}
            ])
            return response.strip()
        except Exception as e:
            logger.warning(f"Failed to generate RAG report with LLM: {e}")
            return "RAG retrieval completed, but summary generation failed."

    def get_context_for_model(self, search_results: Dict[str, Any], unique_id: str) -> Optional[np.ndarray]:
        """
        モデル(TSFMAdapter等)に渡すための数値コンテキストを取得する。
        圧縮済みの時系列データを展開して返す。
        """
        details = search_results.get("details", {}).get(unique_id)
        if not details:
            return None
        
        matches = details.get("matches", [])
        if not matches:
            return None

        # 圧縮されたウィンドウデータを取得 (List[float])
        # モデル入力用に numpy array に変換 [n_matches, compressed_len]
        contexts = []
        for m in matches:
            cw = m.get("compressed_window")
            if cw:
                contexts.append(cw)
        
        if not contexts:
            return None
            
        return np.array(contexts)