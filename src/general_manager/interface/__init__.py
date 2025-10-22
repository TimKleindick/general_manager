"""Public interface classes for GeneralManager implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from general_manager.public_api_registry import INTERFACE_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(INTERFACE_EXPORTS)

_MODULE_MAP = INTERFACE_EXPORTS

if TYPE_CHECKING:
    from general_manager._types.interface import *  # noqa: F403


def __getattr__(name: str) -> Any:
    """
    Lazily resolve a public API export and return the object for the given attribute name.

    Parameters:
        name (str): Name of the attribute to resolve from the module's public exports.

    Returns:
        Any: The resolved export object associated with `name`.
    """
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
