"""Helpers for caching GeneralManager computations with dependency tracking."""

from functools import wraps
from typing import (
    Any,
    Callable,
    Iterable,
    Literal,
    Optional,
    Protocol,
    Set,
    TypeVar,
    cast,
)

from django.core.cache import cache as django_cache

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_cache import (
    DependencyCacheHit,
    read_dependency_cache_hit,
    replay_dependency_cache_hit,
)
from general_manager.cache.dependency_index import (
    Dependency,
    get_dependency_generation,
    record_dependencies,
)
from general_manager.cache.dependency_publish import (
    CachePublishAborted,
    acquire_compute_lease,
    publish_dependency_cache_entry,
    release_compute_lease,
    wait_for_cached_dependency_hit,
)
from general_manager.cache.run_context import (
    current_calculation_run_context,
    ensure_calculation_run_context,
)
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
CacheScope = Literal["dependency", "run", "timeout", "none"]

_SENTINEL = object()
logger = get_logger("cache.decorator")


class UnsupportedCacheScopeError(ValueError):
    """Raised when a cache decorator receives an unsupported scope."""

    def __init__(self, scope: object) -> None:
        super().__init__(f"Unsupported cache scope: {scope}")


class CacheTimeoutConfigurationError(ValueError):
    """Raised when timeout is used with an incompatible cache scope."""

    @classmethod
    def missing_timeout(cls) -> "CacheTimeoutConfigurationError":
        return cls('scope="timeout" requires timeout')

    @classmethod
    def unexpected_timeout(cls) -> "CacheTimeoutConfigurationError":
        return cls('timeout is only supported with scope="timeout"')


def cached(
    timeout: Optional[int] = None,
    cache_backend: CacheBackend = django_cache,
    record_fn: RecordFn = record_dependencies,
    *,
    scope: CacheScope = "run",
) -> Callable[[FuncT], FuncT]:
    """
    Decorator factory for caching a function call.

    By default, cached values are scoped to the active
    :class:`~general_manager.cache.run_context.CalculationRunContext` and are
    discarded when that run ends. Use ``scope="dependency"`` to persist values
    in ``cache_backend`` and use ``record_fn`` to persist dependency metadata
    for invalidation. Use ``scope="timeout"`` with ``timeout`` set for
    cache-backend storage with time-based expiry; dependency recording is
    ignored for timeout-scoped values.

    Parameters:
        timeout (int | None): Expiration in seconds for timeout-scoped cached values.
            Required when ``scope`` is ``"timeout"`` and invalid with any other
            ``CacheScope``.
        cache_backend (CacheBackend): Backend used to read and write cached results.
        record_fn (RecordFn): Callback invoked to persist dependency metadata when
            ``scope`` is ``"dependency"``. Defaults to
            :func:`~general_manager.cache.dependency_index.record_dependencies`.
        scope (CacheScope): Cache storage strategy. ``"run"`` memoizes for the active run,
            ``"dependency"`` stores in ``cache_backend`` with dependency tracking,
            ``"timeout"`` stores in ``cache_backend`` with time-based expiry, and
            ``"none"`` disables caching.

    Returns:
        Callable: Decorator that wraps the target function with caching behaviour.

    Raises:
        ValueError: Raised for invalid ``scope``/``timeout`` combinations, including
            unsupported ``CacheScope`` values, missing ``timeout`` for
            ``scope="timeout"``, and ``timeout`` supplied for non-timeout scopes.
        DependencyLockTimeoutError: Propagated from ``record_fn`` (i.e.
            :func:`~general_manager.cache.dependency_index.record_dependencies`) when the
            dependency-index lock cannot be acquired within the configured timeout.  The cached
            value has already been stored at that point; only the dependency metadata is lost.
    """
    if scope not in {"dependency", "run", "timeout", "none"}:
        raise UnsupportedCacheScopeError(scope)
    if scope == "timeout" and timeout is None:
        raise CacheTimeoutConfigurationError.missing_timeout()
    if timeout is not None and scope != "timeout":
        raise CacheTimeoutConfigurationError.unexpected_timeout()

    def decorator(func: FuncT) -> FuncT:
        @wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            if scope == "none":
                return func(*args, **kwargs)

            if scope == "run":
                key = make_cache_key(func, args, kwargs)
                with ensure_calculation_run_context() as context:
                    return context.get_or_set(key, lambda: func(*args, **kwargs))

            key = make_cache_key(func, args, kwargs)

            if scope == "timeout":
                cached_result = cache_backend.get(key, _SENTINEL)
                if cached_result is not _SENTINEL:
                    logger.debug(
                        "cache hit",
                        context={
                            "function": func.__qualname__,
                            "key": key,
                            "scope": scope,
                        },
                    )
                    return cached_result

                result = func(*args, **kwargs)
                cache_backend.set(key, result, timeout)
                logger.debug(
                    "cache miss stored",
                    context={
                        "function": func.__qualname__,
                        "key": key,
                        "timeout": timeout,
                        "scope": scope,
                    },
                )
                return result

            def return_cached_hit(hit: DependencyCacheHit, message: str) -> object:
                replay_dependency_cache_hit(hit)
                logger.debug(
                    message,
                    context={
                        "function": func.__qualname__,
                        "key": key,
                        "scope": scope,
                    },
                )
                return hit.value

            prefetch_context = current_calculation_run_context()
            if prefetch_context is not None:
                prefetched_hit = prefetch_context.get_dependency_cache_hit(
                    key, _SENTINEL
                )
                if isinstance(prefetched_hit, DependencyCacheHit):
                    return return_cached_hit(
                        prefetched_hit,
                        "cache hit from dependency prefetch",
                    )

            cached_hit = read_dependency_cache_hit(
                cache_backend,
                key,
                sentinel=_SENTINEL,
            )
            if cached_hit is not _SENTINEL:
                return return_cached_hit(cached_hit, "cache hit")

            lease = acquire_compute_lease(key)
            while lease is None:
                cached_hit = wait_for_cached_dependency_hit(
                    cache_backend,
                    key,
                    sentinel=_SENTINEL,
                )
                if cached_hit is not _SENTINEL:
                    return return_cached_hit(
                        cached_hit,
                        "cache hit after waiting for dependency publish",
                    )
                lease = acquire_compute_lease(key)

            try:
                cached_hit = read_dependency_cache_hit(
                    cache_backend,
                    key,
                    sentinel=_SENTINEL,
                )
                if cached_hit is not _SENTINEL:
                    return return_cached_hit(cached_hit, "cache hit")

                started_generation = get_dependency_generation()
                with DependencyTracker() as dependencies:
                    result = func(*args, **kwargs)
                    ModelDependencyCollector.add_args(dependencies, args, kwargs)

                def record_many(
                    entries: Iterable[tuple[str, Iterable[Dependency]]],
                ) -> None:
                    for entry_key, entry_dependencies in entries:
                        record_fn(entry_key, set(entry_dependencies))

                try:
                    publish_dependency_cache_entry(
                        cache_key=key,
                        result=result,
                        dependencies=dependencies,
                        cache_backend=cache_backend,
                        timeout=timeout,
                        started_generation=started_generation,
                        record_many_fn=(
                            None if record_fn is record_dependencies else record_many
                        ),
                    )
                except CachePublishAborted:
                    logger.debug(
                        "dependency cache publish aborted",
                        context={
                            "function": func.__qualname__,
                            "key": key,
                            "scope": scope,
                        },
                    )
            finally:
                release_compute_lease(lease)

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
