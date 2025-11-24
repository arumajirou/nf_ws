"""PostgreSQL 接続設定を一元的に管理するモジュール."""

from __future__ import annotations

import os
from typing import Any, Dict

# 設定ファイル読み込み用モジュール (src/nf_loto_platform/core/settings.py)
# 循環インポート回避やファイル不在時のためにtry-exceptで保護する場合もありますが、
# 基本的にはプロジェクト構成に従いインポートします。
try:
    from nf_loto_platform.core.settings import load_db_config
except ImportError:
    # ユニットテストや単体実行時など、パッケージ解決ができない場合のダミー
    def load_db_config():
        return {}

def _resolve_db_config() -> Dict[str, Any]:
    """
    DB接続設定を解決する。
    
    優先順位:
    1. 環境変数 (os.getenv)
    2. YAML設定ファイル (config/db.yaml)
    3. デフォルト値 (ローカル開発用フォールバック)
    
    Returns:
        Dict[str, Any]: psycopg2等に渡せる接続設定辞書
    """
    # 1. YAMLからロード (存在しなければ空辞書)
    yaml_config = load_db_config() or {}
    if not isinstance(yaml_config, dict):
        yaml_config = {}

    # 2. 環境変数を優先して構築 (Env > YAML > Default)
    host = os.getenv("POSTGRES_HOST", yaml_config.get("host", "127.0.0.1"))
    
    # portは整数型に変換
    port_env = os.getenv("POSTGRES_PORT")
    if port_env:
        port = int(port_env)
    else:
        port = int(yaml_config.get("port", 5432))

    dbname = os.getenv("POSTGRES_DB", yaml_config.get("database", "postgres"))
    user = os.getenv("POSTGRES_USER", yaml_config.get("user", "postgres"))
    
    # パスワード: 環境変数 -> YAML -> デフォルト('postgres')
    # ※ 本番環境では必ず環境変数またはYAMLで指定することを推奨
    password = os.getenv("POSTGRES_PASSWORD", yaml_config.get("password", "postgres"))

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