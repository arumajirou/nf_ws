"""ts_research_store のクエリヘルパーの形だけ確認するテスト.

実 DB には依存せず、TSResearchStore.get_metrics_for_experiment の存在と
関数シグネチャを確認することに主眼を置く。
"""

from __future__ import annotations

from inspect import signature

from nf_loto_platform.db.ts_research_store import TSResearchStore
from nf_loto_platform.db.db_config import DB_CONFIG


def test_ts_research_store_has_get_metrics_for_experiment() -> None:
    store = TSResearchStore(DB_CONFIG)
    fn = getattr(store, "get_metrics_for_experiment")
    sig = signature(fn)
    params = list(sig.parameters.values())
    # self + experiment_id の 2 引数であることを確認
    assert len(params) == 2
    assert params[1].name == "experiment_id"
