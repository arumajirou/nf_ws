"""High level EasyTSF runner.

YAML/JSON で定義された実験設定を読み込み、TSResearchOrchestrator を
通して実験を登録・実行するための薄いエントリポイントです。

このモジュールは CLI と Python API の両方から利用できます。

制約:
    - DB や TSFM ライブラリが存在しない環境でも import だけは通るように、
      依存関係の解決は遅延インポートで行います。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from nf_loto_platform.agents import (
    AgentOrchestrator,
    CuratorAgent,
    ForecasterAgent,
    PlannerAgent,
    ReporterAgent,
    TimeSeriesTaskSpec,
)
from nf_loto_platform.agents.ts_research_orchestrator import TSResearchOrchestrator
from nf_loto_platform.apps.dependencies import (
    get_llm_client,
    get_model_runner,
    get_ts_research_client,
)


@dataclass
class EasyTSFConfig:
    """EasyTSF 設定のトップレベル表現."""

    raw: Mapping[str, Any]

    @classmethod
    def from_file(cls, path: Path) -> "EasyTSFConfig":
        if not path.exists():
            raise FileNotFoundError(path)
        if path.suffix.lower() in {".json"}:
            data = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix.lower() in {".yml", ".yaml"}:
            try:
                import yaml  # type: ignore
            except Exception as exc:  # pragma: no cover - 環境依存
                raise RuntimeError("PyYAML がインストールされていないため YAML を読み込めません。") from exc
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            raise ValueError(f"Unsupported config extension: {path.suffix}")
        if not isinstance(data, Mapping):
            raise ValueError("config file must contain a mapping at top level")
        return cls(raw=data)

    @property
    def dataset(self) -> Mapping[str, Any]:
        return self.raw.get("dataset", {})

    @property
    def experiment(self) -> Mapping[str, Any]:
        return self.raw.get("experiment", {})

    @property
    def strategy(self) -> Mapping[str, Any]:
        return self.raw.get("strategy", {})


def build_agent_orchestrator(
    *,
    llm_client=None,
    model_runner=None,
) -> AgentOrchestrator:
    """Assemble the default AgentOrchestrator used by EasyTSF."""

    llm = llm_client or get_llm_client()
    _ = model_runner or get_model_runner()

    curator = CuratorAgent(llm)
    planner = PlannerAgent()
    forecaster = ForecasterAgent()
    reporter = ReporterAgent(llm)
    return AgentOrchestrator(curator=curator, planner=planner, forecaster=forecaster, reporter=reporter)


def _coerce_unique_ids(raw: Any) -> Sequence[str]:
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return [str(x) for x in raw]
    raise ValueError("dataset.unique_ids must be a sequence of identifiers")


def _build_task(dataset: Mapping[str, Any], experiment: Mapping[str, Any]) -> TimeSeriesTaskSpec:
    return TimeSeriesTaskSpec(
        loto_kind=str(dataset.get("loto", "loto6")),
        target_horizon=int(experiment.get("horizon", 28)),
        frequency=str(experiment.get("frequency", "W")),
        objective_metric=str(experiment.get("objective", "mae")),
        allow_tsfm=bool(experiment.get("allow_tsfm", True)),
        allow_neuralforecast=bool(experiment.get("allow_neuralforecast", True)),
        allow_classical=bool(experiment.get("allow_classical", False)),
        notes=str(experiment.get("notes", "")),
    )


def run_easytsf(
    config: EasyTSFConfig,
    *,
    tag: Optional[str] = None,
    orchestrator_builder: Callable[..., AgentOrchestrator] | None = None,
) -> Tuple[Any, Any, Mapping[str, int]]:
    """EasyTSF 設定に従って実験を登録・起動する.

    実装上は TSResearchOrchestrator に処理を委譲します。
    ここでは細かい戦略ロジックには踏み込まず、
    「設定を Orchestrator に渡す」責務だけを負います。
    """
    dataset_spec = config.dataset
    experiment_spec = config.experiment

    table_name = str(dataset_spec.get("table") or dataset_spec.get("table_name") or "").strip()
    loto_kind = str(dataset_spec.get("loto", "loto6"))
    if not table_name:
        raise ValueError("dataset.table is required for EasyTSF")

    unique_ids = _coerce_unique_ids(dataset_spec.get("unique_ids"))
    if not unique_ids:
        raise ValueError("dataset.unique_ids must contain at least one entry")

    task = _build_task(dataset_spec, experiment_spec)

    builder = orchestrator_builder or build_agent_orchestrator
    base_orchestrator = builder()
    ts_store = get_ts_research_client()
    ts_orchestrator = TSResearchOrchestrator(base_orchestrator=base_orchestrator, store=ts_store)

    outcome, report, meta = ts_orchestrator.run_full_cycle_with_logging(
        task=task,
        table_name=table_name,
        loto=loto_kind,
        unique_ids=unique_ids,
    )

    if tag is not None:
        meta = {**meta, "tag": tag}

    return outcome, report, meta


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="nf_loto_platform EasyTSF runner")
    parser.add_argument("--config", type=str, required=True, help="YAML or JSON config path")
    parser.add_argument("--tag", type=str, default=None, help="optional experiment tag")
    args = parser.parse_args(argv)

    cfg = EasyTSFConfig.from_file(Path(args.config))
    outcome, _, meta = run_easytsf(cfg, tag=args.tag)
    print(f"[EasyTSF] best_model={outcome.best_model_name} meta={meta}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
