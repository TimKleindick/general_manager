"""Public interface classes for GeneralManager implementations."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "InterfaceBase",
    "DBBasedInterface",
    "DatabaseInterface",
    "ReadOnlyInterface",
    "CalculationInterface",
]

_MODULE_MAP = {
    "InterfaceBase": "general_manager.interface.baseInterface",
    "DBBasedInterface": "general_manager.interface.databaseBasedInterface",
    "DatabaseInterface": "general_manager.interface.databaseInterface",
    "ReadOnlyInterface": "general_manager.interface.readOnlyInterface",
    "CalculationInterface": "general_manager.interface.calculationInterface",
}


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(_MODULE_MAP[name])
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
