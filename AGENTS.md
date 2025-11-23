# Repository Guidelines

## Project Structure & Module Organization

`src/nf_loto_platform/` hosts the package: `core/` (settings/exceptions), `db/` (ETL and Postgres access), `features/` (futr/hist/stat builders), `ml/` (AutoML runners), plus logging, monitoring, reporting, and metadata helpers. Keep runtime code here so imports remain `nf_loto_platform.<module>`. `apps/webui_streamlit/` holds the two entry points (`streamlit_app.py` and the `nf_auto_runner_full.py` wrapper). Environment YAML lives in `config/`, reference docs and SQL templates live in `docs/` and `sql/`, and experiments/assets reside in `notebooks/` and `artifacts/`. Tests mirror the package layout; see `tests/TEST_PLAN.md` and `tests/TEST_MATRIX.md`.

## Build, Test, and Development Commands

- `python -m venv .venv && source .venv/bin/activate`
- `python -m pip install -e .[dev]`
- `streamlit run apps/webui_streamlit/streamlit_app.py --server.port 8501`
- `python apps/webui_streamlit/nf_auto_runner_full.py`
- `pytest -m "not (integration or e2e or nonfunctional)"` (add `pytest tests/e2e`, `pytest -m integration`, or `pytest --cov=src/nf_loto_platform --cov-report=term-missing` as needed)

## Coding Style & Naming Conventions

Adopt Black-formatted, 4-space-indented Python with imports sorted by isort and type hints strict enough for `mypy src/nf_loto_platform`. Modules and files use `snake_case`, classes use `PascalCase`, and functions carry action verbs (`build_hist_features`, `run_legacy_nf_auto_runner`). Use structured logging (`logger.info("stage", extra={...})`) so Prometheus, MLflow, and Streamlit diagnostics can parse metadata consistently.

## Testing Guidelines

Choose the right layer before writing tests: module-level checks live under the mirrored directories (`tests/db`, `tests/features`, etc.), while `tests/integration`, `tests/e2e`, and `tests/nonfunctional` host cross-module, UI, and reproducibility suites. Stick to the `test_<unit>.py` pattern plus fixtures in `tests/conftest.py`, and update the scenario list in `tests/TEST_PLAN.md` whenever you introduce a new contract or configuration key. Run the default marker set locally and schedule the slower suites before merging or whenever you touch shared pipelines.

## Commit & Pull Request Guidelines

Keep commits in the repo’s established style: single-line, imperative subjects such as `Add time-series MLOps utilities`, each focused on one concern. PRs should explain intent, describe schema/config changes, list the commands executed (formatters, pytest, pipeline or Streamlit smoke), and attach screenshots or logs when UI or monitoring code changes. Link issues, experiments, or notebooks that produced artifacts so reviewers can trace provenance.

## Security & Configuration Tips

Never commit filled `config/db.yaml`, `.env`, or credential dumps; rely on the `.template` files plus local environment variables for MLflow, Weights & Biases, and Postgres secrets. Keep bulky outputs under `artifacts/` or `report/` and extend `.gitignore` if the files contain identifiers. For any new SQL under `sql/`, validate against a scratch schema before pointing it at production datasets and document the requirement in your PR.

---

## Agent Behavior (Codex rules)

These rules describe how the Codex agent should behave **in this repository**.

### General principles

- Treat short, imperative English commands as actions to execute in this repo, not as free-form chat.
- Prefer running **read-only** commands (e.g. `git`, `pytest`, `ls`, `cat`) automatically when they are obviously safe.
- For destructive or risky actions (e.g. `rm -rf`, schema changes), ask for confirmation.

All summaries or explanations requested by the user should be written in **Japanese**.

---

### Command: `Summarize recent commits`

When the user types **exactly**:

```text
Summarize recent commits
```

on its own line, you MUST behave as follows:

1. **Immediately run read-only git commands** in this repository, without asking for confirmation:

   - Required:

     - `git log --oneline --decorate -n 20`
   - Optional (run when useful):

     - `git status`
     - `git show -1`
     - `git diff HEAD~3..HEAD --stat`
2. **Generate a summary in Japanese** based on the above commands:

   - Use bullet points.
   - Explain:
     - 何がどのように変更されたか
     - どのディレクトリ／ファイルが主に触られているか
     - リファクタ・テスト追加・設定変更・ドキュメント更新などのパターンがあれば一言で触れる
3. **Output constraints (no chatter, no planning)**

   - 出力は **コミット要約だけ** にする。
   - 次のようなテキストは出力してはいけない:
     - 「了解しました」「これから git log を実行します」などの確認メッセージ
     - 「I will do X now」などのプランニング文
     - 実行したコマンド一覧の説明（必要な場合を除き、結果の要約だけを書く）
   - 応答は、いきなり箇条書き（`- ...`）から始めること。
4. **Approval rules**

   - `Summarize recent commits` をトリガーとして以下のコマンドを実行することは、常に事前承認されている:
     - `git log`
     - `git status`
     - `git show`
     - `git diff`（`--stat` 付きの読み取り専用）
   - これらのコマンドを実行するときに、ユーザへ追加の確認質問をしてはいけない。
5. **Repeatable trigger**

   - このセッション中、`Summarize recent commits` が再度入力された場合も、同じ挙動を繰り返す。
   - 毎回同様に:
     - git コマンド実行
     - 日本語の箇条書きによる要約のみを出力
       を行う。

---

### Other short commands (future extension)

If the user later defines similar patterns (e.g. `Run unit tests`, `Show failing tests`), treat them in the same style:

- 自動で安全なコマンドを実行
- プランニングや「了解しました」を挟まず
- 結果の要約だけを日本語で返す

When in doubt for **new** commands not documented here, ask for clarification once, then extend this file (AGENTS.md) to encode the new behavior.



## Testing Agent: `Write tests for <file>`

### トリガー条件

ユーザーがこのリポジトリで、次の形式の行を入力したとき:

  Write tests for <path/to/file.py>

ここで `<path/to/file.py>` は、このリポジトリ内の Python ファイルへのパスとする。

### 期待する挙動

1. 対象ファイルの解析

   - 指定された Python ファイルを開き、**外部に公開される関数・クラス**（モジュールレベル関数、公開メソッドなど）を特定する。
   - それぞれについて、引数・戻り値・主要な分岐・例外などをざっと把握する。
2. 対応する pytest テストファイルの作成/更新

   - `src/` 配下のファイルであれば、パッケージ構造をミラーする `tests/` モジュールを作成する。
     - 例:
       - `src/nf_loto_platform/features/loto_etl.py`
         → `tests/features/test_loto_etl.py`
       - `src/nf_loto_platform/db/loto_pg_store.py`
         → `tests/db/test_loto_pg_store.py`
       - `src/nf_loto_platform/ml/model_runner.py`
         → `tests/ml/test_model_runner.py`
   - 既存の `tests/` ディレクトリのスタイル（pytest ベースの関数/フィクスチャ）に合わせる。
   - ファイルがすでに存在する場合は、重複を避けつつテストケースを拡張・整理する。
3. テスト設計方針（共通ルール）

   - **外部依存は必ずモック/スタブ化**する:
     - 実際の PostgreSQL/ネットワーク/ファイルシステムなどに接続しない。
     - DB 接続やカーソルは `unittest.mock` や pytest のフィクスチャで差し替える。
   - テスト対象の分類:
     - 正常系: 代表的な入力で期待通りの出力・副作用になること。
     - エッジケース: 空データ、1件だけのデータ、境界値などでも落ちないこと。
     - 異常系: 不正なパラメータ時に、静かに壊れず適切な例外/エラー処理がされること。
   - テストは**高速かつ決定的**であること:
     - 小さいダミー DataFrame や軽量オブジェクトを使い、数秒以内に終わるようにする。
     - 時刻やランダム性に依存する場合は、シード指定や固定値モックで安定させる。
4. モジュール種別ごとの追加観点

   - ETL / Features (例: `loto_etl.py`)

     - 出力の型・カラム名・必須カラムが仕様通りか。
     - 空データや単一レコードでも例外が出ないか。
     - 無効なパラメータ時にわかりやすい失敗モードになっているか。
   - DB ストア / Repository (例: `loto_pg_store.py`, `loto_repository.py`)

     - 実行される SQL 文（文字列）とパラメータの個数・順序が期待通りか。
     - トランザクション/ロールバック/コミットの呼び出し回数が正しいか。
     - DB 例外発生時のログや挙動が規約通りか。
   - ML ランナー / Pipelines (例: `model_runner.py`, `training_pipeline.py`)

     - パラメータグリッド生成関数が、設定から期待通りの候補を列挙しているか。
     - 前処理（列名チェック、欠損値扱いなど）の分岐がテストでカバーされているか。
     - 重い学習本体はモックしつつ、全体のフローがエラーなく最後まで到達するか。
5. テストファイルの末尾に、実行例コメントを付与する:

   ```python
   # To run:
   #   pytest tests/features/test_loto_etl.py -q
   ```
