from __future__ import annotations

from typing import Dict, Type

from .base import BaseTSFMAdapter
from .chronos_adapter import Chronos2ZeroShotAdapter

_ADAPTERS: Dict[str, Type[BaseTSFMAdapter]] = {
    "Chronos2-ZeroShot": Chronos2ZeroShotAdapter,
}


def get_adapter(model_name: str) -> BaseTSFMAdapter:
    try:
        adapter_cls = _ADAPTERS[model_name]
    except KeyError as exc:  # pragma: no cover - defensive guard
        raise ValueError(f"TSFM adapter for {model_name!r} is not registered") from exc
    return adapter_cls()
