"""Convenience re-exports for common utility helpers."""

from __future__ import annotations

from importlib import import_module
from typing import Any

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
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = _MODULE_MAP[name]
    module = import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
