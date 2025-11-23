# nf_loto_platform リポジトリ調査報告書

## 1. 調査サマリ
- 目的: 統合ロト実験プラットフォームのディレクトリ構造・主要モジュール・補助資産を横断調査し、未実装／不整合／改善余地を洗い出す。
- 対象: `/mnt/e/env/ts/nf/nf_ws` 配下のアプリ・ライブラリ・インフラ支援スクリプト・ドキュメント・テスト資産一式。
- 手法: README や docs で意図を再確認しつつ、`src/nf_loto_platform`、`apps/webui_streamlit`、`pipelines/`、`tsfm/` 等を静的解析。pytest ログや `report/` 生成物も参照。
- 観測: コアドメイン (db/ml/agents) は skeleton 実装が揃う一方で、WebUI/TSFM/EasyTSF/ts_research 連携は未接続な箇所が多く、API 契約齟齬も複数見つかった。

## 2. ディレクトリ構造概要
| パス | 役割 |
| --- | --- |
| `apps/webui_streamlit/` | 旧 nf_loto WebUI (Streamlit) のエントリ。DB/UI 設定を直接 import しつつ、`run_loto_experiment` を即時実行する構成。現状は import パスがリポジトリ構造と一致せず動作不可。|
| `config/` | DB・特徴量・UI 等の YAML 設定テンプレート。`db.yaml.template` など機密値を埋める前提。|
| `docs/` | 設計書・API サマリ・TSFM 統合計画など。`DESIGN_OVERVIEW.md`、`IMPLEMENTATION_PLAN.md`、`tsfm_integration/` など多層。|
| `notebooks/` | プラットフォーム紹介／検証ノートブック。環境契約テストが `notebooks/` の存在を検証。|
| `sql/` | `nf_*` や `ts_research.*` メタテーブルの DDL/クエリ群。`
| `src/nf_loto_platform/` | 統合 Python パッケージ本体。`core`, `db`, `ml`, `agents`, `pipelines`, `monitoring`, `reports`, `tsfm` 等に分割。|
| `tests/` | 単体/統合/非機能/静的テストを網羅。DB/ML stubs を多用し import 契約を確認。|
| `artifacts/`, `hist/`, `lightning_logs/` | 実験成果物・履歴を置くデフォルトディレクトリ。|
| `report/` | pytest HTML/ログ等の既存レポート出力先。本報告書もここに配置した。|

## 3. モジュール別メモ
- `core`/`db`: DB 設定は `core/settings.load_db_config` が YAML から読む想定だが、`db/db_config.py` で固定 dict も共存。`postgres_manager` や `loto_pg_store` は本番用 CRUD を実装済み。
- `ml`: `automodel_builder` と `model_runner` が主。NeuralForecast 依存を極力ラップしているが、計測メトリクスの書き込みは未着手。
- `agents`: Curator/Planner/Forecaster/Reporter/Scientist/Orchestrator 群を提供。LLM クライアント抽象化 (`BaseLLMClient`) あり。ts_research 拡張は設計途中。
- `pipelines`: 旧 runner を `runpy` で叩くラッパと EasyTSF CLI ひな型が存在。
- `tsfm`: Chronos/TimeGPT/Lag-Llama/TempoPFN など TSFM アダプタ骨格のみ (predict 未実装)。

## 4. 主な課題と改善提案
1. **WebUI が現行パッケージ構造と不整合**  
   - `apps/webui_streamlit/streamlit_app.py` は `config.db_config` や `src.*` モジュールを import しており (L19-L26)、現リポジトリ内に該当モジュールが存在しないため起動時に即 `ModuleNotFoundError`。  
   - `nf_loto_platform` パッケージ経由で DB 設定・リポジトリ・モデルランナーを提供するよう import を整理し、依存する設定ファイルを YAML/環境変数経由で読み込むべき。 
2. **ForecasterAgent と model_runner の API 契約不整合**  
   - `ForecasterAgent.run_sweep` が `model_runner.sweep_loto_experiments` に存在しない `loss`/`metric` キーワードを渡しており (src/nf_loto_platform/agents/forecaster_agent.py L67-L80)、TypeError で停止する。  
   - さらに `run_loto_experiment` がメトリクス値を `meta` に格納せず (src/nf_loto_platform/ml/model_runner.py L246-L275)、Forecaster は常に `None` を受け取るためベストモデル判定が成立しない。  
   - sweep/running API を揃え、学習時に得た指標 (loss/metric) を `meta` や `log_run_end(...metrics=...)` に保存する必要がある。 
3. **TSResearchOrchestrator のメタデータ登録が破綻**  
   - `ensure_dataset` へ `id_columns=list(unique_ids)` を渡しており (src/nf_loto_platform/agents/ts_research_orchestrator.py L79-L87)、列名ではなく予測対象 ID 値が JSON で永続化される。これは dataset メタの意味を成さず、ID 数が多いと高速化目的のメタ表が肥大する。  
   - パネル抽出時に実際の列名 (`unique_id` など) を渡すよう分離すべき。 
4. **Anomaly 連携の設計が未完**  
   - Orchestrator は `anomaly_agent.detect(..., id_columns=list(unique_ids))` を呼んでおり (L111-L116)、列名ではなく ID 値を渡しているため `ValueError: 必要なカラムが不足` が発生する。  
   - さらに `self._anomaly_agent.to_rows(...)` を呼び出すが、`AnomalyAgent` に `to_rows` 実装が存在しない (src/nf_loto_platform/agents/anomaly_agent.py 全体)。  
   - `id_columns` には列名を指定し、検知結果を bulk insert 用 dict に変換する補助 (例: `def to_rows(self, records) -> List[dict]: ...`) を追加する必要がある。 
5. **EasyTSF ランナーがダミーのまま**  
   - `_lazy_import_orchestrator` が引数必須の `AgentOrchestrator` を引数なしで生成しているため (src/nf_loto_platform/pipelines/easytsf_runner.py L58-L69)、インスタンス化段階で例外。  
   - `run_easytsf` も設定内容を `print` するだけで `TSResearchOrchestrator` を一切呼ばず、TODO コメントのまま。CLI は実質未実装。 
6. **TimeSeriesScientistAgent が LLM 契約を満たさない**  
   - `analyze_experiment` / `compare_experiments` は `self._llm.complete(...)` を呼ぶ設計 (src/nf_loto_platform/agents/time_series_scientist_agent.py L62-L75) だが、提供されている `BaseLLMClient` インターフェイスは `generate(system_prompt, user_prompt, ...)` のみ。  
   - 既存クライアントでは AttributeError で停止するため、LLM 呼び出しを `BaseLLMClient` 契約に合わせて修正するか、Scientist 用の専用抽象クラスを定義すべき。 
7. **TSFM アダプタ群がすべて NotImplemented**  
   - Chronos/TimesFM/Lag-Llama/TempoPFN などの `predict` は明示的に `NotImplementedError` を投げる (例: src/nf_loto_platform/tsfm/chronos_adapter.py L34-L52)。  
   - README/docs では TSFM 統合完成を謳っているが、実装は骨格のみ。少なくともバックエンド呼び出し／入力検証／結果整形の MVP を提供しないと実験フローに組み込めない。 
8. **特徴量モジュールが README と乖離**  
   - README では `features/cleaning.py` など多数の下位モジュールを列挙しているが (README.md 2.3 節)、実際の `src/nf_loto_platform/features` には `add_lag_feature` のみ (src/nf_loto_platform/features/__init__.py)。  
   - ドキュメントと実コードの差が大きく、利用者が import すると `ImportError` になる。未移植モジュールは README から除外するか TODO として明記し、ロードマップを提示する必要がある。 
9. **WebUI/DB 設定の機密取り扱いが未整備**  
   - `src/nf_loto_platform/db/db_config.py` に平文のデフォルトパスワード (`'password': 'z'`) をベタ書きしており、本番向けに提供できない。  
   - `core/settings.load_db_config` が YAML を探す実装になっているので、`db_config.py` はテンプレート参照に切り替え、環境変数 or `.yaml` に集約した方が安全。 
10. **TSResearch 付帯処理のリカバリー設計が不足**  
    - Orchestrator は `ensure_schema()`〜`create_experiment()` の全工程で例外を握りつぶさないため、たとえば schema 作成失敗がそのまま実験を巻き戻す。最低限、DB 不達時のフォールバックやリトライ、ログ出力を追加することを推奨。

## 5. 推奨アクション
- WebUI と EasyTSF の import/依存関係を `nf_loto_platform` パッケージ基準にそろえ、未実装部分を段階的に削るか TODO 管理表へ切り出す。
- model_runner で算出した目的関数を `meta` および `log_run_end(...metrics=...)` に反映し、Forecaster から参照できるよう API 契約を更新する。
- ts_research 連携 (dataset 定義、anomaly/casual 付帯解析、Scientist Agent) を end-to-end で 1 件通し、必要なユーティリティ (`AnomalyAgent.to_rows` など) を補う。
- TSFM アダプタを 1 モデルだけでも実装し、テスト/ドキュメント双方から実貌を確認できるようにする。
- README/docs を現状ベースにアップデートし、未実装モジュールには明示的なロードマップを付記する。

---
本報告書は `report/repository_audit.md` に保存しました。追加で深掘りが必要な箇所があれば指示してください。
