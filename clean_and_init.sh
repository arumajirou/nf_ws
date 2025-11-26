#!/bin/bash

# スクリプトの実行時にエラーが発生しても続行する設定（削除対象がない場合などを考慮）
set +e

echo "========================================================"
echo "  Starting Project Cleanup and Initialization"
echo "========================================================"

# 1. Pythonキャッシュの削除
echo "[1/5] Cleaning Python cache files..."
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type f -name "*.pyc" -delete
find . -type f -name "*.pyo" -delete
find . -type f -name "*.pyd" -delete
echo "  -> Cache cleared."

# 2. 実行ログ・アーティファクトの削除 (ディレクトリ自体は残し、中身を空にする)
echo "[2/5] Cleaning artifacts and logs..."
rm -rf lightning_logs/*
rm -rf artifacts/*
# reportディレクトリはREADME.mdなどのドキュメントを残して、自動生成ファイルのみ消すのが安全
find report -type f -name "*.html" -delete
find report -type f -name "*.log" -delete
# 自動生成された履歴や監査レポートも削除対象とする場合
rm -f report/repository_audit.md
rm -f report/change_history_*.md
echo "  -> Logs and artifacts cleared."

# 3. ビルドアーティファクトの削除
echo "[3/5] Cleaning build artifacts..."
rm -rf src/*.egg-info
rm -rf build/
rm -rf dist/
rm -f *.zip
echo "  -> Build artifacts cleared."

# 4. 設定ファイルの初期化 (テンプレートからコピー)
echo "[4/5] Initializing config files from templates..."

# db.yaml.template -> db.yaml
if [ -f "config/db.yaml.template" ] && [ ! -f "config/db.yaml" ]; then
    cp config/db.yaml.template config/db.yaml
    echo "  -> Created config/db.yaml from template."
else
    echo "  -> config/db.yaml already exists or template missing. Skipped."
fi

# loto_db_config.yaml.template -> loto_db_config.yaml
if [ -f "config/loto_db_config.yaml.template" ] && [ ! -f "config/loto_db_config.yaml" ]; then
    cp config/loto_db_config.yaml.template config/loto_db_config.yaml
    echo "  -> Created config/loto_db_config.yaml from template."
else
    echo "  -> config/loto_db_config.yaml already exists or template missing. Skipped."
fi

# 5. ディレクトリ構造の再確保 (削除しすぎた場合の保険)
echo "[5/5] Ensuring directory structure..."
mkdir -p artifacts
mkdir -p lightning_logs
mkdir -p report
mkdir -p notebooks

echo "========================================================"
echo "  Cleanup and Initialization Complete!"
echo "========================================================"