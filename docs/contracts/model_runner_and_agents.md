# model_runner と ForecasterAgent の契約

本ドキュメントは `run_loto_experiment` / `sweep_loto_experiments` / `ForecasterAgent.run_sweep` が共有する契約をまとめる。Phase 1 での実装強化を進める際の参照仕様として扱う。

## 用語

- 外向き API (WebUI, EasyTSF, ForecasterAgent)
  - `objective`: 主目的関数。MAE など最適化対象を表す。
  - `secondary_metric`: 補助的に記録する指標 (例: RMSE)。指定しなければ `objective` を流用する。
- model_runner 内部
  - `loss_name`: 実際に最小化する損失関数の識別子。`objective` と 1:1 に対応する。
  - `metric_name`: 副次的なメトリクスの識別子。`secondary_metric` と対応する。

## ランナー/エージェント間の責務

### `run_loto_experiment`

- 対象データセット・モデル設定を受けて単一の LOTO 実験を実行する。
- モデル訓練と検証を行い、可能な場合は `objective` と `secondary_metric` を計算する。
  - 予測対象と同じ `unique_id`,`ds` の実測値 (`panel_df["y"]`) が存在する場合に限り、MAE/MSE/RMSE/MAPE/SMAPE を算出する。
  - 将来ホライゾンの実測値が存在しない場合は `objective_value=None` を返し、ForecasterAgent 側でフォールバック動作 (先頭モデル選択) に委ねる。
- 戻り値には少なくとも次の情報を含める。
  - 予測結果 (`preds`: `unique_id`, `ds`, `<model_name>` 列を含む DataFrame)
  - `meta`: 実験のメタ情報 (下記参照)

### `sweep_loto_experiments`

- ハイパーパラメータやモデル構成の候補をまとめて試行する。
- 各試行で `run_loto_experiment` を呼び、統一された `meta` を収集する。
- 最終的に `Result` または `TrialResult` のリストを返し、ベスト試行も判定可能にする。
- `objective`, `secondary_metric` を引数で受け、grid 内に `loss` / `objective` / `secondary_metric` が含まれていればそちらを優先する。

### `ForecasterAgent.run_sweep`

- 外部から受け取った設定 (dataset, horizon, `objective`, `secondary_metric` など) を `sweep_loto_experiments` に橋渡しする。
- 戻り値の `Result` 群から最適な試行を判定し、オーケストレータやレポーターが使える形に整える。
- 上位層 (WebUI/EasyTSF) に対しては `objective` / `secondary_metric` という用語で公開し、内側の model_runner では `loss_name` / `metric_name` に対応付ける。

## Result/meta の必須フィールド

`run_loto_experiment` や `sweep_loto_experiments` が返す `Result.meta` には最低限以下を含める。

- `objective_name`: 使用した目的関数名 (例: `"mae"`).
- `objective_value`: 目的関数の値 (float または `null`).
- `secondary_metric_name`: 副次的メトリクス名 (なければ `null`)。
- `secondary_metric_value`: 副次的メトリクス値 (なければ `null`)。
- `metric_name` / `metric_value`: 旧 API との互換を保つため `secondary_metric_*` と同じ値を格納する。

これに加えて、モデル識別子、ハイパーパラメータ、データセット情報、実行環境など必要に応じたメタ情報を格納する。

## ロギングの取り扱い

- `run_loto_experiment` 開始時 (`log_run_start` 想定)
  - dataset やモデル情報に加え、`objective_name`, `metric_name` をログに含める。
- 実験完了時 (`log_run_end` 想定)
  - `objective_value`, `secondary_metric_value` を `metrics` JSON に含める (取得できた場合)。
- Sweep 実行では各 trial ごとのログを行い、ForecasterAgent から参照できるようトレース ID や experiment ID を共有する。

以上により、model_runner と ForecasterAgent を介した end-to-end の目的関数管理が一貫性を持つ。
