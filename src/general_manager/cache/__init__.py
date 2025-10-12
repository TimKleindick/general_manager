"""Caching helpers for GeneralManager dependencies."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "cached",
    "CacheBackend",
    "DependencyTracker",
    "record_dependencies",
    "remove_cache_key_from_index",
    "invalidate_cache_key",
]

_MODULE_MAP = {
    "cached": ("general_manager.cache.cacheDecorator", "cached"),
    "CacheBackend": ("general_manager.cache.cacheDecorator", "CacheBackend"),
    "DependencyTracker": ("general_manager.cache.cacheTracker", "DependencyTracker"),
    "record_dependencies": ("general_manager.cache.dependencyIndex", "record_dependencies"),
    "remove_cache_key_from_index": ("general_manager.cache.dependencyIndex", "remove_cache_key_from_index"),
    "invalidate_cache_key": ("general_manager.cache.dependencyIndex", "invalidate_cache_key"),
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
