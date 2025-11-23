# ts_research 連携契約

nf_loto_platform と ts_research サービス間の契約を整理する。Phase 1 では仕様ドキュメントとして利用し、今後の実装の基準にする。

## 概念対応

| ts_research | 概要 | nf_loto_platform 対応 |
| --- | --- | --- |
| Dataset | 時系列パネルのメタ情報 (ID 列、時間列、ターゲット列) | `panel_datasets` 等のテーブル、`TSResearchOrchestrator.ensure_dataset` |
| Experiment | 特定のデータセットと設定での実行履歴 | `experiments` テーブル、`model_runner` からの run 情報 |
| Metrics | 実験結果の指標群 (MAE など) | `ts_research_store.metrics`、`Result.meta` |
| Anomaly | 異常検知結果 | `AnomalyAgent` / `ts_research_store.anomalies` |
| Causal | 因果解析結果 | (将来実装予定) `CausalAgent` / 対応テーブル |

## Canonical panel スキーマ

- 必須カラム: `unique_id`, `ds`, `y`
- 任意の特徴量カラムは `y` 以外に追加可能。
- すべての agent / orchestrator / ts_research API はこのスキーマを前提とする。

## API 呼び出しルール

- `id_columns` は **ID の値ではなく列名のリスト**。
  - シングル ID の場合: `id_columns=["unique_id"]`（Phase 2 実装で `TSResearchOrchestrator` が常にこの値を登録するよう修正済み）。
  - マルチ ID の場合: 例 `id_columns=["store_id", "item_id"]`
- `time_column` は `ds`、`target_column` は `y` を指定するのが基本。
- dataset 登録/API 呼び出しでは、列名とデータサンプルを分けて扱い、値を `id_columns` に渡さないこと。

## 主要コンポーネントの役割

### TSResearchOrchestrator

- nf_loto_platform から ts_research への窓口。
- Dataset の登録・更新、Experiment の開始/完了記録、Metrics の同期などを担当。
- 同一構成で重複登録しないようキャッシュを持つ。

### AnomalyAgent

- ts_research の anomaly API を呼び、結果をローカル DB に保存できる形式へ変換する。
- `to_rows` は `ts`, `series_id`, `score`, `is_anomaly`, `method`, `meta` を含む dict のリストを返す（`TSResearchStore.bulk_insert_anomalies` で利用）。
- Orchestrator から呼び出され、結果は `ts_research_store` を経由して保存される。

### TimeSeriesScientistAgent

- ts_research に蓄積された metrics/anomaly/causal 情報を解釈し、LLM を使ったサマリーを生成する。
- `BaseLLMClient.generate(system_prompt, user_prompt, ...)` に準拠した LLM クライアントを用い、明示的なプロンプトとレスポンス構造を定義する。

以上の契約を基盤として、ts_research 連携機能を段階的に実装する。
