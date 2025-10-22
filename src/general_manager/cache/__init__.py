"""Caching helpers for GeneralManager dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from general_manager.public_api_registry import CACHE_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(CACHE_EXPORTS)

_MODULE_MAP = CACHE_EXPORTS

if TYPE_CHECKING:
    from general_manager._types.cache import *  # noqa: F403


def __getattr__(name: str) -> Any:
    """
    Resolve a public API export by attribute name for module-level dynamic access.

    Parameters:
        name (str): The attribute name being accessed on the module.

    Returns:
        Any: The object exported under `name` from the module's cached public API, or raises AttributeError if not found.
    """
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
