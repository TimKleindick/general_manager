"""Factory helpers for generating GeneralManager test data."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AutoFactory",
    "LazyMeasurement",
    "LazyDeltaDate",
    "LazyProjectName",
]

_MODULE_MAP = {
    "AutoFactory": ("general_manager.factory.autoFactory", "AutoFactory"),
    "LazyMeasurement": ("general_manager.factory.factoryMethods", "LazyMeasurement"),
    "LazyDeltaDate": ("general_manager.factory.factoryMethods", "LazyDeltaDate"),
    "LazyProjectName": ("general_manager.factory.factoryMethods", "LazyProjectName"),
}


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = _MODULE_MAP[name]
    module = import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
