"""Convenience re-exports for manager utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from general_manager.public_api_registry import MANAGER_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(MANAGER_EXPORTS)

_MODULE_MAP = MANAGER_EXPORTS

if TYPE_CHECKING:
    from general_manager._types.manager import *  # noqa: F403


def __getattr__(name: str) -> Any:
    """
    Resolve and return a public export by name for dynamic attribute access.

    Parameters:
        name (str): The attribute name to resolve.

    Returns:
        Any: The object bound to the requested public name.
    """
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
