from __future__ import annotations

"""Type-only imports for public API re-exports."""

__all__ = [
    "CacheBackend",
    "DependencyTracker",
    "cached",
    "invalidate_cache_key",
    "record_dependencies",
    "remove_cache_key_from_index",
]

from general_manager.cache.cache_decorator import CacheBackend
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.cache_decorator import cached
from general_manager.cache.dependency_index import invalidate_cache_key
from general_manager.cache.dependency_index import record_dependencies
from general_manager.cache.dependency_index import remove_cache_key_from_index
