-- sql/004_add_agent_rag_schema.sql
-- AI Agent, RAG, and Uncertainty Quantification Schema Extension
--
-- 概要:
-- 既存の nf_model_runs テーブルを拡張し、2025年版機能強化である
-- 「自律エージェント(Analyst/Planner)」「検索拡張生成(RAG)」「不確実性評価(CP)」
-- のメタデータを保存可能にします。

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. nf_model_runs テーブルの拡張
-- ----------------------------------------------------------------------------

-- agent_reasoning:
-- エージェントがなぜそのモデルやパラメータを選んだか、なぜその予測結果になったかの
-- 「思考プロセス」を自然言語（テキスト）で記録します。
ALTER TABLE nf_model_runs
ADD COLUMN IF NOT EXISTS agent_reasoning TEXT;

-- agent_metadata:
-- 各エージェント（Analyst, Planner, Reflection）からの構造化された出力を保存します。
-- 例: {"analyst": {"trend": "upward", "seasonality": "weekly"}, "planner": {...}}
ALTER TABLE nf_model_runs
ADD COLUMN IF NOT EXISTS agent_metadata JSONB DEFAULT '{}'::jsonb;

-- rag_context_ids:
-- RAG（Retrieval Augmented Generation）において、過去の類似パターンとして
-- 検索・参照されたデータのユニークIDリストを配列で保存します。
ALTER TABLE nf_model_runs
ADD COLUMN IF NOT EXISTS rag_context_ids TEXT[];

-- rag_metadata:
-- RAG検索の詳細情報（類似度スコア、検索に使用したベクトルクエリ、検索設定など）を保存します。
ALTER TABLE nf_model_runs
ADD COLUMN IF NOT EXISTS rag_metadata JSONB DEFAULT '{}'::jsonb;

-- uncertainty_score:
-- Conformal Prediction（適合予測）によって算出された、予測の「不確実性スコア」です。
-- 一般に、予測区間（Interval Width）の広さや分散の大きさを正規化した値を想定しています。
ALTER TABLE nf_model_runs
ADD COLUMN IF NOT EXISTS uncertainty_score DOUBLE PRECISION;

-- uncertainty_metrics:
-- 不確実性に関する詳細な評価指標をJSONBで保存します。
-- 例: {"coverage_error": 0.05, "mean_interval_width": 12.5, "confidence_level": 0.9}
ALTER TABLE nf_model_runs
ADD COLUMN IF NOT EXISTS uncertainty_metrics JSONB DEFAULT '{}'::jsonb;

-- ----------------------------------------------------------------------------
-- 2. カラムコメントの追加（ドキュメンテーション）
-- ----------------------------------------------------------------------------

COMMENT ON COLUMN nf_model_runs.agent_reasoning IS 'AIエージェントによる分析・思考プロセスのテキストログ（CoT: Chain of Thought）';
COMMENT ON COLUMN nf_model_runs.agent_metadata IS '各エージェント(Analyst, Planner, Reflection)の構造化された分析レポート';
COMMENT ON COLUMN nf_model_runs.rag_context_ids IS 'RAGプロセスによりコンテキストとして注入された過去データのユニークIDリスト';
COMMENT ON COLUMN nf_model_runs.rag_metadata IS 'RAG検索の実行メタデータ（類似度スコア、検索パラメータ等）';
COMMENT ON COLUMN nf_model_runs.uncertainty_score IS 'モデルの不確実性を示す単一スコア（値が大きいほど予測困難）';
COMMENT ON COLUMN nf_model_runs.uncertainty_metrics IS 'Conformal Predictionの実測カバレッジ率や区間幅などの詳細指標';

-- ----------------------------------------------------------------------------
-- 3. インデックスの作成（JSONBクエリの高速化）
-- ----------------------------------------------------------------------------

-- エージェントの分析結果（例: "trend" = "upward" な実験を検索）での絞り込み用
CREATE INDEX IF NOT EXISTS idx_nf_model_runs_agent_metadata ON nf_model_runs USING gin (agent_metadata);

-- 不確実性の内訳（例: coverage_error > 0.1 の実験を検索）での絞り込み用
CREATE INDEX IF NOT EXISTS idx_nf_model_runs_uncertainty_metrics ON nf_model_runs USING gin (uncertainty_metrics);

COMMIT;