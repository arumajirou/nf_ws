from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "apps" / "webui_streamlit" / "nf_auto_runner_full.py"


def _install_stub_pipeline(monkeypatch):
    module_name = "nf_loto_platform.pipelines.training_pipeline"
    stub = ModuleType(module_name)
    calls = {"count": 0}

    def fake_runner():
        calls["count"] += 1

    stub.run_legacy_nf_auto_runner = fake_runner
    monkeypatch.setitem(sys.modules, module_name, stub)
    return calls


def test_nf_auto_runner_invokes_pipeline_when_executed(monkeypatch):
    calls = _install_stub_pipeline(monkeypatch)

    runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    assert calls["count"] == 1


def test_nf_auto_runner_is_noop_when_imported(monkeypatch):
    calls = _install_stub_pipeline(monkeypatch)

    runpy.run_path(str(SCRIPT_PATH), run_name="not_main")

    assert calls["count"] == 0


# To run:
#   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/apps/test_nf_auto_runner_full.py -q
