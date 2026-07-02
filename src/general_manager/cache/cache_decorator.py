"""Helpers for caching GeneralManager computations with dependency tracking."""

from collections.abc import Callable, Iterable
from functools import wraps
from hashlib import sha256
from typing import (
    Literal,
    Protocol,
    TypeVar,
    cast,
    overload,
)

from django.core.cache import cache as django_cache

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_cache import (
    DependencyCacheHit,
    dependency_cache_prefetch_bundle_key,
    dependency_cache_prefetch_segment_bundle_key,
    dependency_cache_prefetch_segment_index_key,
    dependency_cache_prefetch_segment_value_bundle_key,
    dependency_cache_prefetch_value_bundle_key,
    read_many_dependency_cache_hits,
    read_many_dependency_cache_prefetch_bundle_hits,
    read_many_dependency_cache_prefetch_bundle_values,
    read_dependency_cache_prefetch_bundle_hits,
    read_dependency_cache_prefetch_bundle_values,
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
    PendingDependencyCachePublication,
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
    """Minimal cache backend protocol used by `cached`.

    Implementations must behave like Django cache backends for single-key
    `get()` and `set()` operations. Stored values are intentionally typed as
    `object` because decorated functions may return any Python value accepted by
    the configured backend serializer.
    """

    def get(self, key: str, default: object = None) -> object:
        """Return the cached value for `key`, or `default` when absent."""
        ...

    def set(self, key: str, value: object, timeout: int | None = None) -> None:
        """Store `value` under `key` with an optional backend timeout."""
        ...


RecordFn = Callable[[str, set[Dependency]], None]
FuncT = TypeVar("FuncT", bound=Callable[..., object])
CacheScope = Literal["dependency", "run", "timeout", "none"]

_SENTINEL = object()
_RUN_CACHE_MISS = object()
_DEPENDENCY_CACHE_PREFETCH_MANIFEST_PREFIX = "dependency_cache_prefetch_manifest"
_DEPENDENCY_CACHE_PREFETCH_ATTEMPT_PREFIX = "dependency_cache_prefetch_attempt"
_DEPENDENCY_CACHE_PREFETCH_VALUE_PREFIX = "dependency_cache_prefetch_value"
logger = get_logger("cache.decorator")


class UnsupportedCacheScopeError(ValueError):
    """Raised when a cache decorator receives an unsupported scope."""

    def __init__(self, scope: object) -> None:
        super().__init__(f"Unsupported cache scope: {scope}")


class CacheTimeoutConfigurationError(ValueError):
    """Raised when timeout is used with an incompatible cache setting."""

    @classmethod
    def missing_timeout(cls) -> "CacheTimeoutConfigurationError":
        return cls('cache="timeout" requires timeout')

    @classmethod
    def unexpected_timeout(cls) -> "CacheTimeoutConfigurationError":
        return cls('timeout is only supported with cache="timeout"')


@overload
def cached(func: FuncT) -> FuncT: ...
@overload
def cached(
    func: None = None,
    timeout: int | None = None,
    cache_backend: CacheBackend = django_cache,
    record_fn: RecordFn = record_dependencies,
    *,
    cache: CacheScope = "run",
) -> Callable[[FuncT], FuncT]: ...


def cached(
    func: FuncT | None = None,
    timeout: int | None = None,
    cache_backend: CacheBackend = django_cache,
    record_fn: RecordFn = record_dependencies,
    *,
    cache: CacheScope = "run",
) -> FuncT | Callable[[FuncT], FuncT]:
    """
    Decorate a callable with one of GeneralManager's cache strategies.

    By default, cached values are scoped to the active
    :class:`~general_manager.cache.run_context.CalculationRunContext` and are
    discarded when that run ends. Use ``cache="dependency"`` to persist values
    in ``cache_backend`` and use ``record_fn`` to persist dependency metadata
    for invalidation. Use ``cache="timeout"`` with ``timeout`` set for
    cache-backend storage with time-based expiry; dependency recording is
    ignored for timeout-cached values.

    The decorator supports both ``@cached`` and ``@cached(...)`` forms and
    preserves the wrapped function's type signature for static type checkers.
    Cache keys are built from the wrapped callable plus positional and keyword
    arguments through :func:`general_manager.utils.make_cache_key.make_cache_key`.

    Parameters:
        func: Function being decorated when used as ``@cached``. Leave unset
            when using ``@cached(...)``.
        timeout: Expiration in seconds for timeout-cached values. Required when
            ``cache`` is ``"timeout"`` and invalid with any other cache mode.
            The decorator validates only presence/absence; accepted value ranges
            are delegated to the configured backend.
        cache_backend: Backend used to read and write dependency or timeout
            cached results. ``cache="run"`` and ``cache="none"`` do not use it.
        record_fn: Callback invoked with ``(cache_key, dependencies)`` when
            ``cache`` is ``"dependency"`` and the default dependency publisher
            is not batching the write in a run context.
        cache: Cache storage strategy. ``"run"`` memoizes for the active run,
            ``"dependency"`` stores in ``cache_backend`` with dependency
            tracking, ``"timeout"`` stores in ``cache_backend`` with
            time-based expiry, and ``"none"`` disables caching.

    Returns:
        The decorated callable when ``func`` is supplied, otherwise a decorator
        that wraps the target function with the selected caching behaviour.

    Raises:
        UnsupportedCacheScopeError: If ``cache`` is not one of
            ``"dependency"``, ``"run"``, ``"timeout"``, or ``"none"`` at
            runtime.
        CacheTimeoutConfigurationError: If ``cache="timeout"`` has no timeout
            or a timeout is supplied for another cache mode.
        Cache backend errors: Propagated from ``cache_backend.get`` or
            ``cache_backend.set`` for dependency and timeout scopes.
        Exception: Exceptions raised by the wrapped callable, dependency
            tracking, dependency publication, compute lease acquisition/waiting,
            or custom ``record_fn`` callbacks propagate unless the dependency
            publisher reports ``CachePublishAborted``. In that case, the fresh
            function result is returned without publishing a dependency cache
            entry.
        DependencyLockTimeoutError: Propagated from ``record_fn`` (i.e.
            :func:`~general_manager.cache.dependency_index.record_dependencies`) when the
            dependency-index lock cannot be acquired within the configured timeout. The cached
            value has already been stored at that point; only the dependency metadata is lost.
    """
    if cache not in {"dependency", "run", "timeout", "none"}:
        raise UnsupportedCacheScopeError(cache)
    if cache == "timeout" and timeout is None:
        raise CacheTimeoutConfigurationError.missing_timeout()
    if timeout is not None and cache != "timeout":
        raise CacheTimeoutConfigurationError.unexpected_timeout()

    def dependency_cache_prefetch_manifest_key(
        decorated_func: Callable[..., object],
    ) -> str:
        raw = f"{decorated_func.__module__}:{decorated_func.__qualname__}".encode()
        digest = sha256(raw, usedforsecurity=False).hexdigest()
        return f"{_DEPENDENCY_CACHE_PREFETCH_MANIFEST_PREFIX}:{digest}"

    def prefetch_dependency_cache_manifest(
        context: object,
        manifest_key: str,
    ) -> None:
        if not callable(getattr(cache_backend, "get_many", None)):
            return
        if not all(
            hasattr(context, attr)
            for attr in (
                "get",
                "set",
                "set_dependency_cache_hits",
            )
        ):
            return
        attempt_key = (
            _DEPENDENCY_CACHE_PREFETCH_ATTEMPT_PREFIX,
            id(cache_backend),
            manifest_key,
        )
        if context.get(attempt_key, False):  # type: ignore[attr-defined]
            return
        context.set(attempt_key, True)  # type: ignore[attr-defined]

        segment_tokens = cache_backend.get(
            dependency_cache_prefetch_segment_index_key(manifest_key),
            (),
        )
        if isinstance(segment_tokens, (tuple, list, frozenset, set)):
            valid_segment_tokens = tuple(
                token for token in segment_tokens if isinstance(token, str)
            )
        else:
            valid_segment_tokens = ()

        if not DependencyTracker.is_active():
            if valid_segment_tokens:
                segment_value_keys = tuple(
                    dependency_cache_prefetch_segment_value_bundle_key(
                        manifest_key,
                        segment_token,
                    )
                    for segment_token in valid_segment_tokens
                )
                segment_values = read_many_dependency_cache_prefetch_bundle_values(
                    cache_backend,
                    segment_value_keys,
                )
                if segment_values:
                    for cache_key, value in segment_values.items():
                        context.set(  # type: ignore[attr-defined]
                            (
                                _DEPENDENCY_CACHE_PREFETCH_VALUE_PREFIX,
                                id(cache_backend),
                                cache_key,
                            ),
                            value,
                        )
                    return

            bundle_values = read_dependency_cache_prefetch_bundle_values(
                cache_backend,
                dependency_cache_prefetch_value_bundle_key(manifest_key),
            )
            if bundle_values:
                for cache_key, value in bundle_values.items():
                    context.set(  # type: ignore[attr-defined]
                        (
                            _DEPENDENCY_CACHE_PREFETCH_VALUE_PREFIX,
                            id(cache_backend),
                            cache_key,
                        ),
                        value,
                    )
                return

        if valid_segment_tokens:
            segment_bundle_keys = tuple(
                dependency_cache_prefetch_segment_bundle_key(
                    manifest_key,
                    segment_token,
                )
                for segment_token in valid_segment_tokens
            )
            segment_hits = read_many_dependency_cache_prefetch_bundle_hits(
                cache_backend,
                segment_bundle_keys,
            )
            if segment_hits:
                context.set_dependency_cache_hits(segment_hits)  # type: ignore[attr-defined]
                return

        bundle_hits = read_dependency_cache_prefetch_bundle_hits(
            cache_backend,
            dependency_cache_prefetch_bundle_key(manifest_key),
        )
        if bundle_hits:
            context.set_dependency_cache_hits(bundle_hits)  # type: ignore[attr-defined]
            return

        manifest = cache_backend.get(manifest_key, ())
        if not isinstance(manifest, (tuple, list, frozenset, set)):
            return
        cache_keys = tuple(key for key in manifest if isinstance(key, str))
        if not cache_keys:
            return
        hits = read_many_dependency_cache_hits(cache_backend, cache_keys)
        if hits:
            context.set_dependency_cache_hits(hits)  # type: ignore[attr-defined]

    def decorator(decorated_func: FuncT) -> FuncT:
        prefetch_manifest_key = (
            dependency_cache_prefetch_manifest_key(decorated_func)
            if cache == "dependency"
            else None
        )

        @wraps(decorated_func)
        def wrapper(*args: object, **kwargs: object) -> object:
            if cache == "none":
                return decorated_func(*args, **kwargs)

            if cache == "run":
                key = make_cache_key(decorated_func, args, kwargs)
                active_context = current_calculation_run_context()
                if active_context is not None:
                    cached_run_value = active_context.get(key, _RUN_CACHE_MISS)
                    if cached_run_value is not _RUN_CACHE_MISS:
                        return cached_run_value
                    result = decorated_func(*args, **kwargs)
                    active_context.set(key, result)
                    return result
                with ensure_calculation_run_context() as context:
                    cached_run_value = context.get(key, _RUN_CACHE_MISS)
                    if cached_run_value is not _RUN_CACHE_MISS:
                        return cached_run_value
                    result = decorated_func(*args, **kwargs)
                    context.set(key, result)
                    return result

            key = make_cache_key(decorated_func, args, kwargs)

            if cache == "timeout":
                cached_result = cache_backend.get(key, _SENTINEL)
                if cached_result is not _SENTINEL:
                    logger.debug(
                        "cache hit",
                        context={
                            "function": decorated_func.__qualname__,
                            "key": key,
                            "cache": cache,
                        },
                    )
                    return cached_result

                result = decorated_func(*args, **kwargs)
                cache_backend.set(key, result, timeout)
                logger.debug(
                    "cache miss stored",
                    context={
                        "function": decorated_func.__qualname__,
                        "key": key,
                        "timeout": timeout,
                        "cache": cache,
                    },
                )
                return result

            def return_cached_hit(hit: DependencyCacheHit, message: str) -> object:
                replay_dependency_cache_hit(hit)
                logger.debug(
                    message,
                    context={
                        "function": decorated_func.__qualname__,
                        "key": key,
                        "cache": cache,
                    },
                )
                return hit.value

            def prefetched_value_from_context(context: object) -> object:
                if DependencyTracker.is_active():
                    return _SENTINEL
                return context.get(  # type: ignore[attr-defined]
                    (
                        _DEPENDENCY_CACHE_PREFETCH_VALUE_PREFIX,
                        id(cache_backend),
                        key,
                    ),
                    _SENTINEL,
                )

            prefetch_context = current_calculation_run_context()
            if prefetch_context is not None:
                prefetched_value = prefetched_value_from_context(prefetch_context)
                if prefetched_value is not _SENTINEL:
                    return prefetched_value
                prefetched_hit = prefetch_context.get_dependency_cache_hit(
                    key, _SENTINEL
                )
                if isinstance(prefetched_hit, DependencyCacheHit):
                    return return_cached_hit(
                        prefetched_hit,
                        "cache hit from dependency prefetch",
                    )
                if prefetch_manifest_key is not None:
                    prefetch_dependency_cache_manifest(
                        prefetch_context,
                        prefetch_manifest_key,
                    )
                    prefetched_value = prefetched_value_from_context(prefetch_context)
                    if prefetched_value is not _SENTINEL:
                        return prefetched_value
                    prefetched_hit = prefetch_context.get_dependency_cache_hit(
                        key, _SENTINEL
                    )
                    if isinstance(prefetched_hit, DependencyCacheHit):
                        return return_cached_hit(
                            prefetched_hit,
                            "cache hit from dependency prefetch manifest",
                        )

            cached_hit = read_dependency_cache_hit(
                cache_backend,
                key,
                sentinel=_SENTINEL,
            )
            if isinstance(cached_hit, DependencyCacheHit):
                return return_cached_hit(cached_hit, "cache hit")

            lease = acquire_compute_lease(key)
            while lease is None:
                cached_hit = wait_for_cached_dependency_hit(
                    cache_backend,
                    key,
                    sentinel=_SENTINEL,
                )
                if isinstance(cached_hit, DependencyCacheHit):
                    return return_cached_hit(
                        cached_hit,
                        "cache hit after waiting for dependency publish",
                    )
                lease = acquire_compute_lease(key)

            lease_transferred_to_context = False
            try:
                cached_hit = read_dependency_cache_hit(
                    cache_backend,
                    key,
                    sentinel=_SENTINEL,
                )
                if isinstance(cached_hit, DependencyCacheHit):
                    return return_cached_hit(cached_hit, "cache hit")

                started_generation = get_dependency_generation()
                with DependencyTracker() as dependencies:
                    result = decorated_func(*args, **kwargs)
                    ModelDependencyCollector.add_args(dependencies, args, kwargs)

                def record_many(
                    entries: Iterable[tuple[str, Iterable[Dependency]]],
                ) -> None:
                    for entry_key, entry_dependencies in entries:
                        record_fn(entry_key, set(entry_dependencies))

                publish_context = current_calculation_run_context()
                if publish_context is not None and record_fn is record_dependencies:
                    publish_context.buffer_dependency_cache_publication(
                        PendingDependencyCachePublication(
                            cache_key=key,
                            result=result,
                            dependencies=frozenset(dependencies),
                            cache_backend=cache_backend,
                            timeout=timeout,
                            started_generation=started_generation,
                            lease=lease,
                            dependencies_trusted=DependencyTracker._dependencies_are_tracker_captured(
                                dependencies
                            ),
                            prefetch_manifest_key=prefetch_manifest_key,
                        )
                    )
                    lease_transferred_to_context = True
                else:
                    try:
                        publish_dependency_cache_entry(
                            cache_key=key,
                            result=result,
                            dependencies=dependencies,
                            cache_backend=cache_backend,
                            timeout=timeout,
                            started_generation=started_generation,
                            record_many_fn=(
                                None
                                if record_fn is record_dependencies
                                else record_many
                            ),
                            prefetch_manifest_key=prefetch_manifest_key,
                        )
                    except CachePublishAborted:
                        logger.debug(
                            "dependency cache publish aborted",
                            context={
                                "function": decorated_func.__qualname__,
                                "key": key,
                                "cache": cache,
                            },
                        )
            finally:
                if not lease_transferred_to_context:
                    release_compute_lease(lease)

            logger.debug(
                "cache miss recorded",
                context={
                    "function": decorated_func.__qualname__,
                    "key": key,
                    "dependency_count": len(dependencies),
                    "timeout": timeout,
                },
            )
            return result

        # fix for python 3.14:
        wrapper.__annotations__ = decorated_func.__annotations__

        return cast(FuncT, wrapper)

    if func is None:
        return decorator
    return decorator(func)
