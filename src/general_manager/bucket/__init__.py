"""Bucket utilities for GeneralManager."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["Bucket", "DatabaseBucket", "CalculationBucket", "GroupBucket"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_map = {
        "Bucket": "baseBucket",
        "DatabaseBucket": "databaseBucket",
        "CalculationBucket": "calculationBucket",
        "GroupBucket": "groupBucket",
    }
    module = import_module(f"{__name__}.{module_map[name]}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
