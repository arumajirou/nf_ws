"""
CLI Entry point for running full automation pipeline (legacy wrapper).
This script is intended to be run as a standalone script or via `python -m`.

Usage:
    # Run the automation pipeline (Default)
    python apps/webui_streamlit/nf_auto_runner_full.py
    
    # Initialize the system (Database setup)
    python apps/webui_streamlit/nf_auto_runner_full.py init
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加するハックは廃止し、
# インストールされた nf_loto_platform パッケージを利用する方針に変更。
# 開発環境で `pip install -e .` していれば、以下の import は通るはずである。

try:
    # 既存のパイプライン実行機能
    from nf_loto_platform.pipelines.training_pipeline import run_legacy_nf_auto_runner
    # [追加機能] DBセットアップ機能のインポート
    from nf_loto_platform.db.setup_postgres import setup_database
except ImportError as e:
    # パッケージが見つからない場合の親切なエラーメッセージ
    logging.error("Could not import 'nf_loto_platform'. Ensure the package is installed via 'pip install -e .'")
    raise e

def main():
    # ログ設定 (簡易)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

    # コマンドライン引数の解析
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        # init コマンド: 初期化処理を実行 [追加機能]
        if command == "init":
            logger.info("Starting system initialization (Database Setup)...")
            try:
                # setup_postgres.py の setup_database 関数を呼び出す
                success = setup_database()
                if success:
                    logger.info("System initialization completed successfully.")
                else:
                    logger.error("System initialization failed.")
                    sys.exit(1)
            except Exception as e:
                logger.error(f"Initialization failed with exception: {e}")
                sys.exit(1)
            return
        
        # その他の引数に対するヘルプ表示（オプション）
        elif command in ["--help", "-h", "help"]:
            print(__doc__)
            return

    # 引数がない、または init 以外の場合は既存のオートランナーを実行
    logger.info("Starting legacy NF auto runner via CLI wrapper...")
    
    try:
        run_legacy_nf_auto_runner()
        logger.info("Execution completed successfully.")
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()