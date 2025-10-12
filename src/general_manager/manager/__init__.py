"""Convenience re-exports for manager utilities."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "GeneralManager",
    "Input",
    "graphQlProperty",
    "GeneralManagerMeta",
    "GroupManager",
]

_MODULE_MAP = {
    "GeneralManager": ("general_manager.manager.generalManager", "GeneralManager"),
    "GeneralManagerMeta": ("general_manager.manager.meta", "GeneralManagerMeta"),
    "Input": ("general_manager.manager.input", "Input"),
    "GroupManager": ("general_manager.manager.groupManager", "GroupManager"),
    "graphQlProperty": ("general_manager.api.property", "graphQlProperty"),
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
