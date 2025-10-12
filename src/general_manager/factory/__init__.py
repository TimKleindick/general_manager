"""Factory helpers for generating GeneralManager test data."""

from __future__ import annotations

from typing import Any

from general_manager.utils.public_api import build_module_dir, resolve_export

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
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
