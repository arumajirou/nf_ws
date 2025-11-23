from __future__ import annotations

from nf_loto_platform.monitoring import resource_monitor


class DummyPsutil:
    """psutil API を最小限だけ再現するテスト用スタブ。"""

    class _VM:
        total = 1024
        used = 512
        percent = 50.0

    class _ProcMem:
        rss = 256
        vms = 2048

    class _Proc:
        def memory_info(self):
            return DummyPsutil._ProcMem()

    def cpu_percent(self, interval=None):
        return 42.5

    def virtual_memory(self):
        return DummyPsutil._VM()

    def Process(self):
        return DummyPsutil._Proc()


def test_collect_resource_snapshot_without_psutil(monkeypatch):
    """psutil 未インストール時でもフォールバック値を返すことを確認。"""

    monkeypatch.setattr(resource_monitor, "_PSUTIL_AVAILABLE", False, raising=False)
    monkeypatch.setattr(resource_monitor, "psutil", None, raising=False)
    monkeypatch.setattr(resource_monitor.platform, "system", lambda: "TestOS")
    monkeypatch.setattr(resource_monitor.platform, "release", lambda: "1.2")
    monkeypatch.setattr(resource_monitor.time, "time", lambda: 123.45)

    snapshot = resource_monitor.collect_resource_snapshot()

    assert snapshot["platform"] == "TestOS"
    assert snapshot["platform_release"] == "1.2"
    assert snapshot["timestamp"] == 123.45
    assert snapshot["cpu_percent"] == 0.0


def test_collect_resource_snapshot_with_psutil(monkeypatch):
    """psutil が利用可能な場合、主要なメトリクスを含めて返す。"""

    monkeypatch.setattr(resource_monitor, "_PSUTIL_AVAILABLE", True, raising=False)
    monkeypatch.setattr(resource_monitor, "psutil", DummyPsutil(), raising=False)
    monkeypatch.setattr(resource_monitor.platform, "system", lambda: "Linux")
    monkeypatch.setattr(resource_monitor.platform, "release", lambda: "6.0")
    monkeypatch.setattr(resource_monitor.time, "time", lambda: 999.0)

    snapshot = resource_monitor.collect_resource_snapshot()

    assert snapshot["cpu_percent"] == 42.5
    assert snapshot["memory_total"] == 1024
    assert snapshot["memory_used"] == 512
    assert snapshot["memory_percent"] == 50.0
    assert snapshot["process_rss"] == 256
    assert snapshot["process_vms"] == 2048


def test_collect_resource_usage_is_alias(monkeypatch):
    """collect_resource_usage は collect_resource_snapshot を単純に委譲する。"""

    calls = {"count": 0}

    def fake_snapshot():
        calls["count"] += 1
        return {"ok": True}

    monkeypatch.setattr(resource_monitor, "collect_resource_snapshot", fake_snapshot)

    assert resource_monitor.collect_resource_usage() == {"ok": True}
    assert calls["count"] == 1


# To run:
#   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/monitoring/test_resource_monitor.py -q
