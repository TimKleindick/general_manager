"""Convenience re-exports for common utility helpers."""

from __future__ import annotations

from typing import Any

from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = [
    "noneToZero",
    "args_to_kwargs",
    "make_cache_key",
    "parse_filters",
    "create_filter_function",
    "snake_to_pascal",
    "snake_to_camel",
    "pascal_to_snake",
    "camel_to_snake",
]

_MODULE_MAP = {
    "noneToZero": ("general_manager.utils.noneToZero", "noneToZero"),
    "args_to_kwargs": ("general_manager.utils.argsToKwargs", "args_to_kwargs"),
    "make_cache_key": ("general_manager.utils.makeCacheKey", "make_cache_key"),
    "parse_filters": ("general_manager.utils.filterParser", "parse_filters"),
    "create_filter_function": ("general_manager.utils.filterParser", "create_filter_function"),
    "snake_to_pascal": ("general_manager.utils.formatString", "snake_to_pascal"),
    "snake_to_camel": ("general_manager.utils.formatString", "snake_to_camel"),
    "pascal_to_snake": ("general_manager.utils.formatString", "pascal_to_snake"),
    "camel_to_snake": ("general_manager.utils.formatString", "camel_to_snake"),
}


def __getattr__(name: str) -> Any:
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
