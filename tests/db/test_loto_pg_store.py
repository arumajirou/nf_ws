from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Sequence

import pandas as pd
import pytest

from nf_loto_platform.db import loto_pg_store


@dataclass
class DummyCursor:
    fetchall_result: Sequence[tuple[str, str]] = field(default_factory=list)
    executed: List[tuple[str, Any]] = field(default_factory=list)

    def execute(self, sql: str, params: tuple[Any, ...] | None = None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self.fetchall_result)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@dataclass
class DummyConnection:
    cursor_obj: DummyCursor
    commits: int = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_ensure_table_executes_create_sql(monkeypatch):
    cursor = DummyCursor()
    conn = DummyConnection(cursor)
    monkeypatch.setattr(loto_pg_store, "_connect", lambda: conn)

    loto_pg_store.ensure_table()

    assert cursor.executed[0][0].strip().startswith("CREATE TABLE IF NOT EXISTS")
    assert conn.commits == 1


def test_migrate_to_bigint_alters_integer_columns(monkeypatch):
    cursor = DummyCursor(
        fetchall_result=[
            ("y", "integer"),
            ("ds", "timestamp without time zone"),
            ("custom_col", "integer"),
            ("loto", "text"),
        ]
    )
    conn = DummyConnection(cursor)
    monkeypatch.setattr(loto_pg_store, "_connect", lambda: conn)

    loto_pg_store.migrate_to_bigint()

    # 1 回目は情報スキーマ、2 回目以降が ALTER
    assert "ALTER TABLE" in cursor.executed[1][0]
    assert "ALTER TABLE" in cursor.executed[2][0]
    assert conn.commits == 1


def test_prepare_rows_fills_missing_columns_and_casts_types():
    df = pd.DataFrame(
        {
            "loto": ["loto6"],
            "num": [1],
            "ds": ["2024-01-01"],
            "unique_id": ["N1"],
            "y": ["7"],
        }
    )

    rows = loto_pg_store._prepare_rows(df)
    assert len(rows) == 1
    row = rows[0]
    assert row[:4] == ("loto6", 1, pd.Timestamp("2024-01-01"), "N1")
    assert isinstance(row[4], int)
    # 追加列はゼロ埋めされる
    assert all(value in (0, None) for value in row[5:])


def test_upsert_df_batches_and_calls_execute_values(monkeypatch):
    cursor = DummyCursor()
    conn = DummyConnection(cursor)
    monkeypatch.setattr(loto_pg_store, "_connect", lambda: conn)
    monkeypatch.setattr(loto_pg_store, "ensure_table", lambda: None)
    monkeypatch.setattr(loto_pg_store, "migrate_to_bigint", lambda: None)

    calls: list[dict[str, Any]] = []

    def fake_execute_values(cur, sql, chunk, page_size=None):
        calls.append({"cursor": cur, "sql": sql, "chunk": chunk, "page_size": page_size})

    monkeypatch.setattr(loto_pg_store, "execute_values", fake_execute_values)

    df = pd.DataFrame(
        {
            "loto": ["loto6", "loto6", "loto6"],
            "num": [1, 2, 3],
            "ds": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "unique_id": ["N1", "N1", "N1"],
            "y": [10, 11, 12],
            "CO": [0, 0, 0],
        }
    )

    inserted = loto_pg_store.upsert_df(df, batch_size=2)

    assert inserted == 3
    assert len(calls) == 2  # 2 件 + 1 件で 2 バッチ
    assert calls[0]["page_size"] == 2
    assert calls[1]["page_size"] == 1
    assert calls[0]["cursor"] is cursor
