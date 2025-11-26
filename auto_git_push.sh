#!/usr/bin/env bash
set -euo pipefail

# yyyymmddhhmmss 形式の実行日時
timestamp=$(date '+%Y%m%d%H%M%S')
msg="実行日時${timestamp}"

# 変更がなければ何もしないで終了（必要なければこのブロック削ってOK）
if git diff --quiet && git diff --cached --quiet; then
  echo "コミットする変更がありません。処理を終了します。"
  exit 0
fi

echo "git add ."
git add .

echo "git commit -m \"${msg}\""
git commit -m "${msg}"

# デフォルトの upstream ブランチに push
# 特定のブランチに固定したい場合は `git push origin main` などに書き換え
echo "git push"
git push
