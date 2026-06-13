"""Coordination helpers for dependency-scoped cache publishing."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

from django.core.cache import cache as coordination_cache

from general_manager.cache.dependency_index import (
    LOCK_TIMEOUT,
    Dependency,
    _record_dependencies_locked,
    acquire_lock_with_retry,
    get_dependency_generation,
    get_full_index,
    is_dependency_data_change_active,
    release_lock,
    set_full_index,
)


class CachePublishAborted(RuntimeError):
    """Raised when dependency-cache publishing is no longer safe."""


@dataclass(frozen=True)
class CacheComputeLease:
    """Token proving ownership of a dependency-cache computation."""

    key: str
    token: str


class DependencyCacheBackend(Protocol):
    def get(self, key: str, default: Any = None) -> Any:
        raise NotImplementedError

    def set(self, key: str, value: Any, timeout: int | None = None) -> None:
        raise NotImplementedError


RecordManyDependenciesFn = Callable[[Iterable[tuple[str, Iterable[Dependency]]]], None]

COMPUTE_LOCK_PREFIX = "dependency_cache_compute_lock"
COMPUTE_LOCK_TIMEOUT = LOCK_TIMEOUT
WAIT_INITIAL_DELAY = 0.01
WAIT_MAX_DELAY = 0.2
_WAIT_MISS = object()


def _compute_lock_key(cache_key: str) -> str:
    return f"{COMPUTE_LOCK_PREFIX}:{cache_key}"


def acquire_compute_lease(
    cache_key: str,
    *,
    timeout: int = COMPUTE_LOCK_TIMEOUT,
) -> CacheComputeLease | None:
    """Acquire a per-cache-key compute lease if no worker currently owns it."""
    lock_key = _compute_lock_key(cache_key)
    token = uuid.uuid4().hex
    if not coordination_cache.add(lock_key, token, timeout):
        return None
    return CacheComputeLease(key=lock_key, token=token)


def release_compute_lease(lease: CacheComputeLease) -> None:
    """Release a compute lease without risking deletion of a newer owner.

    Django's cache API does not provide an atomic compare-and-delete operation.
    Leaving the lease to expire avoids a check-then-delete race where an expired
    lease could delete another worker's freshly acquired token.
    """
    return None


def wait_for_cached_dependency_value(
    cache_backend: DependencyCacheBackend,
    cache_key: str,
    timeout_seconds: float = LOCK_TIMEOUT,
    sentinel: Any = _WAIT_MISS,
) -> Any:
    """Poll for a cached dependency value until it appears or the wait expires."""
    deadline = time.monotonic() + timeout_seconds
    delay = WAIT_INITIAL_DELAY

    while True:
        cached_value = cache_backend.get(cache_key, sentinel)
        if cached_value is not sentinel:
            return cached_value

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return sentinel

        time.sleep(min(delay, remaining))
        delay = min(delay * 2, WAIT_MAX_DELAY)


def _ensure_publish_current(started_generation: int) -> None:
    if is_dependency_data_change_active():
        raise CachePublishAborted()
    if get_dependency_generation() != started_generation:
        raise CachePublishAborted()


def publish_dependency_cache_entry(
    *,
    cache_key: str,
    deps_key: str,
    result: Any,
    dependencies: Iterable[Dependency],
    cache_backend: DependencyCacheBackend,
    timeout: int | None,
    started_generation: int,
    record_many_fn: RecordManyDependenciesFn | None = None,
) -> None:
    """Publish dependency metadata and value only if the computation is current.

    When supplied, ``record_many_fn`` is called while the dependency-index lock
    acquired by ``acquire_lock_with_retry`` is still held. Custom callbacks must
    not acquire that lock again or call helpers that do so. The cache decorator
    passes ``None`` for the default ``record_dependencies`` implementation so
    this function can use the non-reentrant locked helper directly.
    """
    dependency_set = set(dependencies)

    acquire_lock_with_retry("publish_dependency_cache_entry")
    try:
        _ensure_publish_current(started_generation)

        if record_many_fn is not None:
            if dependency_set:
                record_many_fn([(cache_key, dependency_set)])
            _ensure_publish_current(started_generation)
        else:
            idx = get_full_index()
            if dependency_set:
                _record_dependencies_locked(idx, cache_key, dependency_set)
            _ensure_publish_current(started_generation)
            cache_backend.set(deps_key, dependency_set, timeout)
            set_full_index(idx)
            cache_backend.set(cache_key, result, timeout)
            return

        cache_backend.set(deps_key, dependency_set, timeout)
        cache_backend.set(cache_key, result, timeout)
    finally:
        release_lock()
