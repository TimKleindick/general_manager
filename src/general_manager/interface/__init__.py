"""Public interface classes for GeneralManager implementations."""

from __future__ import annotations

from typing import Any

from general_manager.utils.public_api import build_module_dir, resolve_export

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
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
