"""Streamlit entry point for nf_loto_platform's lightweight WebUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

from nf_loto_platform.apps.dependencies import get_db_conn, get_model_runner
from nf_loto_platform.core.settings import load_db_config
from nf_loto_platform.db import loto_repository
from nf_loto_platform.ml.model_registry import list_automodel_names

# ---------------------------------------------------------------------------
# Dependency container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebUIDependencies:
    """Bundle of dependencies used by the WebUI.

    Keeping this in one place makes it easy to inject fakes in smoke tests.
    """

    repo: Any
    model_runner: Any
    db_conn_factory: Callable[[], Any]
    db_config: Mapping[str, Any]


def build_webui_dependencies() -> WebUIDependencies:
    """Return the default dependency bundle."""

    return WebUIDependencies(
        repo=loto_repository,
        model_runner=get_model_runner(),
        db_conn_factory=get_db_conn,
        db_config=load_db_config() or {},
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
    except Exception:  # noqa: BLE001 - UI レイヤーでは詳細を表示
        df = pd.DataFrame(columns=columns)
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    missing = [col for col in columns if col not in df.columns]
    if missing:
        for col in missing:
            df[col] = None
    return df


def _selectbox_default(options: Sequence[str], default: str) -> str:
    return options[0] if options else default


# ---------------------------------------------------------------------------
# UI renderer
# ---------------------------------------------------------------------------


def render_app(st_module=None, deps: WebUIDependencies | None = None) -> None:
    """Render the Streamlit application (all Streamlit calls live inside here)."""

    st = st_module or __import__("streamlit")
    deps = deps or build_webui_dependencies()

    repo = deps.repo
    model_runner = deps.model_runner

    st.set_page_config(page_title="nf_loto_webui", layout="wide")

    st.sidebar.title("nf_loto_webui")
    st.sidebar.write("nf_loto% テーブルから実験を送信する簡易 UI")
    st.sidebar.subheader("DB 接続情報")
    st.sidebar.json(deps.db_config or {"message": "config/db.yaml を設定してください"})

    tab_run, tab_history = st.tabs(["🧪 実験実行", "📈 実行履歴"])

    with tab_run:
        st.header("ロト実験の実行")

        tables_df = _fetch_dataframe(repo.list_loto_tables, columns=["tablename"])
        table_options = _safe_options(tables_df["tablename"].tolist(), ["nf_loto_panel"])
        table_name = st.selectbox("対象テーブル (nf_loto%)", options=table_options, index=0)

        def _load_loto_values() -> pd.DataFrame:
            return repo.list_loto_values(table_name)

        loto_df = _fetch_dataframe(_load_loto_values, columns=["loto"])
        loto_options = _safe_options(loto_df["loto"].tolist(), ["loto6"])
        loto_value = st.selectbox("loto", options=loto_options, index=0)

        def _load_unique_ids() -> pd.DataFrame:
            return repo.list_unique_ids(table_name, loto_value)

        uid_df = _fetch_dataframe(_load_unique_ids, columns=["unique_id"])
        uid_options = _safe_options(uid_df["unique_id"].tolist(), ["S1", "S2"])
        default_ids = uid_options[: min(3, len(uid_options))]
        unique_ids = st.multiselect("unique_ids", options=uid_options, default=default_ids or uid_options)

        st.subheader("モデル / backend / 探索モード")

        model_names = st.multiselect(
            "AutoModel",
            options=list_automodel_names(),
            default=["AutoNHITS"],
            help="TSFM/NeuralForecast の両モデルを選択可能",
        )
        backends = st.multiselect("backend", options=["ray", "optuna", "local"], default=["optuna"])

        mode = st.radio(
            "パラメータ探索モード",
            options=["defaults", "grid"],
            index=0,
            help="defaults: 単一設定のみ / grid: パラメータリストを直積で探索",
            horizontal=True,
        )

        st.markdown("### 共通設定")

        num_samples = st.number_input("num_samples", min_value=1, max_value=1000, value=10, step=1)
        cpus = st.number_input("cpus", min_value=1, max_value=64, value=1)
        gpus = st.number_input("gpus", min_value=0, max_value=8, value=0)
        early_stop = st.checkbox("アーリーストッピングを有効にする", value=True)
        early_stop_patience = st.number_input("early_stop_patience_steps", min_value=-1, max_value=1000, value=3)

        param_spec: dict[str, Any] = {}

        if mode == "defaults":
            st.info("defaults モード: horizon/freq などはデフォルト 1 通りのみを実行します。")
            param_spec["early_stop"] = early_stop
            param_spec["early_stop_patience_steps"] = early_stop_patience
        else:
            st.markdown("### グリッド検索パラメータ (カンマ区切り)")

            def _parse_list(raw: str, cast=str) -> list[Any]:
                vals = [x.strip() for x in raw.split(",") if x.strip()]
                return [cast(x) for x in vals]

            loss_str = st.text_input("objective 候補 (例: mae,rmse)", value="mae")
            h_str = st.text_input("horizon 候補 (例: 28,56)", value="28")
            freq_str = st.text_input("freq 候補 (例: D,W,M)", value="D")
            local_scaler_str = st.text_input("local_scaler_type 候補", value="robust")
            val_size_str = st.text_input("val_size 候補", value="28")
            refit_str = st.text_input("refit_with_val 候補 (true,false)", value="true")
            use_init_str = st.text_input("use_init_models 候補 (false,true)", value="false")

            param_spec = {
                "objective": _parse_list(loss_str, str),
                "h": [int(x) for x in _parse_list(h_str, int)],
                "freq": _parse_list(freq_str, str),
                "local_scaler_type": _parse_list(local_scaler_str, str),
                "val_size": [int(x) for x in _parse_list(val_size_str, int)],
                "refit_with_val": [v.lower() == "true" for v in _parse_list(refit_str, str)],
                "use_init_models": [v.lower() == "true" for v in _parse_list(use_init_str, str)],
                "early_stop": [early_stop],
                "early_stop_patience_steps": [early_stop_patience],
            }

        if st.button("実験を実行", type="primary"):
            if not unique_ids:
                st.error("少なくとも 1 つ unique_id を選択してください。")
            elif not model_names:
                st.error("少なくとも 1 つ AutoModel を選択してください。")
            elif not backends:
                st.error("少なくとも 1 つ backend を選択してください。")
            else:
                with st.spinner("実験を実行中..."):
                    try:
                        if mode == "defaults":
                            preds, meta = model_runner.run_loto_experiment(
                                table_name=table_name,
                                loto=loto_value,
                                unique_ids=unique_ids,
                                model_name=model_names[0],
                                backend=backends[0],
                                horizon=28,
                                objective="mae",
                                secondary_metric="val_loss",
                                num_samples=num_samples,
                                cpus=cpus,
                                gpus=gpus,
                                search_space=None,
                                freq="D",
                                local_scaler_type="robust",
                                val_size=28,
                                refit_with_val=True,
                                use_init_models=False,
                                early_stop=early_stop,
                                early_stop_patience_steps=early_stop_patience,
                            )
                            st.success(f"run_id={meta.get('run_id')} で実行完了")
                            st.dataframe(preds.head())
                            st.json(meta)
                        else:
                            results = model_runner.sweep_loto_experiments(
                                table_name=table_name,
                                loto=loto_value,
                                unique_ids=unique_ids,
                                model_names=model_names,
                                backends=backends,
                                param_spec=param_spec,
                                mode="grid",
                                objective=str(param_spec.get("objective", ["mae"])[0]),
                                secondary_metric="val_loss",
                                num_samples=num_samples,
                                cpus=cpus,
                                gpus=gpus,
                            )
                            st.success(f"{len(results)} 本の実験を実行しました。")
                            if results:
                                st.dataframe(results[0].preds.head())
                                st.json(results[0].meta)
                    except Exception as exc:  # noqa: BLE001 - surfaced in UI
                        st.exception(exc)

    with tab_history:
        st.header("実行履歴 (nf_model_runs)")
        try:
            with deps.db_conn_factory() as conn:
                df_hist = pd.read_sql("SELECT * FROM nf_model_runs ORDER BY id DESC LIMIT 500", conn)
            st.dataframe(df_hist)
        except Exception as exc:  # noqa: BLE001 - UI guard
            st.warning("nf_model_runs を取得できませんでした。テーブルを作成済みか確認してください。")
            st.exception(exc)


def main() -> None:  # pragma: no cover - exercised via streamlit run
    render_app()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    main()
