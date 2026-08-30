"""Canonical connector exports."""
import importlib
from typing import Any

__all__ = [
    "postgres",
    "ibkr",
    "ntfy",
    "tradingedge_dexgex",
    "tradingedge_optionflow",
]

_MODULE_MAP = {
    "postgres": "common_lib.connectors.postgres",
    "ibkr": "common_lib.connectors.ibkr",
    "ntfy": "common_lib.connectors.nfty",
    "nfty": "common_lib.connectors.nfty",
    "tradingedge_dexgex": "common_lib.connectors.tradingedge.dexgex",
    "tradingedge_optionflow": "common_lib.connectors.tradingedge.optionflow",
}


def __getattr__(name: str) -> Any:
    if name in _MODULE_MAP:
        module = importlib.import_module(_MODULE_MAP[name])
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


