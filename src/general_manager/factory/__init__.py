"""Factory helpers for generating GeneralManager test data."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from general_manager.public_api_registry import FACTORY_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(FACTORY_EXPORTS)

_MODULE_MAP = FACTORY_EXPORTS

if TYPE_CHECKING:
    from general_manager._types.factory import *  # noqa: F403


def __getattr__(name: str) -> Any:
    """
    Dynamically resolve and return a named export from this module.

    Parameters:
        name (str): The attribute name to resolve.

    Returns:
        Any: The resolved attribute object corresponding to `name`.
    """
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
