import importlib
import inspect

import nf_loto_platform.monitoring.prometheus_metrics as pm


class _MetricBinding:
    def __init__(self, metric, labels):
        self.metric = metric
        self.labels = labels

    def inc(self):
        self.metric.records.append({"op": "inc", "labels": self.labels})

    def observe(self, value):
        self.metric.records.append({"op": "observe", "labels": self.labels, "value": value})

    def set(self, value):
        self.metric.records.append({"op": "set", "labels": self.labels, "value": value})


class DummyMetric:
    """prometheus_client の Counter/Gauge/Histogram を模倣する簡易スタブ。"""

    def __init__(self):
        self.records: list[dict] = []

    def labels(self, **labels):
        return _MetricBinding(self, labels)


def test_prometheus_metrics_module_present():
    spec = importlib.util.find_spec("nf_loto_platform.monitoring.prometheus_metrics")
    assert spec is not None


def test_prometheus_metrics_public_api_contains_expected_functions():
    """Basic API contract: required functions must exist.

    This helps detect accidental renames/removals that would break callers
    like nf_loto_platform.ml.model_runner.
    """
    funcs = {name for name, obj in inspect.getmembers(pm, inspect.isfunction)}
    required = {
        "init_metrics_server",
        "observe_run_start",
        "observe_run_end",
        "observe_run_error",
        "observe_train_step",
    }
    assert required.issubset(funcs)


def test_prometheus_metrics_functions_are_noop_without_prom_client(monkeypatch):
    """When prometheus_client is not available, helpers must safely no-op."""
    # Force the module into "prometheus not available" mode.
    monkeypatch.setattr(pm, "_PROM_AVAILABLE", False, raising=False)

    # None of these calls should raise, even without prometheus_client installed.
    pm.init_metrics_server(port=9999)
    pm.observe_run_start(model_name="TestModel", backend="local")
    pm.observe_run_end(
        model_name="TestModel",
        backend="local",
        status="success",
        duration_seconds=0.1,
        resource_after=None,
    )
    pm.observe_run_error(model_name="TestModel", backend="local")
    pm.observe_train_step(
        model_name="TestModel",
        backend="local",
        train_loss=0.5,
        val_loss=0.4,
    )


def test_init_metrics_server_starts_only_once(monkeypatch):
    monkeypatch.setattr(pm, "_PROM_AVAILABLE", True, raising=False)
    monkeypatch.setattr(pm, "_METRICS_SERVER_STARTED", False, raising=False)
    started_ports: list[int] = []

    def fake_start_http_server(port: int):
        started_ports.append(port)

    monkeypatch.setattr(pm, "start_http_server", fake_start_http_server)

    pm.init_metrics_server(port=9100)
    pm.init_metrics_server(port=9200)

    assert started_ports == [9100]
    assert pm._METRICS_SERVER_STARTED is True


def test_observe_run_start_updates_counter(monkeypatch):
    metric = DummyMetric()
    monkeypatch.setattr(pm, "_PROM_AVAILABLE", True, raising=False)
    monkeypatch.setattr(pm, "RUNS_STARTED", metric)

    pm.observe_run_start(model_name="ModelA", backend="local")

    assert metric.records == [
        {"op": "inc", "labels": {"model_name": "ModelA", "backend": "local"}}
    ]


def test_observe_run_end_updates_counters_and_histogram(monkeypatch):
    completed = DummyMetric()
    duration = DummyMetric()
    monkeypatch.setattr(pm, "_PROM_AVAILABLE", True, raising=False)
    monkeypatch.setattr(pm, "RUNS_COMPLETED", completed)
    monkeypatch.setattr(pm, "RUN_DURATION", duration)

    pm.observe_run_end(
        model_name="ModelA",
        backend="local",
        status="success",
        duration_seconds=12.5,
        resource_after={"cpu": 50},
    )

    assert completed.records == [
        {"op": "inc", "labels": {"model_name": "ModelA", "backend": "local", "status": "success"}}
    ]
    assert duration.records == [
        {
            "op": "observe",
            "labels": {"model_name": "ModelA", "backend": "local", "status": "success"},
            "value": 12.5,
        }
    ]


def test_observe_train_step_sets_gauges(monkeypatch):
    train_metric = DummyMetric()
    val_metric = DummyMetric()
    monkeypatch.setattr(pm, "_PROM_AVAILABLE", True, raising=False)
    monkeypatch.setattr(pm, "TRAIN_LOSS", train_metric)
    monkeypatch.setattr(pm, "VAL_LOSS", val_metric)

    pm.observe_train_step("ModelA", "local", train_loss=0.33, val_loss=0.44)
    pm.observe_train_step("ModelA", "local", train_loss=None, val_loss=None)

    assert train_metric.records == [
        {"op": "set", "labels": {"model_name": "ModelA", "backend": "local"}, "value": 0.33}
    ]
    assert val_metric.records == [
        {"op": "set", "labels": {"model_name": "ModelA", "backend": "local"}, "value": 0.44}
    ]
