"""
Pytest configuration and shared fixtures.
"""
import os
import sys
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

# src ディレクトリをパスに追加し、テストから nf_loto_platform をインポート可能にする
BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture(scope="session", autouse=True)
def mock_env_vars() -> Generator[None, None, None]:
    """
    テストセッション全体で環境変数をテスト用に固定する (Auto-use).
    
    これにより、開発者のローカル環境変数や .env ファイルの内容が
    テスト実行時に誤って使用されるのを防ぐ。
    """
    test_env_vars = {
        "NF_ENV": "test",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "nf_test_db",
        "POSTGRES_USER": "test_user",
        "POSTGRES_PASSWORD": "test_password",
        # 外部サービスへの誤接続防止
        "OPENAI_API_KEY": "test_dummy_key",
        "MLFLOW_TRACKING_URI": "sqlite:///:memory:",
        "WANDB_MODE": "disabled",
    }
    
    with patch.dict(os.environ, test_env_vars):
        yield


@pytest.fixture
def mock_db_config(monkeypatch: pytest.MonkeyPatch) -> dict:
    """
    core.settings.load_db_config をモックし、辞書型のダミー設定を返す。
    
    DB接続設定の読み込みロジック自体をスキップしたいユニットテストで使用する。
    """
    test_config = {
        "host": "mock_host",
        "port": 5432,
        "database": "mock_db",
        "user": "mock_user",
        "password": "mock_password",
    }
    
    # settings モジュールの load_db_config を差し替え
    monkeypatch.setattr(
        "nf_loto_platform.core.settings.load_db_config",
        lambda: test_config
    )
    return test_config


@pytest.fixture
def mock_postgres_connection() -> Generator[MagicMock, None, None]:
    """
    psycopg2.connect をモックする。
    
    実際に DB へのネットワーク接続を行わずに、
    Repository や Manager クラスのロジックのみをテストしたい場合に使用する。
    """
    with patch("psycopg2.connect") as mock_connect:
        # コネクションとカーソルのモックを作成
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # コンテキストマネージャ (__enter__, __exit__) の挙動を模倣
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        
        # connect() -> conn
        mock_connect.return_value = mock_conn
        # conn.cursor() -> cursor
        mock_conn.cursor.return_value = mock_cursor
        
        yield mock_connect


@pytest.fixture
def mock_neuralforecast() -> Generator[MagicMock, None, None]:
    """
    NeuralForecast クラスをモックする。
    
    モデルの学習・推論（重い処理）をスキップし、
    パイプラインのフロー確認だけを行いたい場合に使用する。
    """
    with patch("nf_loto_platform.ml.model_runner.NeuralForecast") as mock_nf:
        yield mock_nf