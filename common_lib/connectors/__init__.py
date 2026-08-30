"""Canonical connector exports."""
from typing import Any

__all__ = [
    "postgres",
    "ibkr",
    "ntfy",
    "tradingedge_dexgex",
    "tradingedge_optionflow",
]


def __getattr__(name: str) -> Any:
    if name == "postgres":
        from common_lib.connectors import postgres
        return postgres
    elif name == "ibkr":
        from common_lib.connectors import ibkr
        return ibkr
    elif name == "ntfy" or name == "nfty":
        from common_lib.connectors import nfty
        return nfty
    elif name == "tradingedge_dexgex":
        from common_lib.connectors.tradingedge import dexgex
        return dexgex
    elif name == "tradingedge_optionflow":
        from common_lib.connectors.tradingedge import optionflow
        return optionflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

