"""
CLI Entry point for running full automation pipeline (legacy wrapper).
This script is intended to be run as a standalone script or via `python -m`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加するハックは廃止し、
# インストールされた nf_loto_platform パッケージを利用する方針に変更。
# 開発環境で `pip install -e .` していれば、以下の import は通るはずである。

try:
    from nf_loto_platform.pipelines.training_pipeline import run_legacy_nf_auto_runner
except ImportError as e:
    # パッケージが見つからない場合の親切なエラーメッセージ
    logging.error("Could not import 'nf_loto_platform'. Ensure the package is installed via 'pip install -e .'")
    raise e

if __name__ == "__main__":
    # ログ設定 (簡易)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    logger = logging.getLogger(__name__)
    logger.info("Starting legacy NF auto runner via CLI wrapper...")
    
    try:
        run_legacy_nf_auto_runner()
        logger.info("Execution completed successfully.")
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        sys.exit(1)