"""PostgreSQL 接続設定を一元的に管理するモジュール."""

from __future__ import annotations

from typing import Any, Dict

from nf_loto_platform.core.settings import load_db_config

# config/db.yaml (or *.template) から読み込めなかった場合にだけ使うフォールバック。
_FALLBACK_DB_CONFIG: Dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 5432,
    "database": "postgres",
    "user": "postgres",
    "password": "postgres",
}


def _resolve_db_config() -> Dict[str, Any]:
    """config/db.yaml -> template -> フォールバックの順に設定を解決する."""
    loaded = load_db_config()
    if loaded:
        return dict(loaded)
    return dict(_FALLBACK_DB_CONFIG)


# 呼び出し側との互換性を維持するため、従来どおり定数を公開する。
DB_CONFIG: Dict[str, Any] = _resolve_db_config()

# テーブル名のプレフィックス
TABLE_PREFIX = 'nf_'

# カラム名の最大長
MAX_COLUMN_NAME_LENGTH = 63

# PostgreSQLデータ型マッピング
PYTHON_TO_PG_TYPE = {
    int: 'INTEGER',
    float: 'REAL',
    str: 'TEXT',
    bool: 'BOOLEAN',
    list: 'JSONB',
    dict: 'JSONB',
    type(None): 'TEXT',
}
