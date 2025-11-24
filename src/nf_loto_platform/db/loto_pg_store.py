# df_final を PostgreSQL に保存し、高速な CRUD を提供するモジュール
from typing import Optional, List, Tuple
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime
from .db_config import DB_CONFIG, TABLE_PREFIX

TABLE_NAME = f"{TABLE_PREFIX}loto_final"

COLS = [
    "loto", "num", "ds", "unique_id", "y", "CO",
    "N1NU","N1PM","N2NU","N2PM","N3NU","N3PM",
    "N4NU","N4PM","N5NU","N5PM","N6NU","N6PM","N7NU","N7PM",
]

# Note: DDL (CREATE TABLE) は sql/005_create_loto_data_tables.sql に移動しました。
# アプリケーションコード内でのスキーマ変更は行いません。

UPSERT_SQL_TEMPLATE = f"""
    INSERT INTO {TABLE_NAME} ({', '.join(COLS)})
    VALUES %s
    ON CONFLICT (loto, num, unique_id) DO UPDATE SET
        ds = EXCLUDED.ds,
        y  = EXCLUDED.y,
        CO = EXCLUDED.CO,
        {', '.join([f"{c} = EXCLUDED.{c}" for c in COLS[6:]])}
"""

def _connect():
    return psycopg2.connect(**DB_CONFIG)

def _prepare_rows(df: pd.DataFrame) -> List[Tuple]:
    data = df.copy()

    # 欠損列は作成（ゼロ埋め対象）
    for c in COLS:
        if c not in data.columns:
            data[c] = 0 if c != "ds" else None

    # 数値列は 0 埋めして int64 化（dsは別処理）
    for c in COLS:
        if c in ("loto", "unique_id", "ds"):
            continue
        data[c] = pd.to_numeric(data[c], errors="coerce").fillna(0).astype("int64")

    data["ds"] = pd.to_datetime(data["ds"], errors="coerce")
    data = data[COLS].astype(object)
    data = data.where(pd.notna(data), None)

    return [tuple(row[c] for c in COLS) for _, row in data.iterrows()]

def upsert_df(df: pd.DataFrame, batch_size: int = 5000):
    """
    データフレームをDBにUPSERTする。
    
    Note:
        テーブルが存在しない場合、この関数は psycopg2.errors.UndefinedTable エラーを発生させます。
        事前に sql/ ディレクトリ内のマイグレーションスクリプトを実行してください。
    """
    if df.empty:
        return 0
    
    # DDL操作（ensure_table, migrate_to_bigint）は廃止
    
    rows = _prepare_rows(df)
    with _connect() as conn, conn.cursor() as cur:
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i+batch_size]
            execute_values(cur, UPSERT_SQL_TEMPLATE, chunk, page_size=len(chunk))
        conn.commit()
    return len(rows)

# ======= CRUD ヘルパ =======
def read_by_loto_num(loto: str, num: int) -> list[dict]:
    sql = f"""
    SELECT {', '.join(COLS)} FROM {TABLE_NAME}
    WHERE loto = %s AND num = %s
    ORDER BY unique_id
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (loto, num))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

def read_by_date_range(loto: Optional[str], start: datetime, end: datetime) -> list[dict]:
    if loto:
        sql = f"""
        SELECT {', '.join(COLS)} FROM {TABLE_NAME}
        WHERE loto = %s AND ds >= %s AND ds < %s
        ORDER BY ds, num, unique_id
        """
        params = (loto, start, end)
    else:
        sql = f"""
        SELECT {', '.join(COLS)} FROM {TABLE_NAME}
        WHERE ds >= %s AND ds < %s
        ORDER BY loto, ds, num, unique_id
        """
        params = (start, end)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

def update_y(loto: str, num: int, unique_id: str, y: Optional[int]) -> int:
    sql = f"""
    UPDATE {TABLE_NAME}
    SET y = %s
    WHERE loto = %s AND num = %s AND unique_id = %s
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (y, loto, num, unique_id))
        conn.commit()
        return cur.rowcount

def delete_by_loto_num(loto: str, num: int) -> int:
    sql = f"""DELETE FROM {TABLE_NAME} WHERE loto = %s AND num = %s"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (loto, num))
        affected = cur.rowcount
        conn.commit()
        return affected

if __name__ == "__main__":
    from .loto_etl import build_df_final
    df_final = build_df_final()
    n = upsert_df(df_final, batch_size=5000)
    print(f"UPSERT行数: {n}")