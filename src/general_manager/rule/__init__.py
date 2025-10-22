"""Helpers for defining rule-based validations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from general_manager.public_api_registry import RULE_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(RULE_EXPORTS)

_MODULE_MAP = RULE_EXPORTS

if TYPE_CHECKING:
    from general_manager._types.rule import *  # noqa: F403


def __getattr__(name: str) -> Any:
    """
    Dynamically resolve a missing module attribute using the module's export registry.

    Parameters:
        name (str): The attribute name being accessed on the module.

    Returns:
        The attribute value associated with `name` from the module's export registry, or a fallback value if the name cannot be resolved.
    """
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
