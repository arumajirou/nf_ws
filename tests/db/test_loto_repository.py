from __future__ import annotations

import pandas as pd
import pytest

from nf_loto_platform.db import loto_repository


class DummyConnection:
    """with 文で利用するだけの簡易接続オブジェクト。"""

    def __init__(self):
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited = True
        return False


@pytest.fixture
def stub_connection(monkeypatch):
    """psycopg2 接続を差し替えて、読み取り専用のテストを行う。"""

    conn = DummyConnection()
    monkeypatch.setattr(loto_repository, "get_connection", lambda: conn)
    return conn


@pytest.mark.parametrize("table_name", ["nf_loto_hist", "Tbl123", "tbl_456"])
def test_validate_table_name_accepts_safe_inputs(table_name):
    """英数字とアンダースコアだけならそのまま通す。"""

    assert loto_repository._validate_table_name(table_name) == table_name


@pytest.mark.parametrize("table_name", ["nf-loto", "nf loto", "nf;drop"])
def test_validate_table_name_rejects_invalid_inputs(table_name):
    """危険な文字を含む名前は ValueError になる。"""

    with pytest.raises(ValueError):
        loto_repository._validate_table_name(table_name)


def test_list_loto_tables_queries_catalog(monkeypatch, stub_connection):
    """nf_loto% テーブル一覧を取得する SQL を実行する。"""

    captured = {}

    def fake_read_sql(query, conn):
        captured["query"] = query
        captured["conn"] = conn
        return pd.DataFrame({"tablename": ["nf_loto_hist"]})

    monkeypatch.setattr(loto_repository.pd, "read_sql", fake_read_sql)

    df = loto_repository.list_loto_tables()

    assert "nf_loto%" in captured["query"]
    assert captured["conn"] is stub_connection
    assert df["tablename"].tolist() == ["nf_loto_hist"]


def test_list_loto_values_executes_select(monkeypatch, stub_connection):
    """テーブル名をホワイトリスト検証したうえで SELECT を実行する。"""

    captured = {}

    def fake_read_sql(query, conn):
        captured["query"] = query
        captured["conn"] = conn
        return pd.DataFrame({"loto": ["loto6"]})

    monkeypatch.setattr(loto_repository.pd, "read_sql", fake_read_sql)

    df = loto_repository.list_loto_values("nf_loto_hist")

    assert "FROM nf_loto_hist" in captured["query"]
    assert "ORDER BY loto" in captured["query"]
    assert df["loto"].tolist() == ["loto6"]


def test_list_loto_values_invalid_table_name():
    """不正なテーブル名は list_loto_values でも拒否される。"""

    with pytest.raises(ValueError):
        loto_repository.list_loto_values("nf-loto_hist")


def test_list_unique_ids_uses_parameterized_query(monkeypatch, stub_connection):
    """loto を %s プレースホルダでバインドし、unique_id を返す。"""

    captured = {}

    def fake_read_sql(query, conn, params):
        captured["query"] = query
        captured["conn"] = conn
        captured["params"] = params
        return pd.DataFrame({"unique_id": ["N1", "N2"]})

    monkeypatch.setattr(loto_repository.pd, "read_sql", fake_read_sql)

    df = loto_repository.list_unique_ids("nf_loto_hist", "loto6")

    assert "WHERE loto = %s" in captured["query"]
    assert captured["params"] == ["loto6"]
    assert df["unique_id"].tolist() == ["N1", "N2"]


def test_load_panel_by_loto_rejects_empty_unique_ids():
    """unique_id を 1 件も指定しない場合は即座に例外となる。"""

    with pytest.raises(ValueError):
        loto_repository.load_panel_by_loto("nf_loto_hist", "loto6", [])


def test_load_panel_by_loto_executes_in_query(monkeypatch, stub_connection):
    """unique_id の数に応じて IN 句のプレースホルダを生成する。"""

    captured = {}
    fake_df = pd.DataFrame(
        {
            "unique_id": ["N1", "N1"],
            "ds": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "y": [1.0, 2.0],
            "hist_dummy": [0, 0],
        }
    )

    def fake_read_sql(query, conn, params):
        captured["query"] = query
        captured["conn"] = conn
        captured["params"] = params
        return fake_df

    monkeypatch.setattr(loto_repository.pd, "read_sql", fake_read_sql)

    result = loto_repository.load_panel_by_loto("nf_loto_hist", "loto6", ["N1", "N2"])

    assert captured["conn"] is stub_connection
    assert captured["query"].count("%s") == 3  # 1 (loto) + 2 unique_id
    assert "ORDER BY unique_id, ds" in captured["query"]
    assert captured["params"] == ["loto6", "N1", "N2"]
    pd.testing.assert_frame_equal(result, fake_df)


def test_load_panel_by_loto_detects_missing_columns(monkeypatch, stub_connection):
    """必須カラムが欠けている場合は ValueError を送出する。"""

    def fake_read_sql(query, conn, params):
        return pd.DataFrame(
            {
                "unique_id": ["N1"],
                "ds": pd.to_datetime(["2024-01-01"]),
            }
        )

    monkeypatch.setattr(loto_repository.pd, "read_sql", fake_read_sql)

    with pytest.raises(ValueError, match="必要なカラム"):
        loto_repository.load_panel_by_loto("nf_loto_hist", "loto6", ["N1"])


# To run:
#   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/db/test_loto_repository.py -q
