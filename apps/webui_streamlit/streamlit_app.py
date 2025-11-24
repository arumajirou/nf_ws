"""Streamlit entry point for nf_loto_platform's Extended WebUI."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import pandas as pd
import altair as alt
import streamlit as st

# --- Project Imports ---
# sys.path 操作を排除し、インストール済みパッケージとしてインポートする
from nf_loto_platform.apps.dependencies import get_db_conn, get_model_runner
from nf_loto_platform.core.settings import load_db_config
from nf_loto_platform.db import loto_repository
from nf_loto_platform.ml.model_registry import list_automodel_names, get_model_spec

# New Agent Orchestrator
# 依存関係が解決できない場合でもWebUI自体は落ちないようにする
try:
    from nf_loto_platform.agents.orchestrator import AgentOrchestrator
except ImportError:
    AgentOrchestrator = None
    logging.warning("AgentOrchestrator could not be imported. Agent features will be disabled.")

# ---------------------------------------------------------------------------
# Dependency container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WebUIDependencies:
    """Bundle of dependencies used by the WebUI."""
    repo: Any
    model_runner: Any
    db_conn_factory: Callable[[], Any]
    db_config: Mapping[str, Any]
    orchestrator: Optional[Any] = None


def build_webui_dependencies() -> WebUIDependencies:
    """Return the default dependency bundle."""
    orchestrator = AgentOrchestrator() if AgentOrchestrator else None
    return WebUIDependencies(
        repo=loto_repository,
        model_runner=get_model_runner(),
        db_conn_factory=get_db_conn,
        db_config=load_db_config() or {},
        orchestrator=orchestrator
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_options(values: Sequence[str] | None, fallback: Sequence[str]) -> Sequence[str]:
    return values if values else fallback


def _fetch_dataframe(loader: Callable[[], pd.DataFrame], columns: Sequence[str]) -> pd.DataFrame:
    """Call a loader and guarantee the expected columns exist."""
    try:
        df = loader()
    except Exception:
        df = pd.DataFrame(columns=columns)
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    missing = [col for col in columns if col not in df.columns]
    if missing:
        for col in missing:
            df[col] = None
    return df


def _plot_forecast(df_preds: pd.DataFrame, title: str = "Forecast Results"):
    """Altairを使用して予測結果と信頼区間をプロットする."""
    
    if df_preds.empty:
        st.warning("No prediction data to plot.")
        return

    # データ整形 (Long format for Altair)
    # 想定カラム: unique_id, ds, y (history), AutoNHITS, AutoNHITS-lo-90, AutoNHITS-hi-90
    
    # モデル名を特定 (y, ds, unique_id 以外)
    cols = df_preds.columns
    model_cols = [c for c in cols if c not in ["unique_id", "ds", "y", "y_hat_lower", "y_hat_upper"] and "-lo-" not in c and "-hi-" not in c]
    
    # チャート生成
    charts = []
    unique_ids = df_preds["unique_id"].unique()
    
    for uid in unique_ids:
        subset = df_preds[df_preds["unique_id"] == uid].copy()
        
        # Base line chart for predictions
        base = alt.Chart(subset).encode(x='ds:T')
        
        lines = []
        # Plot Model Predictions
        for model in model_cols:
            line = base.mark_line().encode(
                y=alt.Y(f'{model}:Q', title='Value'),
                color=alt.value('blue'),
                tooltip=['ds', model]
            ).properties(title=f"{uid} - {model}")
            lines.append(line)
            
            # Plot Confidence Intervals (if available)
            # 簡易的に -lo-90 / -hi-90 を探す、または standard column names
            lo_col = f"{model}-lo-90"
            hi_col = f"{model}-hi-90"
            
            # conformal.py の出力形式 (y_lower, y_upper) の場合
            if "y_lower" in subset.columns and "y_upper" in subset.columns:
                band = base.mark_area(opacity=0.3, color='lightblue').encode(
                    y='y_lower:Q',
                    y2='y_upper:Q'
                )
                lines.append(band)
            elif lo_col in subset.columns and hi_col in subset.columns:
                band = base.mark_area(opacity=0.3, color='lightblue').encode(
                    y=f'{lo_col}:Q',
                    y2=f'{hi_col}:Q'
                )
                lines.append(band)

        # Plot Historical/True values if available ("y" column)
        if "y" in subset.columns and subset["y"].notna().any():
            truth = base.mark_circle(color='black', size=30).encode(
                y='y:Q',
                tooltip=['ds', 'y']
            )
            lines.append(truth)

        if lines:
            combined = alt.layer(*lines).interactive()
            charts.append(combined)

    for chart in charts:
        st.altair_chart(chart, use_container_width=True)


# ---------------------------------------------------------------------------
# UI renderer
# ---------------------------------------------------------------------------

def render_app(st_module=None, deps: WebUIDependencies | None = None) -> None:
    """Render the Streamlit application."""

    # 外部からモジュールを注入可能にする（テスト用）
    if st_module:
        global st
        st = st_module
    
    deps = deps or build_webui_dependencies()

    repo = deps.repo
    model_runner = deps.model_runner
    orchestrator = deps.orchestrator

    st.set_page_config(page_title="NeuralForecast AutoML Platform (2025)", layout="wide")

    st.sidebar.title("NF AutoML Platform")
    st.sidebar.caption("v2.0.0 (Agent & TSFM Supported)")
    
    st.sidebar.subheader("DB Connection")
    # DBパスワードなど機密情報はマスクして表示するのが望ましいが、ここではconfigの内容を確認用に表示
    safe_config = {k: v if k != 'password' else '******' for k, v in deps.db_config.items()}
    st.sidebar.json(safe_config or {"message": "Please configure config/db.yaml"})

    # Tabs
    tab_run, tab_agent, tab_history = st.tabs([
        "🧪 Manual Experiment", 
        "🤖 AI Agent (Auto)", 
        "📈 Results & History"
    ])

    # ========================================================================
    # Tab 1: Manual Experiment (Existing + TSFM/RAG extensions)
    # ========================================================================
    with tab_run:
        st.header("Manual Experiment Execution")

        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Data Selection")
            tables_df = _fetch_dataframe(repo.list_loto_tables, columns=["tablename"])
            table_options = _safe_options(tables_df["tablename"].tolist(), ["nf_loto_panel"])
            table_name = st.selectbox("Table (nf_loto%)", options=table_options, index=0)

            def _load_loto_values() -> pd.DataFrame:
                return repo.list_loto_values(table_name)

            loto_df = _fetch_dataframe(_load_loto_values, columns=["loto"])
            loto_options = _safe_options(loto_df["loto"].tolist(), ["loto6"])
            loto_value = st.selectbox("Loto Type", options=loto_options, index=0)

            def _load_unique_ids() -> pd.DataFrame:
                return repo.list_unique_ids(table_name, loto_value)

            uid_df = _fetch_dataframe(_load_unique_ids, columns=["unique_id"])
            uid_options = _safe_options(uid_df["unique_id"].tolist(), ["S1", "S2"])
            unique_ids = st.multiselect("Series IDs", options=uid_options, default=uid_options[:min(3, len(uid_options))])

        with col2:
            st.subheader("Model Configuration")
            model_names = st.multiselect(
                "AutoModel / TSFM",
                options=list_automodel_names(),
                default=["AutoNHITS"],
                help="Select NeuralForecast AutoModels or TSFM (Time-MoE, etc.)",
            )
            
            backend_opts = ["optuna", "ray", "tsfm", "local"]
            backend = st.selectbox("Backend", options=backend_opts, index=0)
            
            st.markdown("---")
            st.markdown("##### Advanced Features")
            use_rag = st.checkbox("Enable RAG (Retrieval Augmented Generation)", value=False, 
                                help="Search for similar historical patterns to inform the model.")
            
            st.markdown("##### Resources")
            c1, c2, c3 = st.columns(3)
            num_samples = c1.number_input("num_samples", 1, 1000, 10)
            cpus = c2.number_input("CPUs", 1, 64, 4)
            gpus = c3.number_input("GPUs", 0, 8, 0)

        st.markdown("---")
        horizon = st.number_input("Forecast Horizon (h)", 1, 365, 28)
        
        if st.button("🚀 Start Experiment", type="primary"):
            if not unique_ids or not model_names:
                st.error("Please select at least one series and one model.")
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                results = []
                
                for i, model_name in enumerate(model_names):
                    status_text.text(f"Running {model_name} with backend={backend}...")
                    
                    try:
                        # Call Model Runner
                        preds, meta = model_runner.run_loto_experiment(
                            table_name=table_name,
                            loto=loto_value,
                            unique_ids=unique_ids,
                            model_name=model_name,
                            backend=backend,
                            horizon=horizon,
                            num_samples=num_samples,
                            cpus=cpus,
                            gpus=gpus,
                            use_rag=use_rag
                        )
                        results.append((model_name, preds, meta))
                        st.success(f"✅ {model_name} Completed (Run ID: {meta.get('run_id')})")
                        
                        # Show Metrics
                        metrics = meta.get("metrics", {})
                        u_score = meta.get("uncertainty_score", 0.0)
                        
                        m1, m2, m3 = st.columns(3)
                        # metricsがNoneの場合のガード
                        mae_val = metrics.get('mae', 0) if metrics else 0
                        m1.metric("MAE", f"{mae_val:.4f}")
                        m2.metric("Time (s)", f"{meta.get('duration_seconds', 0):.2f}")
                        m3.metric("Uncertainty Score", f"{u_score:.4f}")
                        
                        # Plot
                        with st.expander(f"Forecast Plot: {model_name}", expanded=True):
                            _plot_forecast(preds)
                            
                        # Show RAG Info if used
                        if use_rag and meta.get("rag_metadata"):
                            with st.expander("🔍 RAG Context (Retrieved Patterns)"):
                                st.json(meta["rag_metadata"])

                    except Exception as e:
                        st.error(f"❌ Failed to run {model_name}: {e}")
                        st.exception(e)
                    
                    progress_bar.progress((i + 1) / len(model_names))
                
                status_text.text("All experiments finished.")

    # ========================================================================
    # Tab 2: AI Agent (Autonomous Loop)
    # ========================================================================
    with tab_agent:
        st.header("🤖 Autonomous Analyst Agent")
        st.markdown("""
        The AI Agent will autonomously analyze your data, select the best models (including TSFMs), 
        execute experiments, and refine parameters based on the results (Reflection).
        """)
        
        if orchestrator is None:
            st.error("AgentOrchestrator is not available. Check your python environment.")
        else:
            col_a, col_b = st.columns([1, 2])
            
            with col_a:
                st.subheader("Agent Settings")
                agent_table = st.selectbox("Target Table", options=table_options, key="agent_table")
                agent_loto = st.selectbox("Target Loto", options=loto_options, key="agent_loto")
                agent_uids = st.multiselect("Target Series", options=uid_options, default=uid_options[:1], key="agent_uids")
                
                agent_horizon = st.number_input("Horizon", value=28, key="agent_h")
                max_iter = st.slider("Max Iterations (PDCA Cycles)", 1, 5, 3)
                
                start_agent_btn = st.button("🤖 Start Autonomous Loop", type="primary")

            with col_b:
                st.subheader("Agent Reasoning & Logs")
                agent_container = st.container()

            if start_agent_btn and agent_uids:
                with st.spinner("Agent is working... (This may take a few minutes)"):
                    # Output capture callback could be implemented here to stream logs
                    results = orchestrator.run_autonomous_loop(
                        table_name=agent_table,
                        loto=agent_loto,
                        unique_ids=agent_uids,
                        horizon=agent_horizon,
                        max_iterations=max_iter
                    )
                
                st.success("Autonomous loop completed!")
                
                # 結果表示
                for i, res in enumerate(results):
                    meta = res.meta
                    agent_meta = meta.get("agent_metadata", {})
                    
                    with agent_container.expander(f"Iteration {i+1}: {meta.get('model_name')}", expanded=True):
                        st.markdown("#### 🧠 Analyst & Planner Thoughts")
                        if "analyst_report" in agent_meta:
                            st.info(agent_meta["analyst_report"])
                        if "planner_rationale" in agent_meta:
                            st.markdown(f"**Plan**: {agent_meta['planner_rationale']}")
                        
                        st.markdown("#### 📊 Result Metrics")
                        st.json(meta.get("metrics"))
                        
                        _plot_forecast(res.preds)


    # ========================================================================
    # Tab 3: Results & History
    # ========================================================================
    with tab_history:
        st.header("Experiment History (nf_model_runs)")
        
        try:
            with deps.db_conn_factory() as conn:
                # Fetch latest runs with extended metadata
                query = """
                    SELECT 
                        id, created_at, model_name, backend, status, 
                        metrics, uncertainty_score, agent_reasoning 
                    FROM nf_model_runs 
                    ORDER BY id DESC 
                    LIMIT 100
                """
                df_hist = pd.read_sql(query, conn)
                
            st.dataframe(df_hist, use_container_width=True)
            
            # Detail View
            selected_id = st.number_input("Select Run ID for Details", min_value=1, step=1)
            if st.button("Load Details"):
                with deps.db_conn_factory() as conn:
                    detail_df = pd.read_sql(f"SELECT * FROM nf_model_runs WHERE id = {selected_id}", conn)
                
                if not detail_df.empty:
                    row = detail_df.iloc[0]
                    st.subheader(f"Run {selected_id}: {row['model_name']}")
                    
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("#### Configuration")
                        st.json(row['optimization_config'])
                        st.markdown("#### Best Parameters")
                        st.json(row['best_params'])
                    
                    with c2:
                        st.markdown("#### Metrics")
                        st.json(row['metrics'])
                        st.markdown("#### Uncertainty / RAG")
                        st.write(f"Uncertainty Score: {row.get('uncertainty_score')}")
                        st.json(row.get('uncertainty_metrics'))
                        
                    if row.get('agent_reasoning'):
                        st.markdown("### 🧠 Agent Reasoning")
                        st.text_area("Log", row['agent_reasoning'], height=200)

        except Exception as exc:
            st.error("Could not fetch history. Ensure DB migration (004_add_agent_rag_schema.sql) is applied.")
            st.exception(exc)


def main() -> None:
    render_app()


if __name__ == "__main__":
    main()