"""Convenience access to GeneralManager core components."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from general_manager.public_api_registry import GENERAL_MANAGER_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(GENERAL_MANAGER_EXPORTS)

_MODULE_MAP = GENERAL_MANAGER_EXPORTS

if TYPE_CHECKING:
    from general_manager._types.general_manager import *  # noqa: F403


def __getattr__(name: str) -> Any:
    """
    Dynamically resolve and return a requested export when a module attribute is accessed but not defined.

    Parameters:
        name (str): The attribute name being accessed on the module.

    Returns:
        Any: The resolved export object or value corresponding to `name`.
    """
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
