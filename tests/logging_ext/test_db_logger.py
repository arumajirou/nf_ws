from __future__ import annotations

import json
import logging

import pytest

from nf_loto_platform.logging_ext import db_logger


class DummyCursor:
    def __init__(self, fetchone_result=(1,)):
        self.fetchone_result = fetchone_result
        self.executed: list[tuple[str, dict]] = []

    def execute(self, sql: str, params: dict):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyConnection:
    def __init__(self, cursor: DummyCursor):
        self.cursor_obj = cursor
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def make_inputs():
    return dict(
        table_name="nf_loto_hist",
        loto="loto6",
        unique_ids=("N1", "N2"),
        model_name="DeepModel",
        backend="local",
        horizon=12,
        loss="mae",
        metric="mape",
        optimization_config={"lr": 0.1},
        search_space={"lr": [0.1, 0.2]},
        resource_snapshot={"cpu": 50},
        system_info={"python": "3.11"},
    )


def test_log_run_start_inserts_and_returns_db_id(monkeypatch):
    cursor = DummyCursor(fetchone_result=(321,))
    conn = DummyConnection(cursor)
    monkeypatch.setattr(db_logger, "get_connection", lambda: conn)

    run_id = db_logger.log_run_start(**make_inputs())

    assert run_id == 321
    assert conn.commits == 1
    assert len(cursor.executed) == 1
    sql, params = cursor.executed[0]
    assert "INSERT INTO nf_model_runs" in sql
    assert params["unique_ids"] == ["N1", "N2"]
    assert json.loads(params["optimization_config"]) == {"lr": 0.1}
    assert json.loads(params["search_space"]) == {"lr": [0.1, 0.2]}
    assert json.loads(params["resource_summary"]) == {"before": {"cpu": 50}}
    assert json.loads(params["system_info"]) == {"python": "3.11"}


def test_log_run_end_updates_metrics(monkeypatch):
    cursor = DummyCursor()
    conn = DummyConnection(cursor)
    monkeypatch.setattr(db_logger, "get_connection", lambda: conn)

    db_logger.log_run_end(
        run_id=99,
        status="succeeded",
        metrics={"mae": 0.1},
        best_params={"lr": 0.1},
        model_properties={"layers": 3},
        resource_after={"cpu": 60},
        extra_logs="done",
    )

    assert conn.commits == 1
    sql, params = cursor.executed[0]
    assert "UPDATE nf_model_runs SET" in sql
    assert params["run_id"] == 99
    assert json.loads(params["metrics"]) == {"mae": 0.1}
    assert json.loads(params["best_params"]) == {"lr": 0.1}
    assert json.loads(params["model_properties"]) == {"layers": 3}
    assert json.loads(params["resource_after"]) == {"after": {"cpu": 60}}
    assert params["logs"] == "done"


def test_log_run_error_records_traceback(monkeypatch):
    cursor = DummyCursor()
    conn = DummyConnection(cursor)
    monkeypatch.setattr(db_logger, "get_connection", lambda: conn)

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        db_logger.log_run_error(run_id=7, exc=exc)

    assert conn.commits == 1
    sql, params = cursor.executed[0]
    assert "UPDATE nf_model_runs SET" in sql
    assert params["run_id"] == 7
    assert "RuntimeError: boom" in params["error_message"]
    assert "RuntimeError" in params["traceback"]


def test_log_run_start_returns_fallback_id_on_psycopg_error(monkeypatch, caplog):
    class FakePsycopgError(Exception):
        pass

    monkeypatch.setattr(db_logger.psycopg2, "Error", FakePsycopgError, raising=False)

    def failing_conn():
        raise FakePsycopgError("db down")

    monkeypatch.setattr(db_logger, "get_connection", failing_conn)
    monkeypatch.setattr(db_logger.time, "time", lambda: 123.456)

    with caplog.at_level(logging.WARNING):
        run_id = db_logger.log_run_start(**make_inputs())

    assert "Failed to log run start" in caplog.text
    assert run_id == 123456


# To run:
#   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/logging_ext/test_db_logger.py -q
