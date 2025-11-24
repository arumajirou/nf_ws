提供されたシステム設計書およびソースコード（`nf_loto_platform`）の現状分析と、2024年〜2025年の最新論文（ICLR 2025採択論文など）の調査結果に基づき、本システムの**改善・拡張・提案報告書**を作成しました。

本システムはすでに「NeuralForecast」と「Optuna/Ray」を統合した高度なAutoML基盤を持っていますが、最新の**Time Series Foundation Models (TSFM)** や **LLM Agent** の概念を取り入れることで、予測精度だけでなく「自律的な分析・改善」が可能なシステムへと進化する余地があります。

-----

# 改善・機能拡張提案書：NeuralForecast AutoML Platform (2025版)

## 1\. エグゼクティブサマリー

現在の `nf_loto_platform` は、Deep Learningモデルのハイパーパラメータ探索を自動化する「AutoMLフェーズ」にあります。
これを、**「Foundation Model (基盤モデル) によるゼロショット予測」** と **「LLM Agentによる自律的データサイエンス」** を統合した次世代プラットフォームへ昇華させることを提案します。

特に、ロト/ナンバーズのような確率的性質の強いデータに対しては、単なる点予測（Point Forecast）ではなく、最新の**Conformal Prediction（適合予測）** を用いた信頼性の高い区間予測と、**RAG（検索拡張生成）** による過去の類似パターン参照が有効と考えられます。

-----

## 2\. 現状システムの分析 (As-Is)

| 評価軸 | 現状のステータス | 課題・限界 |
| :--- | :--- | :--- |
| **モデル** | NeuralForecast (NHITS, TFT等) 中心 | データごとの学習コストが高い。小規模データ（ロト等）では過学習しやすい。 |
| **探索** | Optuna / Ray Tune による探索 | 探索空間が広く、収束に時間がかかる。試行錯誤的アプローチ。 |
| **ワークフロー** | 人間が設定 → 実行 → 結果確認 | データ特性（変化点や異常値）に応じた前処理やモデル選択が静的。 |
| **ロジック** | 過去系列からの自己回帰予測 | 「過去の似た当選パターン」を検索して参考にする機能（RAG的アプローチ）がない。 |

-----

## 3\. 具体的拡張提案 (To-Be)

### 3.1 【Model】Time-MoE / MOMENT の統合 (基盤モデルの強化)

2025年のトレンドは、単一のモデルではなく**Mixture of Experts (MoE)** アーキテクチャです。

  * **提案内容**: 既存の `TSFMAdapter` を拡張し、以下の最新SOTAモデルを統合する。
      * **Time-MoE (2024/2025)**: 24億パラメータ規模のMoEモデル。スパース活性化により、計算コストを抑えつつ多様な時系列パターンに対応可能。
      * **MOMENT / UniTS**: 複数のドメインで事前学習されたモデル。ロトのような「分布が特殊なデータ」に対しても、ゼロショットで安定した特徴抽出が期待できる。
  * **メリット**: 学習なし（Zero-shot）での推論が可能になり、実験サイクルが数時間から数秒に短縮される。

### 3.2 【Agent】LLM Agentによる「自律分析」の実装

2025年のサーベイ論文 では、時系列分析は「予測モデル」から「自律エージェント」へシフトしています。

  * **提案機能**: `AutoModelFactory` の上位層に **"Analyst Agent"** を配置する。
  * **動作フロー**:
    1.  **Perception (知覚)**: データの統計量、トレンド、変化点をLLMが読み取る。
    2.  **Planning (計画)**: 「このデータは変動が激しいため、TFTではなくTime-MoEを使用し、ロバストな損失関数を選択する」といった戦略を立案。
    3.  **Action (実行)**: `model_runner.py` をAPI経由で実行。
    4.  **Reflection (反省)**: 予測結果を見て、「精度が低いのは外れ値のせいなので、前処理を変えて再実行」といった自律修正を行う。

### 3.3 【Method】Conformal Prediction (CPTC) による不確実性定量化

ロト予測において最も重要なのは「予測値」そのものより、「どの範囲に収まる確率が高いか」です。

  * **提案手法**: **CPTC (Conformal Prediction for Time-series with Change points)** の導入。
  * **概要**: 従来の分位点回帰（Quantile Regression）よりも厳密に、「変化点（当選パターンの変化）」を考慮しながら、指定した確率（例: 90%）で真値が含まれる区間を動的に生成する技術。
  * **実装**: `validation.py` の評価指標に「Coverage Error」を追加し、予測区間の信頼性を保証する。

### 3.4 【RAG】時系列RAG (Retrieval Augmented Forecasting)

言語モデルで主流のRAGを時系列に応用します。

  * **提案手法**: **RAFT (Retrieval Augmented Forecasting of Time Series)** の実装。
  * **ロジック**:
    1.  現在の直近データ（クエリ）をエンコード。
    2.  過去数年分のデータ（または他国のロトデータ）から、類似した波形パターンを検索（Retrieve）。
    3.  検索された「その後の動き」をヒントとして、モデルに入力する。
  * **効果**: ニューラルネットワークが「記憶」しきれないレアなパターンや、長期的な周期性を外部データベースから補完できる。

-----

## 4\. 拡張ロードマップと実装設計

### Phase 1: 基盤モデルの拡充 (1-2ヶ月)

  * **実装ファイル**: `src/nf_loto_platform/tsfm/`
  * **タスク**:
      * `TimeMoEAdapter` クラスの作成（HuggingFace上のモデルを利用）。
      * `MOMENTAdapter` の追加。
      * GPUメモリ効率化のための **PEFT (LoRA)** 対応。

### Phase 2: RAG & Uncertainty (2-3ヶ月)

  * **実装ファイル**: `src/nf_loto_platform/features/`, `ml/model_runner.py`
  * **タスク**:
      * 時系列パターン検索用のVector DB（FAISSやChroma）の組み込み。
      * `predict` メソッドの返り値に `conformal_interval` を追加。

### Phase 3: Agentic Workflow (3ヶ月以降)

  * **実装ファイル**: `src/nf_loto_platform/agents/` (新規作成)
  * **タスク**:
      * LangChain または LangGraph を用いた `AnalystAgent` の実装。
      * 現在のWeb UIに「チャットで分析指示」を行うインターフェースを追加。

## 5\. データベース定義の変更提案

エージェントやRAGの導入に伴い、メタデータ管理テーブルの拡張が必要です。

```sql
-- 既存の nf_model_runs に追加
ALTER TABLE nf_model_runs
ADD COLUMN agent_reasoning TEXT,       -- エージェントの思考プロセスログ
ADD COLUMN rag_context_ids TEXT[],     -- 参照した過去データのID
ADD COLUMN uncertainty_score FLOAT;    -- Conformal Predictionによる不確実性スコア
```an

これらの拡張により、貴殿のシステムは単なる「予測ツール」から、最新の研究成果を取り入れた「自律型AI予測プラットフォーム」へと進化します。