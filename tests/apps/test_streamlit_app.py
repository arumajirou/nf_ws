from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from apps.webui_streamlit import streamlit_app


class FakeTab:
    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeSidebar:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.json_payloads: list[object] = []

    def title(self, text: str):
        self.calls.append(("title", text))

    def write(self, text: str):
        self.calls.append(("write", text))

    def subheader(self, text: str):
        self.calls.append(("subheader", text))

    def json(self, payload: object):
        self.json_payloads.append(payload)


class FakeStreamlit:
    def __init__(
        self,
        *,
        selectbox_returns=None,
        multiselect_returns=None,
        radio_returns=None,
        number_input_returns=None,
        checkbox_returns=None,
        text_input_returns=None,
        button_returns=None,
    ):
        self.selectbox_returns = list(selectbox_returns or [])
        self.multiselect_returns = list(multiselect_returns or [])
        self.radio_returns = list(radio_returns or [])
        self.number_input_returns = list(number_input_returns or [])
        self.checkbox_returns = list(checkbox_returns or [])
        self.text_input_returns = list(text_input_returns or [])
        self.button_returns = list(button_returns or [])

        self.sidebar = FakeSidebar()
        self.success_messages: list[str] = []
        self.info_messages: list[str] = []
        self.warning_messages: list[str] = []
        self.exception_messages: list[str] = []
        self.spinner_messages: list[str] = []
        self.dataframes: list[pd.DataFrame] = []
        self.json_payloads: list[object] = []
        self.tabs_called_with: list[list[str]] = []

    # Layout helpers -----------------------------------------------------
    def set_page_config(self, **kwargs):
        pass

    def tabs(self, labels: list[str]):
        self.tabs_called_with.append(labels)
        return tuple(FakeTab(label) for label in labels)

    def header(self, *_args, **_kwargs):
        pass

    def subheader(self, *_args, **_kwargs):
        pass

    def markdown(self, *_args, **_kwargs):
        pass

    def info(self, message: str, **_kwargs):
        self.info_messages.append(message)

    # Widgets ------------------------------------------------------------
    def selectbox(self, label: str, *, options, index=0, **_kwargs):
        if self.selectbox_returns:
            value = self.selectbox_returns.pop(0)
            if options and value not in options:
                raise AssertionError(f"{value} not in {options}")
            return value
        if not options:
            raise AssertionError(f"{label} options empty")
        return options[index]

    def multiselect(self, label: str, *, options, default=None, **_kwargs):
        if self.multiselect_returns:
            value = self.multiselect_returns.pop(0)
            return list(value)
        if default:
            return list(default)
        return list(options)

    def radio(self, label: str, *, options, index=0, **_kwargs):
        if self.radio_returns:
            value = self.radio_returns.pop(0)
            if value not in options:
                raise AssertionError(f"{value} not in {options}")
            return value
        return options[index]

    def number_input(self, label: str, *, value, **_kwargs):
        if self.number_input_returns:
            return self.number_input_returns.pop(0)
        return value

    def checkbox(self, label: str, *, value=False, **_kwargs):
        if self.checkbox_returns:
            return self.checkbox_returns.pop(0)
        return value

    def text_input(self, label: str, *, value="", **_kwargs):
        if self.text_input_returns:
            return self.text_input_returns.pop(0)
        return value

    def button(self, label: str, **_kwargs):
        if self.button_returns:
            return self.button_returns.pop(0)
        return False

    def spinner(self, message: str):
        parent = self

        class _Spinner:
            def __enter__(self):
                parent.spinner_messages.append(message)

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Spinner()

    # Output calls -------------------------------------------------------
    def success(self, message: str):
        self.success_messages.append(message)

    def error(self, message: str):
        raise AssertionError(f"error called: {message}")

    def warning(self, message: str):
        self.warning_messages.append(message)

    def exception(self, exc: Exception):
        self.exception_messages.append(str(exc))

    def dataframe(self, df: pd.DataFrame):
        self.dataframes.append(df)

    def json(self, payload: object):
        self.json_payloads.append(payload)


class DummyRepo:
    def list_loto_tables(self):
        return pd.DataFrame({"tablename": ["nf_loto_panel"]})

    def list_loto_values(self, table_name: str):
        assert table_name == "nf_loto_panel"
        return pd.DataFrame({"loto": ["loto6", "loto7"]})

    def list_unique_ids(self, table_name: str, loto: str):
        assert table_name == "nf_loto_panel"
        assert loto == "loto6"
        return pd.DataFrame({"unique_id": ["S1", "S2", "S3"]})


class DummyModelRunner:
    def __init__(self):
        self.run_calls: list[dict[str, object]] = []
        self.sweep_calls: list[dict[str, object]] = []

    def run_loto_experiment(self, **kwargs):
        self.run_calls.append(kwargs)
        preds = pd.DataFrame({"unique_id": ["S1"], "y": [1.0]})
        meta = {"run_id": 123, "status": "finished"}
        return preds, meta

    def sweep_loto_experiments(self, **kwargs):
        self.sweep_calls.append(kwargs)
        result = SimpleNamespace(
            preds=pd.DataFrame({"unique_id": ["S1"], "y": [2.0]}),
            meta={"run_id": 456, "status": "queued"},
        )
        return [result]


class DummyDBConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fetch_dataframe_handles_errors_and_missing_columns():
    def failing_loader():
        raise RuntimeError("boom")

    df1 = streamlit_app._fetch_dataframe(failing_loader, columns=["a", "b"])
    assert list(df1.columns) == ["a", "b"]
    assert df1.empty

    def missing_column_loader():
        return pd.DataFrame({"unique_id": ["S1"]})

    df2 = streamlit_app._fetch_dataframe(missing_column_loader, columns=["unique_id", "loto"])
    assert "unique_id" in df2.columns
    assert "loto" in df2.columns
    assert df2["loto"].isna().all()


def test_render_app_runs_default_mode(monkeypatch):
    fake_st = FakeStreamlit(
        selectbox_returns=["nf_loto_panel", "loto6"],
        multiselect_returns=[["S1", "S2"], ["AutoNHITS"], ["optuna"]],
        radio_returns=["defaults"],
        number_input_returns=[5, 2, 0, 4],
        checkbox_returns=[True],
        button_returns=[True],
    )

    repo = DummyRepo()
    runner = DummyModelRunner()
    history_df = pd.DataFrame({"id": [1], "status": ["running"]})
    read_sql_calls: list[str] = []
    monkeypatch.setattr(streamlit_app, "list_automodel_names", lambda: ["AutoNHITS", "AutoLSTM"])
    monkeypatch.setattr(
        streamlit_app.pd,
        "read_sql",
        lambda query, conn: read_sql_calls.append(query) or history_df,
    )

    deps = streamlit_app.WebUIDependencies(
        repo=repo,
        model_runner=runner,
        db_conn_factory=lambda: DummyDBConnection(),
        db_config={"host": "localhost"},
    )

    streamlit_app.render_app(st_module=fake_st, deps=deps)

    assert runner.run_calls and not runner.sweep_calls
    call = runner.run_calls[0]
    assert call["table_name"] == "nf_loto_panel"
    assert call["loto"] == "loto6"
    assert call["unique_ids"] == ["S1", "S2"]
    assert call["model_name"] == "AutoNHITS"
    assert call["backend"] == "optuna"
    assert call["num_samples"] == 5
    assert call["cpus"] == 2
    assert call["gpus"] == 0
    assert call["early_stop"] is True
    assert call["early_stop_patience_steps"] == 4
    assert fake_st.success_messages[-1] == "run_id=123 で実行完了"
    assert fake_st.spinner_messages == ["実験を実行中..."]
    assert read_sql_calls and "nf_model_runs" in read_sql_calls[0]
    assert len(fake_st.dataframes) == 2  # preds + history
    assert not fake_st.exception_messages


def test_render_app_runs_grid_sweep(monkeypatch):
    fake_st = FakeStreamlit(
        selectbox_returns=["nf_loto_panel", "loto6"],
        multiselect_returns=[["S2"], ["AutoNHITS", "AutoLSTM"], ["local", "optuna"]],
        radio_returns=["grid"],
        number_input_returns=[8, 3, 1, 2],
        checkbox_returns=[False],
        text_input_returns=[
            "mae,rmse",
            "28,56",
            "D",
            "robust,simple",
            "14,28",
            "true,false",
            "false,true",
        ],
        button_returns=[True],
    )

    repo = DummyRepo()
    runner = DummyModelRunner()
    history_df = pd.DataFrame({"id": [2], "status": ["succeeded"]})
    read_sql_calls: list[str] = []
    monkeypatch.setattr(streamlit_app, "list_automodel_names", lambda: ["AutoNHITS", "AutoLSTM"])
    monkeypatch.setattr(
        streamlit_app.pd,
        "read_sql",
        lambda query, conn: read_sql_calls.append(query) or history_df,
    )

    deps = streamlit_app.WebUIDependencies(
        repo=repo,
        model_runner=runner,
        db_conn_factory=lambda: DummyDBConnection(),
        db_config={"host": "localhost"},
    )

    streamlit_app.render_app(st_module=fake_st, deps=deps)

    assert not runner.run_calls
    assert runner.sweep_calls
    call = runner.sweep_calls[0]
    assert call["mode"] == "grid"
    assert call["model_names"] == ["AutoNHITS", "AutoLSTM"]
    assert call["backends"] == ["local", "optuna"]
    assert call["unique_ids"] == ["S2"]
    assert call["param_spec"]["objective"] == ["mae", "rmse"]
    assert call["param_spec"]["h"] == [28, 56]
    assert call["param_spec"]["refit_with_val"] == [True, False]
    assert call["param_spec"]["use_init_models"] == [False, True]
    assert fake_st.success_messages[-1] == "1 本の実験を実行しました。"
    assert fake_st.spinner_messages == ["実験を実行中..."]
    assert len(fake_st.dataframes) == 2  # sweep result + history
    assert read_sql_calls and "ORDER BY id DESC" in read_sql_calls[0]
    assert not fake_st.exception_messages


# To run:
#   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/apps/test_streamlit_app.py -q
