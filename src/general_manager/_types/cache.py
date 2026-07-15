"""Type-only imports for public API re-exports."""

from __future__ import annotations

__all__ = [
    "CacheBackend",
    "CalculationRunContext",
    "Dependency",
    "DependencyTracker",
    "cached",
    "current_calculation_run_context",
    "ensure_calculation_run_context",
    "invalidate_cache_key",
    "parse_dependency_identifier",
    "record_dependencies",
    "remove_cache_key_from_index",
    "serialize_dependency_identifier",
]

from general_manager.cache.cache_decorator import CacheBackend
from general_manager.cache.run_context import CalculationRunContext
from general_manager.cache.dependency_index import Dependency
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.cache_decorator import cached
from general_manager.cache.run_context import current_calculation_run_context
from general_manager.cache.run_context import ensure_calculation_run_context
from general_manager.cache.dependency_index import invalidate_cache_key
from general_manager.cache.dependency_index import parse_dependency_identifier
from general_manager.cache.dependency_index import record_dependencies
from general_manager.cache.dependency_index import remove_cache_key_from_index
from general_manager.cache.dependency_index import serialize_dependency_identifier
