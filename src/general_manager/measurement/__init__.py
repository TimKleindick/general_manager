"""Public API for measurement utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from general_manager.public_api_registry import MEASUREMENT_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(MEASUREMENT_EXPORTS)

_MODULE_MAP = MEASUREMENT_EXPORTS

if TYPE_CHECKING:
    from general_manager._types.measurement import *  # noqa: F403


def __getattr__(name: str) -> Any:
    """
    Dynamically resolve and return a public API attribute by name.

    Parameters:
        name (str): The attribute name requested from the module's public API.

    Returns:
        Any: The object or submodule bound to `name` as defined by the module export mapping.
    """
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
