"""Helpers for caching GeneralManager computations with dependency tracking."""

from functools import wraps
from typing import Any, Callable, Optional, Protocol, Set, TypeVar, cast

from django.core.cache import cache as django_cache

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import Dependency, record_dependencies
from general_manager.cache.model_dependency_collector import ModelDependencyCollector
from general_manager.logging import get_logger
from general_manager.utils.make_cache_key import make_cache_key


class CacheBackend(Protocol):
    def get(self, key: str, default: Optional[Any] = None) -> Any:
        """
        Retrieve a value from the cache, falling back to a default.

        Parameters:
            key (str): Cache key identifying the stored entry.
            default (Any | None): Value returned when the key is absent.

        Returns:
            Any: Cached value when available; otherwise, `default`.
        """
        ...

    def set(self, key: str, value: Any, timeout: Optional[int] = None) -> None:
        """
        Store a value in the cache with an optional expiration timeout.

        Parameters:
            key (str): Cache key identifying the stored entry.
            value (Any): Object written to the cache.
            timeout (int | None): Expiration in seconds; `None` stores the value indefinitely.

        Returns:
            None
        """
        ...


RecordFn = Callable[[str, Set[Dependency]], None]
FuncT = TypeVar("FuncT", bound=Callable[..., object])

_SENTINEL = object()
logger = get_logger("cache.decorator")


def cached(
    timeout: Optional[int] = None,
    cache_backend: CacheBackend = django_cache,
    record_fn: RecordFn = record_dependencies,
) -> Callable[[FuncT], FuncT]:
    """
    Cache a function call while registering its data dependencies.

    Parameters:
        timeout (int | None): Expiration in seconds for cached values; `None` stores results until invalidated.
        cache_backend (CacheBackend): Backend used to read and write cached results.
        record_fn (RecordFn): Callback invoked to persist dependency metadata when no timeout is defined.

    Returns:
        Callable: Decorator that wraps the target function with caching behaviour.
    """

    def decorator(func: FuncT) -> FuncT:
        @wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            key = make_cache_key(func, args, kwargs)
            deps_key = f"{key}:deps"

            cached_result = cache_backend.get(key, _SENTINEL)
            if cached_result is not _SENTINEL:
                # saved dependencies are added to the current tracker
                cached_deps = cache_backend.get(deps_key)
                if cached_deps:
                    for class_name, operation, identifier in cached_deps:
                        DependencyTracker.track(class_name, operation, identifier)
                logger.debug(
                    "cache hit",
                    context={
                        "function": func.__qualname__,
                        "key": key,
                        "dependency_count": len(cached_deps) if cached_deps else 0,
                    },
                )
                return cached_result

            with DependencyTracker() as dependencies:
                result = func(*args, **kwargs)
                ModelDependencyCollector.add_args(dependencies, args, kwargs)

                cache_backend.set(key, result, timeout)
                cache_backend.set(deps_key, dependencies, timeout)

                if dependencies and timeout is None:
                    record_fn(key, dependencies)

            logger.debug(
                "cache miss recorded",
                context={
                    "function": func.__qualname__,
                    "key": key,
                    "dependency_count": len(dependencies),
                    "timeout": timeout,
                },
            )
            return result

        # fix for python 3.14:
        wrapper.__annotations__ = func.__annotations__

        return cast(FuncT, wrapper)

    return decorator
