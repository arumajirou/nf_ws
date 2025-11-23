"""High level helper for writing experiment data into ts_research.* tables.

This is intentionally conservative: it only uses plain psycopg2 and the
existing DB_CONFIG dictionary so that it can run in the same environments
as the existing nf_model_runs logger.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import psycopg2
from psycopg2.extras import execute_values

from nf_loto_platform.db.db_config import DB_CONFIG
from nf_loto_platform.db_metadata.ts_research_schema import (
    TS_RESEARCH_SCHEMA,
    get_ts_research_ddl,
)


class _SignatureAwareMethod:
    """Descriptor that exposes ``self`` in signatures even when accessed via an instance."""

    def __init__(self, func):
        self._func = func
        params = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        params += list(inspect.signature(func).parameters.values())[1:]
        self._signature = inspect.Signature(params)

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return _BoundSignatureAwareMethod(instance, self._func, self._signature)


class _BoundSignatureAwareMethod:
    """Callable wrapper that injects ``self`` when invoked."""

    def __init__(self, instance, func, signature):
        self._instance = instance
        self._func = func
        self.__signature__ = signature

    def __call__(self, *args, **kwargs):
        return self._func(self._instance, *args, **kwargs)


@dataclass
class ExperimentHandle:
    dataset_id: int
    experiment_id: int


class TSResearchStore:
    """Light‑weight interface to the ts_research experiment schema."""

    def __init__(self, dsn: Optional[Dict[str, Any]] = None) -> None:
        self._dsn = dsn or DB_CONFIG

    def _conn(self):
        return psycopg2.connect(**self._dsn)

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Create ts_research schema and tables if they do not exist.

        Errors are propagated so that callers can decide whether to ignore
        them or fail the whole workflow.
        """
        ddl = get_ts_research_ddl()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    # ------------------------------------------------------------------
    # Dataset / experiment / trial helpers
    # ------------------------------------------------------------------
    def ensure_dataset(
        self,
        schema_name: str,
        table_name: str,
        ts_column: str,
        target_column: str,
        id_columns: Sequence[str],
        freq: str,
        horizon_default: int,
        statistics: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """Insert a dataset row if needed and return its id.

        At the moment we use a simple SELECT/INSERT pair instead of
        upsert with a unique constraint to keep DDL minimal.
        """
        id_cols_json = json.dumps(list(id_columns))
        stats_json = json.dumps(statistics) if statistics is not None else None

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id
                    FROM {TS_RESEARCH_SCHEMA}.datasets
                    WHERE schema_name = %s
                      AND table_name = %s
                      AND ts_column = %s
                      AND target_column = %s
                    """,
                    (schema_name, table_name, ts_column, target_column),
                )
                row = cur.fetchone()
                if row:
                    return int(row[0])

                cur.execute(
                    f"""
                    INSERT INTO {TS_RESEARCH_SCHEMA}.datasets
                    (schema_name, table_name, ts_column, target_column,
                     id_columns, freq, horizon_default, statistics)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        schema_name,
                        table_name,
                        ts_column,
                        target_column,
                        id_cols_json,
                        freq,
                        horizon_default,
                        stats_json,
                    ),
                )
                dataset_id = int(cur.fetchone()[0])
            conn.commit()
        return dataset_id

    def create_experiment(
        self,
        dataset_id: int,
        experiment_name: str,
        objective: str,
        horizon: Optional[int],
        config_json: Optional[Mapping[str, Any]] = None,
        agent_reasoning: Optional[Mapping[str, Any]] = None,
    ) -> int:
        cfg_json = json.dumps(config_json) if config_json is not None else None
        reasoning_json = json.dumps(agent_reasoning) if agent_reasoning is not None else None

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {TS_RESEARCH_SCHEMA}.experiments
                    (dataset_id, experiment_name, objective, horizon,
                     config_json, agent_reasoning, status)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, 'PLANNED')
                    RETURNING id
                    """,
                    (dataset_id, experiment_name, objective, horizon, cfg_json, reasoning_json),
                )
                experiment_id = int(cur.fetchone()[0])
            conn.commit()
        return experiment_id

    def update_experiment_status(self, experiment_id: int, status: str) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {TS_RESEARCH_SCHEMA}.experiments
                    SET status = %s
                    WHERE id = %s
                    """,
                    (status, experiment_id),
                )
            conn.commit()

    def create_trial(
        self,
        experiment_id: int,
        framework: str,
        model_name: str,
        hyperparameters: Mapping[str, Any],
        ensemble_strategy: Optional[str] = None,
        seed: Optional[int] = None,
        status: str = "PENDING",
    ) -> int:
        hparams_json = json.dumps(hyperparameters or {})
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {TS_RESEARCH_SCHEMA}.trials
                    (experiment_id, framework, model_name, hyperparameters,
                     ensemble_strategy, seed, status)
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
                    RETURNING id
                    """ ,
                    (experiment_id, framework, model_name, hparams_json, ensemble_strategy, seed, status),
                )
                trial_id = int(cur.fetchone()[0])
            conn.commit()
        return trial_id

    def update_trial_status(self, trial_id: int, status: str) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {TS_RESEARCH_SCHEMA}.trials
                    SET status = %s, finished_at = NOW()
                    WHERE id = %s
                    """,
                    (status, trial_id),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Metrics / forecasts / resources
    # ------------------------------------------------------------------
    def insert_model_metric(
        self,
        trial_id: int,
        metric_name: str,
        metric_value: float,
        split: str = "val",
        step: Optional[int] = None,
    ) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {TS_RESEARCH_SCHEMA}.model_metrics
                    (trial_id, metric_name, metric_value, split, step)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (trial_id, metric_name, float(metric_value), split, step),
                )
            conn.commit()

    def bulk_insert_forecasts(
        self,
        trial_id: int,
        forecast_rows: Iterable[Mapping[str, Any]],
    ) -> None:
        rows: List[List[Any]] = []
        for r in forecast_rows:
            rows.append(
                [
                    trial_id,
                    r["ts"],
                    r["series_id"],
                    r["point_forecast"],
                    r.get("lower_80"),
                    r.get("upper_80"),
                    r.get("lower_95"),
                    r.get("upper_95"),
                ]
            )
        if not rows:
            return

        with self._conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    f"""
                    INSERT INTO {TS_RESEARCH_SCHEMA}.forecasts
                    (trial_id, ts, series_id, point_forecast,
                     lower_80, upper_80, lower_95, upper_95)
                    VALUES %s
                    """,
                    rows,
                )
            conn.commit()

    def bulk_insert_resource_logs(
        self,
        trial_id: int,
        resource_rows: Iterable[Mapping[str, Any]],
    ) -> None:
        rows: List[List[Any]] = []
        for r in resource_rows:
            rows.append(
                [
                    trial_id,
                    r["timestamp"],
                    r.get("cpu_percent", 0.0),
                    r.get("memory_used_mb", 0.0),
                    r.get("gpu_utilization"),
                    r.get("gpu_memory_mb"),
                    r.get("disk_io_read_mb"),
                    r.get("disk_io_write_mb"),
                ]
            )
        if not rows:
            return

        with self._conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    f"""
                    INSERT INTO {TS_RESEARCH_SCHEMA}.resource_logs
                    (trial_id, timestamp, cpu_percent, memory_used_mb,
                     gpu_utilization, gpu_memory_mb,
                     disk_io_read_mb, disk_io_write_mb)
                    VALUES %s
                    """ ,
                    rows,
                )
            conn.commit()

    def insert_causal_graph(
        self,
        dataset_id: int,
        algorithm: str,
        graph_json: Mapping[str, Any],
        adjacency_matrix: Optional[Mapping[str, Any]] = None,
        interpretation: Optional[str] = None,
    ) -> int:
        """Insert one causal graph row and return its id."""  # noqa: D401
        graph_json_str = json.dumps(graph_json)
        adj_json_str = json.dumps(adjacency_matrix) if adjacency_matrix is not None else None

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {TS_RESEARCH_SCHEMA}.causal_graphs
                    (dataset_id, algorithm, graph_json, adjacency_matrix, interpretation)
                    VALUES (%s, %s, %s::jsonb, %s::jsonb, %s)
                    RETURNING id
                    """,
                    (dataset_id, algorithm, graph_json_str, adj_json_str, interpretation),
                )
                row_id = int(cur.fetchone()[0])
            conn.commit()
        return row_id

    def bulk_insert_anomalies(
        self,
        dataset_id: int,
        anomaly_rows: Iterable[Mapping[str, Any]],
        trial_id: Optional[int] = None,
    ) -> None:
        """Insert multiple anomaly records into ts_research.anomalies."""  # noqa: D401
        rows: List[List[Any]] = []
        for r in anomaly_rows:
            rows.append(
                [
                    trial_id,
                    dataset_id,
                    r["ts"],
                    r["series_id"],
                    r["score"],
                    r["is_anomaly"],
                    r["method"],
                ]
            )
        if not rows:
            return

        with self._conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    f"""
                    INSERT INTO {TS_RESEARCH_SCHEMA}.anomalies
                    (trial_id, dataset_id, ts, series_id, score, is_anomaly, method)
                    VALUES %s
                    """,
                    rows,
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Query helpers for analysis / LLM agents
    # ------------------------------------------------------------------
    @_SignatureAwareMethod
    def get_metrics_for_experiment(self, experiment_id: int) -> Sequence[Mapping[str, Any]]:
        """Fetch aggregated metrics rows for a given experiment.

        This helper is intentionally simple and conservative:
        - It only exposes the raw columns from ``model_metrics`` joined with ``trials``.
        - It returns a list of dict rows so that callers (including LLM agents) can
          decide how深く解析するかを後段で制御できるようにしている。
        """
        query = f"""
            SELECT
                mm.trial_id,
                t.framework,
                t.model_name,
                mm.metric_name,
                mm.metric_value,
                mm.split,
                mm.step
            FROM {TS_RESEARCH_SCHEMA}.model_metrics AS mm
            JOIN {TS_RESEARCH_SCHEMA}.trials AS t
              ON mm.trial_id = t.id
            WHERE t.experiment_id = %s
            ORDER BY mm.metric_name, mm.split, mm.step NULLS FIRST, mm.trial_id
        """
        rows: list[dict[str, Any]] = []
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (experiment_id,))
                colnames = [c.name for c in cur.description]  # type: ignore[attr-defined]
                for rec in cur.fetchall():
                    row = {name: value for name, value in zip(colnames, rec)}
                    rows.append(row)
        return rows
