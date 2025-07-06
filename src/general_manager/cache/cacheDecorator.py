from typing import Any, Callable, Optional, Protocol, Set
from functools import wraps
from django.core.cache import cache as django_cache
from general_manager.cache.cacheTracker import DependencyTracker
from general_manager.cache.dependencyIndex import record_dependencies, Dependency
from general_manager.cache.modelDependencyCollector import ModelDependencyCollector
from general_manager.utils.makeCacheKey import make_cache_key


class CacheBackend(Protocol):
    def get(self, key: str, default: Optional[Any] = None) -> Any:
        """
        Retrieves a value from the cache by key, returning a default if the key is not found.

        Args:
            key: The cache key to look up.
            default: Value to return if the key is not present in the cache.

        Returns:
            The cached value if found; otherwise, the provided default.
        """
        ...

    def set(self, key: str, value: Any, timeout: Optional[int] = None) -> None:
        """
        Stores a value in the cache under the specified key with an optional expiration timeout.

        Args:
            key: The cache key to associate with the value.
            value: The value to store in the cache.
            timeout: Optional expiration time in seconds. If None, the value is cached indefinitely.
        """
        ...


RecordFn = Callable[[str, Set[Dependency]], None]

_SENTINEL = object()


def cached(
    timeout: Optional[int] = None,
    cache_backend: CacheBackend = django_cache,
    record_fn: RecordFn = record_dependencies,
) -> Callable:
    """
    Decorator that caches function results and tracks their dependencies.

    When applied to a function, this decorator caches the function's output using a generated cache key based on its arguments. It also tracks dependencies accessed during the function's execution and stores them alongside the cached result. On cache hits, previously stored dependencies are re-tracked to maintain dependency tracking continuity. If dependencies exist and no timeout is set, an external recording function is invoked to persist the dependency information.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = make_cache_key(func, args, kwargs)
            deps_key = f"{key}:deps"

            cached_result = cache_backend.get(key, _SENTINEL)
            if cached_result is not _SENTINEL:
                # saved dependencies are added to the current tracker
                cached_deps = cache_backend.get(deps_key)
                if cached_deps:
                    for class_name, operation, identifier in cached_deps:
                        DependencyTracker.track(class_name, operation, identifier)
                return cached_result

            with DependencyTracker() as dependencies:
                result = func(*args, **kwargs)
                ModelDependencyCollector.addArgs(dependencies, args, kwargs)

                cache_backend.set(key, result, timeout)
                cache_backend.set(deps_key, dependencies, timeout)

                if dependencies and timeout is None:
                    record_fn(key, dependencies)

            return result

        return wrapper

    return decorator
