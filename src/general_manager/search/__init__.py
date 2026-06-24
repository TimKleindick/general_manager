"""Search configuration primitives and backend helpers."""

from __future__ import annotations

from general_manager.public_api_registry import SEARCH_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(SEARCH_EXPORTS)

_MODULE_MAP = SEARCH_EXPORTS


def __getattr__(name: str) -> object:
    """
    Resolve and return a named export from this module's public API.

    Parameters:
        name (str): Name of the export to resolve.

    Returns:
        The exported object registered under `name`.

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
    """
    Provide the list of attribute names exposed by this module, including dynamically resolvable exports.

    Returns:
        names (list[str]): Names available on the module for dir() and autocompletion.
    """
    return build_module_dir(module_all=__all__, module_globals=globals())
