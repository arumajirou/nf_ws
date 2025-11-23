-- DDL for ts_research experiment schema
-- Auto-generated from nf_loto_platform.db_metadata.ts_research_schema

BEGIN;

CREATE SCHEMA IF NOT EXISTS ts_research;

CREATE TABLE IF NOT EXISTS ts_research.datasets (
    id              SERIAL PRIMARY KEY,
    schema_name     TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    ts_column       TEXT NOT NULL,
    target_column   TEXT NOT NULL,
    id_columns      JSONB NOT NULL,
    freq            TEXT NOT NULL,
    horizon_default INT  NOT NULL,
    statistics      JSONB,
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ts_research.experiments (
    id              SERIAL PRIMARY KEY,
    dataset_id      INT NOT NULL REFERENCES ts_research.datasets(id),
    experiment_name TEXT NOT NULL,
    objective       TEXT NOT NULL,
    horizon         INT,
    config_json     JSONB,
    agent_reasoning JSONB,
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    status          TEXT NOT NULL DEFAULT 'PLANNED'
);

CREATE TABLE IF NOT EXISTS ts_research.trials (
    id                SERIAL PRIMARY KEY,
    experiment_id     INT NOT NULL REFERENCES ts_research.experiments(id),
    framework         TEXT NOT NULL,
    model_name        TEXT NOT NULL,
    hyperparameters   JSONB NOT NULL,
    ensemble_strategy TEXT,
    seed              INT,
    status            TEXT NOT NULL DEFAULT 'PENDING',
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ts_research.model_metrics (
    id           SERIAL PRIMARY KEY,
    trial_id     INT NOT NULL REFERENCES ts_research.trials(id),
    metric_name  TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    split        TEXT NOT NULL,
    step         INT
);

CREATE TABLE IF NOT EXISTS ts_research.forecasts (
    id             SERIAL PRIMARY KEY,
    trial_id       INT NOT NULL REFERENCES ts_research.trials(id),
    ts             TIMESTAMPTZ NOT NULL,
    series_id      TEXT NOT NULL,
    point_forecast DOUBLE PRECISION NOT NULL,
    lower_80       DOUBLE PRECISION,
    upper_80       DOUBLE PRECISION,
    lower_95       DOUBLE PRECISION,
    upper_95       DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS ts_research.resource_logs (
    id              SERIAL PRIMARY KEY,
    trial_id        INT NOT NULL REFERENCES ts_research.trials(id),
    timestamp       TIMESTAMPTZ NOT NULL,
    cpu_percent     DOUBLE PRECISION NOT NULL,
    memory_used_mb  DOUBLE PRECISION NOT NULL,
    gpu_utilization DOUBLE PRECISION,
    gpu_memory_mb   DOUBLE PRECISION,
    disk_io_read_mb DOUBLE PRECISION,
    disk_io_write_mb DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS ts_research.causal_graphs (
    id               SERIAL PRIMARY KEY,
    dataset_id       INT NOT NULL REFERENCES ts_research.datasets(id),
    algorithm        TEXT NOT NULL,
    graph_json       JSONB NOT NULL,
    adjacency_matrix JSONB,
    interpretation   TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ts_research.anomalies (
    id          SERIAL PRIMARY KEY,
    trial_id    INT REFERENCES ts_research.trials(id),
    dataset_id  INT NOT NULL REFERENCES ts_research.datasets(id),
    ts          TIMESTAMPTZ NOT NULL,
    series_id   TEXT NOT NULL,
    score       DOUBLE PRECISION NOT NULL,
    is_anomaly  BOOLEAN NOT NULL,
    method      TEXT NOT NULL
);

COMMIT;
