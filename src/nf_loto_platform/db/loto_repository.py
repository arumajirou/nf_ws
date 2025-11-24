"""
PostgreSQL 上の nf_loto% テーブルにアクセスするためのユーティリティ。

- nf_loto% テーブル構成の想定:
    - loto:       TEXT      (ロト種別)
    - num:        INTEGER   (回号)
    - ds:         DATE/TIMESTAMP (日付)
    - unique_id:  TEXT      (系列ID, N1/N2/... 等)
    - y:          NUMERIC   (目的変数)
    - その他:     外生変数 (hist_*, stat_*, futr_* 接頭辞付き)
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence, List, Dict, Any, Optional

import numpy as np
import pandas as pd
import psycopg2

from .db_config import DB_CONFIG


def get_connection():
    """psycopg2 接続を返すシンプルなヘルパー。"""
    return psycopg2.connect(**DB_CONFIG)


def _validate_table_name(table_name: str) -> str:
    """SQL インジェクション対策として、テーブル名は英数とアンダースコアのみに制限。"""
    if not re.match(r"^[A-Za-z0-9_]+$", table_name):
        raise ValueError(f"不正なテーブル名です: {table_name!r}")
    return table_name


def list_loto_tables() -> pd.DataFrame:
    """nf_loto% で始まるテーブル一覧を返す。"""
    with get_connection() as conn:
        df = pd.read_sql(
            """
            SELECT tablename
            FROM pg_catalog.pg_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
              AND tablename LIKE 'nf_loto%%'
            ORDER BY tablename
            """,
            conn,
        )
    return df


def list_loto_values(table_name: str) -> pd.DataFrame:
    """指定テーブルの loto 値一覧を返す。"""
    table_name = _validate_table_name(table_name)
    query = f"""SELECT DISTINCT loto FROM {table_name} ORDER BY loto"""
    with get_connection() as conn:
        df = pd.read_sql(query, conn)
    return df


def list_unique_ids(table_name: str, loto: str) -> pd.DataFrame:
    """指定テーブル + loto の unique_id 一覧を返す。"""
    table_name = _validate_table_name(table_name)
    query = f"""
        SELECT DISTINCT unique_id
        FROM {table_name}
        WHERE loto = %s
        ORDER BY unique_id
    """
    with get_connection() as conn:
        df = pd.read_sql(query, conn, params=[loto])
    return df


def load_panel_by_loto(
    table_name: str,
    loto: str,
    unique_ids: Sequence[str],
) -> pd.DataFrame:
    """nf_loto テーブルから、指定された loto × unique_ids のパネルデータを取得。

    NeuralForecast の期待するスキーマ:
        - unique_id
        - ds
        - y
        - その他の列:
            - hist_* : 過去のみ既知の外生
            - stat_* : 静的外生
            - futr_* : 未来まで既知の外生
    """
    table_name = _validate_table_name(table_name)
    if not unique_ids:
        raise ValueError("unique_ids が空です。最低 1 件指定してください。")

    # IN 句をプレースホルダで安全に構築
    placeholders = ",".join(["%s"] * len(unique_ids))
    query = f"""
        SELECT *
        FROM {table_name}
        WHERE loto = %s
          AND unique_id IN ({placeholders})
        ORDER BY unique_id, ds
    """
    params = [loto, *list(unique_ids)]
    with get_connection() as conn:
        df = pd.read_sql(query, conn, params=params)

    # NeuralForecast の標準カラム名が揃っているか軽くチェック
    required_cols = {"unique_id", "ds", "y"}
    missing = required_cols.difference(df.columns)
    if missing:
        raise ValueError(f"必要なカラムが不足しています: {missing}")

    return df


def search_similar_patterns(
    table_name: str,
    loto: str,
    unique_id: str,
    query_seq: Sequence[float],
    top_k: int = 5,
) -> pd.DataFrame:
    """指定された系列の過去データから、query_seq に類似した波形パターンを検索する (RAG用)。

    単純なユークリッド距離に基づく類似度検索を行い、
    「類似した過去の状況」と「その直後に何が起きたか」を特定する。

    Args:
        table_name: 検索対象のテーブル名
        loto: ロト種別
        unique_id: 検索対象の系列ID
        query_seq: 直近の観測値リスト (検索クエリ)。この長さ(N)と同じ長さの過去窓を検索する。
        top_k: 返却する類似パターンの数

    Returns:
        pd.DataFrame: 以下のカラムを持つデータフレーム (類似度順)
            - ds: パターン終了日の日付
            - similarity: 類似度スコア (0.0-1.0, 1.0が完全一致)
            - next_val: そのパターンの直後の値 (予測のヒント)
            - window_values: マッチした区間の値リスト
    """
    # 1. 履歴データの取得 (yのみで可)
    table_name = _validate_table_name(table_name)
    query = f"""
        SELECT ds, y
        FROM {table_name}
        WHERE loto = %s
          AND unique_id = %s
        ORDER BY ds ASC
    """
    
    with get_connection() as conn:
        df_hist = pd.read_sql(query, conn, params=[loto, unique_id])
    
    if df_hist.empty:
        return pd.DataFrame(columns=["ds", "similarity", "next_val", "window_values"])
    
    # 2. スライディングウィンドウによる検索 (Python側で実行)
    # Note: データ量が膨大な場合は pgvector 等の利用を検討すべきだが、
    # ロト/ナンバーズ程度のデータ量(数千~数万行)であれば numpy で十分高速。
    
    y_hist = df_hist["y"].to_numpy(dtype=float)
    ds_hist = df_hist["ds"].to_numpy()
    
    q_len = len(query_seq)
    q_vec = np.array(query_seq, dtype=float)
    
    if len(y_hist) < q_len + 1:
        # データ不足
        return pd.DataFrame(columns=["ds", "similarity", "next_val", "window_values"])

    results = []
    
    # 履歴を走査 (未来の値を1つ確保するため -1)
    # query_seq 自身が含まれている場合(直近データ)、それは検索結果から除外すべきだが、
    # ここでは単純に全走査し、後処理でフィルタリングするか、呼び出し側で判断する。
    # 今回は学習データとしての利用を想定し、予測対象時点(未来)を含まない範囲をスキャン。
    
    for i in range(len(y_hist) - q_len):
        # ウィンドウ切り出し
        window = y_hist[i : i + q_len]
        next_val = y_hist[i + q_len]
        match_date = ds_hist[i + q_len - 1] # パターン終了日
        
        # 距離計算 (Euclidean Distance)
        dist = np.linalg.norm(window - q_vec)
        
        # 類似度へ変換 (距離0 -> 1.0, 距離大 -> 0.0)
        # 正規化定数はデータのスケールに依存するが、簡易的に 1 / (1 + dist) を使用
        similarity = 1.0 / (1.0 + dist)
        
        results.append({
            "ds": match_date,
            "similarity": similarity,
            "next_val": next_val,
            "window_values": window.tolist()
        })
    
    # 3. 結果の整形とソート
    df_res = pd.DataFrame(results)
    if df_res.empty:
        return df_res
        
    # 類似度が高い順にソート
    df_res = df_res.sort_values("similarity", ascending=False).head(top_k)
    
    return df_res.reset_index(drop=True)