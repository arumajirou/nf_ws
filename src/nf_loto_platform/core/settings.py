"""
アプリケーション全体の設定管理モジュール.
環境変数 (.env) と YAML 設定ファイルの読み込みを一元管理する。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv

# プロジェクトルート (nf_ws/)
# src/nf_loto_platform/core/settings.py -> parents[3] = nf_ws/
BASE_DIR = Path(__file__).resolve().parents[3]

# .env ファイルの読み込み
# これにより、os.getenv() で .env 内の変数を参照可能になる
# override=True にすると、システム環境変数より .env を優先するが、
# 通常はシステム環境変数を優先するためデフォルト (False) のままとする。
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)


def get_config_dir() -> Path:
    """設定ファイルディレクトリ (config/) のパスを返す."""
    return BASE_DIR / "config"


def get_config_path() -> Path:
    """
    設定ファイルディレクトリのパスを返す (互換性維持のためのエイリアス).
    一部のモジュール(easytsf_runnerなど)がこの名前でインポートしているため、
    get_config_dir へのエイリアスとして提供する。
    """
    return get_config_dir()


def load_yaml(path: Path) -> Dict[str, Any]:
    """
    YAMLファイルを安全に読み込む。
    ファイルが存在しない、または解析できない場合は空辞書を返す。
    """
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        # ロガーが未設定の初期段階で呼ばれる可能性があるため print で警告
        print(f"Warning: Failed to load config {path}: {e}")
        return {}


def load_db_config() -> Dict[str, Any]:
    """
    DB接続設定をロードするヘルパー。
    
    1. config/db.yaml (実設定)
    2. config/db.yaml.template (テンプレート/デフォルト)
    
    の順に探索し、最初に見つかった有効なYAMLの内容を返す。
    最終的な値の決定（環境変数での上書き）は db/db_config.py 側で行う想定。
    """
    config_dir = get_config_dir()
    
    # 優先度順にファイル名を定義
    for name in ("db.yaml", "db.yaml.template"):
        cfg_path = config_dir / name
        data = load_yaml(cfg_path)
        if data:
            return data
            
    return {}


def load_agent_config() -> Dict[str, Any]:
    """エージェント設定 (agent_config.yaml) をロードする."""
    return load_yaml(get_config_dir() / "agent_config.yaml")