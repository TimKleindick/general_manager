"""Dataframe import/export helpers for GeneralManager values."""

from __future__ import annotations

from typing import TYPE_CHECKING

from general_manager.public_api_registry import DATAFRAME_EXPORTS
from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = list(DATAFRAME_EXPORTS)

_MODULE_MAP = DATAFRAME_EXPORTS

if TYPE_CHECKING:
    from general_manager._types.dataframes import *  # noqa: F403


def __getattr__(name: str) -> object:
    """Dynamically resolve dataframe helper exports."""
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
