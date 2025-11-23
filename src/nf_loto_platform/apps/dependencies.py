"""Common dependency factory functions for app entry points."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import psycopg2

from nf_loto_platform.agents.llm_client import BaseLLMClient, EchoLLMClient
from nf_loto_platform.db.db_config import DB_CONFIG
from nf_loto_platform.db.ts_research_store import TSResearchStore
from nf_loto_platform.ml import model_runner as _model_runner


def _resolved_db_config() -> dict[str, Any]:
    if not DB_CONFIG:
        raise RuntimeError("database configuration could not be resolved; configure config/db.yaml first")
    return dict(DB_CONFIG)


def get_db_conn():
    """Return a psycopg2 connection configured for nf_loto_platform apps."""

    return psycopg2.connect(**_resolved_db_config())


@lru_cache(maxsize=1)
def get_ts_research_client() -> TSResearchStore:
    """Return a cached TSResearchStore instance."""

    return TSResearchStore(dsn=_resolved_db_config())


def get_llm_client() -> BaseLLMClient:
    """Return the default BaseLLMClient implementation for app entry points."""

    return EchoLLMClient()


def get_model_runner():
    """Return the nf_loto_platform model_runner module."""

    return _model_runner
