"""Bucket utilities for GeneralManager."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from general_manager.public_api_registry import BUCKET_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(BUCKET_EXPORTS)

_MODULE_MAP = BUCKET_EXPORTS

if TYPE_CHECKING:
    from general_manager._types.bucket import *  # noqa: F403


def __getattr__(name: str) -> Any:
    """
    Dynamically resolve and return a named bucket export from this module's public API.

    Parameters:
        name (str): The attribute name to resolve from the module's exports.

    Returns:
        Any: The resolved export object for `name`.
    """
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
