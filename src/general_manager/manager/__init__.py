"""Convenience re-exports for manager utilities."""

from __future__ import annotations

from typing import Any

from general_manager.utils.public_api import build_module_dir, resolve_export

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
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
