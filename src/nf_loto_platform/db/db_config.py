"""PostgreSQL 接続設定を一元的に管理するモジュール."""

from __future__ import annotations

import os
from typing import Any, Dict

# 設定ファイル読み込み用モジュール
try:
    from nf_loto_platform.core.settings import load_db_config
except ImportError:
    def load_db_config():
        return {}

def _resolve_db_config() -> Dict[str, Any]:
    """
    DB接続設定を解決する。
    
    優先順位:
    1. 環境変数 (os.getenv) - 最優先
    2. YAML設定ファイル (config/db.yaml)
    
    Note:
        セキュリティのため、パスワードのデフォルト値は設定しません。
        必ず環境変数 POSTGRES_PASSWORD または YAMLファイルで指定してください。
    
    Returns:
        Dict[str, Any]: psycopg2等に渡せる接続設定辞書
    """
    # 1. YAMLからロード
    yaml_config = load_db_config() or {}
    if not isinstance(yaml_config, dict):
        yaml_config = {}

    # 2. 環境変数を優先して構築
    # ホスト: Env -> YAML -> Default(localhost)
    host = os.getenv("POSTGRES_HOST", yaml_config.get("host", "127.0.0.1"))
    
    # ポート: Env -> YAML -> Default(5432)
    port_env = os.getenv("POSTGRES_PORT")
    if port_env:
        port = int(port_env)
    else:
        port = int(yaml_config.get("port", 5432))

    # DB名/ユーザー: Env -> YAML -> Default(postgres)
    dbname = os.getenv("POSTGRES_DB", yaml_config.get("database", "postgres"))
    user = os.getenv("POSTGRES_USER", yaml_config.get("user", "postgres"))
    
    # パスワード: Env -> YAML -> None
    # ハードコードされたデフォルト値は削除
    password = os.getenv("POSTGRES_PASSWORD", yaml_config.get("password"))
    
    if password is None:
        # 接続時にエラーになるが、セキュリティ上明示的なエラーの方が安全
        pass 

    return {
        "host": host,
        "port": port,
        "database": dbname,
        "user": user,
        "password": password,
    }

# アプリケーション全体で参照されるDB設定定数
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