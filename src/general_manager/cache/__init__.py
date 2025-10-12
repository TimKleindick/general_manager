"""Caching helpers for GeneralManager dependencies."""

from __future__ import annotations

from typing import Any

from general_manager.utils.public_api import build_module_dir, resolve_export

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
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    return build_module_dir(module_all=__all__, module_globals=globals())
