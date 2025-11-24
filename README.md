NeuralForecast AutoML Platform (nf_loto_platform)統合時系列予測プラットフォーム。NeuralForecast、TSFM (Time Series Foundation Models)、および自律型AIエージェントを統合し、実験から本番適用までをサポートします。主な機能 (Features)AutoML: Optuna / Ray Tune を用いた自動モデル選択とハイパーパラメータ探索。TSFM Integration (Experimental): Chronos, Time-MoE, Lag-Llama などの基盤モデル用アダプタ（順次実装中）。AI Agents:Analyst Agent: データ特性を分析し、戦略を提案。Planner Agent: 実験計画を立案。Reporter Agent: 実験結果を評価・要約。WebUI: 実験管理と可視化のための Streamlit ダッシュボード。Data Management: PostgreSQL を用いたデータセットおよび実験履歴の管理。ディレクトリ構造 (Architecture)nf_ws/
├── apps/                   # アプリケーションエントリポイント (WebUI, CLI)
├── config/                 # 設定ファイル (YAML templates)
├── docs/                   # ドキュメント
├── report/                 # 実験レポート・ログ出力先
├── sql/                    # DB初期化・管理用SQL
├── src/
│   └── nf_loto_platform/   # コアパッケージ
│       ├── agents/         # AIエージェント群
│       ├── core/           # 設定・共通ユーティリティ
│       ├── db/             # DBアクセス層
│       ├── features/       # 特徴量エンジニアリング
│       ├── ml/             # 学習・推論ランナー
│       └── tsfm/           # 基盤モデルアダプタ
└── tests/                  # テストコード
セットアップ (Getting Started)前提条件 (Prerequisites)Python 3.11 以上PostgreSQL 15 以上(Optional) CUDA 11.8+ for GPU accelerationインストール (Installation)リポジトリのクローンgit clone <repository-url>
cd nf_ws
仮想環境の作成と有効化python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows
パッケージのインストール (Editable Mode)本プロジェクトは src レイアウトを採用しているため、WebUIやCLIを正常に動作させるには -e . でのインストールが必須です。pip install -e .[dev]
環境変数の設定.env ファイルを作成し、データベース接続情報などを設定します。# .env ファイルの作成（例）
echo "POSTGRES_HOST=localhost" >> .env
echo "POSTGRES_DB=nf_platform" >> .env
echo "POSTGRES_USER=postgres" >> .env
echo "POSTGRES_PASSWORD=yourpassword" >> .env
または config/db.yaml.template を config/db.yaml にコピーして編集します（環境変数の設定が優先されます）。データベースの初期化# 必要なテーブルを作成
psql -h localhost -U postgres -d <your_db> -f sql/001_create_nf_model_run_tables.sql
psql -h localhost -U postgres -d <your_db> -f sql/004_add_agent_rag_schema.sql
使い方 (Usage)WebUI の起動ダッシュボードからインタラクティブに実験を実行・可視化できます。streamlit run apps/webui_streamlit/streamlit_app.py
CLI での実験実行 (EasyTSF)設定ファイルベースで実験を実行します。python -m nf_loto_platform.pipelines.easytsf_runner --config config/loto_pipeline_config.yaml
モジュール実装状況 (Implementation Status)Features: 現在は基本的なラグ特徴量の生成 (add_lag_feature) をサポート。外れ値除去などの高度な前処理はロードマップに含まれています。TSFM: Chronos アダプタの初期実装が完了しています。その他のモデルは順次対応予定です。Agents: 基本的な会話・計画ループは動作しますが、自律的なコード生成・実行機能は制限されています。ML: NeuralForecast の主要モデル (AutoNHITS, AutoTFT) をサポートしています。開発者向け (For Developers)テストの実行pytest tests/
© 2025 NeuralForecast Loto Platform Project