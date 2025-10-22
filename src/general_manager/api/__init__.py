"""GraphQL helpers for GeneralManager."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from general_manager.public_api_registry import API_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(API_EXPORTS)

_MODULE_MAP = API_EXPORTS

if TYPE_CHECKING:
    from general_manager._types.api import *  # noqa: F403


def __getattr__(name: str) -> Any:
    """
    Dynamically resolve and return a named export from this module.

    Parameters:
        name (str): The attribute name to resolve from the module's public API.

    Returns:
        The resolved exported object corresponding to `name`.

    Raises:
        AttributeError: If `name` is not a registered export for this module.
    """
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
