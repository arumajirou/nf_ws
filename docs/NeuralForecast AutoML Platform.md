提供された設計書、SQL定義書、README、およびソースコード構造に基づき、**「NeuralForecast AutoML Platform (nf_loto_platform)」**のリバースエンジニアリングを行い、業務レベルの仕様書類を作成しました。

このシステムは、**時系列予測（特にロト/ナンバーズのような数値予測、または一般的な時系列データ）に対して、NeuralForecast（Deep Learning）およびTSFM（Foundation Models）を用いて、ハイパーパラメータ探索・学習・評価・履歴管理を自動化する統合プラットフォーム**です。

---

# 1. システム要件定義書

## 1.1 システム概要

本システムは、専門的な知識を必要とするDeep Learningベースの時系列予測モデルの構築を自動化（AutoML）し、Webインターフェースを通じて誰でも高度な予測を実行・管理・分析できる環境を提供する。また、最新の時系列基盤モデル（TSFM: Time Series Foundation Models）への拡張性を持ち、従来モデルとの比較検証を可能にする。

## 1.2 業務目的

1. **予測業務の効率化** : 手動でのパラメータ調整工数を削減し、最適なモデル探索を自動化する。
2. **属人化の解消** : 高度なモデル（Transformer系など）の利用を容易にし、担当者のスキルに依存しない予測精度を確保する。
3. **実験管理の高度化** : 全ての実験結果、パラメータ、リソース使用状況をデータベースおよびMLflowで一元管理し、再現性と説明責任を担保する。

## 1.3 機能要件

| **カテゴリ**     | **ID** | **機能名称**         | **概要**                                                                       | **参照ファイル**             |
| ---------------------- | ------------ | -------------------------- | ------------------------------------------------------------------------------------ | ---------------------------------- |
| **データ管理**   | F-01         | データ取込・検証           | CSV/Parquet等のアップロード、欠損値・カラム構成の自動検証 (`DataValidator`)        | DESIGN_OVERVIEW.md                 |
|                        | F-02         | ロトデータ連携             | PostgreSQL上のロト系データ(`nf_loto%`)からのパネルデータ抽出 (`loto_repository`) | TSFM_INTEGRATION_DETAIL.md         |
| **学習・探索**   | F-03         | 自動ハイパーパラメータ探索 | Optuna/Ray Tuneを用いた探索アルゴリズムの自動選択と実行                              | DESIGN_OVERVIEW.md                 |
|                        | F-04         | マルチバックエンド実行     | NeuralForecastモデルおよびTSFM（Chronos, TimeGPT等）の統一的な実行インターフェース   | TSFM_INTEGRATION_DETAIL.md         |
|                        | F-05         | リソース監視               | 学習中のCPU/GPU/メモリ使用率のリアルタイムモニタリング                               | README.md                          |
| **管理・可視化** | F-06         | 実験履歴管理               | 実行パラメータ、メトリクス、ログのDB保存 (`nf_model_runs`)                         | 001_create_nf_model_run_tables.sql |
|                        | F-07         | Web UI操作                 | Streamlitによる設定、実行、結果確認、グラフ可視化                                    | README.md                          |
|                        | F-08         | レポート生成               | 学習結果のHTMLレポート出力                                                           | TSFM_INTEGRATION_DETAIL.md         |

## 1.4 非機能要件

* **スケーラビリティ** : 複数GPU/CPUを利用した並列処理に対応すること。
* **拡張性** : 新しいモデル（TSFM等）を追加する際、既存のパイプラインを変更せずにAdapterパターンで拡張できること。
* **追跡可能性** : 全てのモデル実行において、使用した設定、データバージョン、乱数シードが記録され、再実行可能であること。

---

# 2. 詳細機能定義書

## 2.1 モジュール構成

システムは疎結合なモジュール群によって構成される。

### (1) AutoModelFactory (Core)

* **役割** : 最適化フローの統合管理、ファサード。
* **機能** :
* データセット分析（サイズ、系列数、外生変数）。
* 探索戦略の決定（データ規模に応じたSampler/Prunerの選択）。
* 学習ジョブのディスパッチ。

### (2) Model Runner & Registry (ML Layer)

* **役割** : 具体的なモデルのインスタンス化と実行。
* **機能** :
* **NeuralForecast経路** : `AutoModel`（AutoNHITS, AutoTFT等）のビルドと学習。
* **TSFM経路** : `BaseTSFMAdapter`を継承したChronos/TimeGPT等の実行。
* モデルカタログ管理（複雑度、推奨データ長の定義）。

### (3) Validation Module

* **役割** : 実行前の健全性チェック。
* **機能** :
* **ConfigValidator** : GPU利用可否、メモリ容量、パラメータ整合性チェック。
* **DataValidator** : 必須カラム(`unique_id`, `ds`, `y`)の存在、データ型の確認。

### (4) Logging & Monitoring

* **役割** : 実行状況の記録と監視。
* **機能** :
* **DB Logger** : PostgreSQLへのメタデータ書き込み。
* **MLflow Adapter** : 実験管理ツールへのメトリクス送信。
* **Resource Monitor** : バックグラウンドスレッドでのシステムリソース収集。

## 2.2 アルゴリズム選択ロジック (SearchAlgorithmSelector)

データ規模とモデルの複雑性に基づき、以下の戦略を自動適用する。

| **条件**            | **推奨アルゴリズム (Optuna)** | **備考**                                      |
| ------------------------- | ----------------------------------- | --------------------------------------------------- |
| 試行数 < 10               | RandomSampler                       | 探索空間のランダムサンプリング                      |
| 大規模データ + 複雑モデル | TPESampler (multivariate)           | 変数間依存関係を考慮、HyperbandPrunerで早期打ち切り |
| 中規模データ              | TPESampler / CmaEsSampler           | 一般的なベイズ最適化                                |

---

# 3. 詳細機能設計仕様書

## 3.1 アーキテクチャ設計

**レイヤー構造:**

1. **Presentation Layer** : Streamlit WebUI
2. **Application Layer** : `AutoModelFactory`, `run_loto_experiment`
3. **Domain Layer** :

* `ModelRunner` (実行戦略)
* `SearchAlgorithmSelector` (探索戦略)
* `Validation` (ドメインルール)

1. **Infrastructure Layer** :

* `LotoRepository` (PostgreSQLアクセス)
* `MLflowClient`
* `TSFMAdapters` (外部モデルラッパー)

## 3.2 クラス設計 (主要部)

### `AutoModelFactory` クラス

**Python**

```
class AutoModelFactory:
    def __init__(self, model_name, h, optimization_config):
        # 初期化と設定保持
  
    def create_auto_model(self, dataset, config, loss):
        # 1. Validation実行
        # 2. SearchStrategy選択
        # 3. モデルインスタンス生成 (NeuralForecast)
        # 4. MLflow Run開始
        # 5. fit() 実行
        return model
```

### `BaseTSFMAdapter` クラス (TSFM統合用)

NeuralForecastと同じインターフェースで振る舞うための基底クラス。

**Python**

```
class BaseTSFMAdapter(ABC):
    @abstractmethod
    def fit(self, df: pd.DataFrame):
        """学習処理（Zero-shotの場合はパス）"""
        pass

    @abstractmethod
    def predict(self, h: int, level: List[int]) -> pd.DataFrame:
        """予測実行。unique_id, ds, y_hat を含むDFを返す"""
        pass
```

## 3.3 データベース設計方針

* **永続化対象** : モデルのバイナリそのものではなく、「どのように作ったか（Config）」と「どうだったか（Metrics）」のメタデータを中心に保存する。
* **トリガー** : `updated_at` カラムはPostgreSQLのトリガー関数 `fn_nf_model_runs_set_updated_at` により自動更新する。

---

# 4. テーブル定義書

`sql/001_create_nf_model_run_tables.sql` に基づく主要テーブル定義です。

## テーブル名: `nf_model_runs`

モデルの学習・推論実行の1回ごとの履歴を管理する。

| **カラム名**            | **論理名** | **型** | **制約/デフォルト** | **説明**                      |
| ----------------------------- | ---------------- | ------------ | ------------------------- | ----------------------------------- |
| **id**                  | 実行ID           | BIGSERIAL    | PK                        | 自動採番ID                          |
| **created_at**          | 作成日時         | TIMESTAMPTZ  | NOT NULL, DEFAULT NOW()   | レコード作成日時                    |
| **updated_at**          | 更新日時         | TIMESTAMPTZ  | NOT NULL, DEFAULT NOW()   | トリガーによる自動更新              |
| **table_name**          | データテーブル名 | TEXT         | NOT NULL                  | 学習に使用したソーステーブル名      |
| **loto**                | ロト種別         | TEXT         |                           | (任意) ロトの種類識別子             |
| **unique_ids**          | 系列ID配列       | TEXT[]       |                           | 学習対象の系列IDリスト              |
| **model_name**          | モデル名         | TEXT         | NOT NULL                  | 使用したモデル (例: NHITS, TFT)     |
| **backend**             | バックエンド     | TEXT         | NOT NULL                  | 実行エンジン (optuna, ray, tsfm)    |
| **horizon**             | 予測期間         | INTEGER      | NOT NULL                  | 予測ホライゾン(h)                   |
| **loss**                | 損失関数         | TEXT         |                           | 最適化に使用した損失関数名          |
| **optimization_config** | 最適化設定       | JSONB        | NOT NULL                  | 試行回数、リソース設定など          |
| **search_space**        | 探索空間         | JSONB        |                           | ハイパーパラメータ探索範囲          |
| **status**              | ステータス       | TEXT         | NOT NULL                  | PENDING, RUNNING, COMPLETED, FAILED |
| **start_time**          | 開始日時         | TIMESTAMPTZ  | DEFAULT NOW()             | 学習開始時間                        |
| **end_time**            | 終了日時         | TIMESTAMPTZ  |                           | 学習終了時間                        |
| **duration_seconds**    | 実行時間         | DOUBLE       |                           | 秒単位の所要時間                    |
| **metrics**             | 評価指標         | JSONB        |                           | MAE, MSE等のテスト結果              |
| **best_params**         | 最適パラメータ   | JSONB        |                           | 探索で得られたベストパラメータ      |
| **resource_summary**    | リソース概要     | JSONB        |                           | CPU/GPU使用率の統計                 |
| **logs**                | ログ             | TEXT         |                           | 標準出力/エラー出力の抜粋           |
| **mlflow_run_id**       | MLflow ID        | TEXT         |                           | MLflow側のRun ID紐付け              |

---

# 5. 運用仕様書

## 5.1 システム起動・停止

* **起動** : `scripts/launch.sh` を使用。これにより、仮想環境の有効化、DBチェック、Streamlitアプリの起動が一括で行われる。
* **停止** : Web UIを閉じ、ターミナルで `Ctrl+C` を実行。

## 5.2 データ投入フロー

1. **CSVアップロード** : Web UIの「Data Upload」画面からドラッグ&ドロップ。

* 必須カラム: `unique_id` (系列ID), `ds` (日時), `y` (目的変数)。
* 検証: `DataValidator` がフォーマット違反を即座にフィードバックする。

## 5.3 トラブルシューティング

* **OOM (Out of Memory) 発生時** :
* 対策A: 設定で `batch_size` を小さくする (例: 32 -> 16)。
* 対策B: `optimization_config` の `gpus` 数を増やす（環境が許せば）。
* 対策C: 同時並行実行数 (`num_samples` の並列度) を下げる。
* **最適化が収束しない場合** :
* `SearchAlgorithmSelector` の推奨に従い、試行回数 (`num_samples`) を増やす。
* 探索空間 (`search_space`) を手動で狭める。

## 5.4 拡張手順 (新規TSFMモデル追加)

1. `src/nf_loto_platform/tsfm_adapters.py` に `BaseTSFMAdapter` を継承したクラスを実装する。
2. `src/nf_loto_platform/ml/model_registry.py` に新しいモデル定義を追加する（`engine_kind="tsfm"` を指定）。
3. Web UIを再起動すると、プルダウンメニューに新モデルが表示される。

## 5.5 バックアップ運用

* PostgreSQLの `nf_model_runs` テーブルは実験の資産であるため、定期的な `pg_dump` を推奨する。
* MLflowの `mlruns` ディレクトリ（またはDB）も同様にバックアップ対象とする。
