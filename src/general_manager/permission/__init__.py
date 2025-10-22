"""Permission helpers for GeneralManager mutations and actions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from general_manager.public_api_registry import PERMISSION_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(PERMISSION_EXPORTS)

_MODULE_MAP = PERMISSION_EXPORTS

if TYPE_CHECKING:
    from general_manager._types.permission import *  # noqa: F403


def __getattr__(name: str) -> Any:
    """
    Resolve and return a lazily-exposed public attribute for this module.

    Parameters:
        name (str): The attribute name being accessed on the module.

    Returns:
        Any: The resolved attribute value associated with `name`.
    """
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
