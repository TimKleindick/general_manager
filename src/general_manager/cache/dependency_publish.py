"""Coordination helpers for dependency-scoped cache publishing."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterable, TypeGuard

from django.core.cache import cache as coordination_cache

from general_manager.cache.dependency_cache import (
    DependencyCacheBackend,
    DependencyCacheSetManyBackend,
    make_dependency_cache_entry,
    read_dependency_cache_hit,
)
from general_manager.cache.dependency_index import (
    LOCK_TIMEOUT,
    Dependency,
    acquire_lock_with_retry,
    get_dependency_generation,
    is_dependency_data_change_active,
    release_lock,
)
from general_manager.cache.dependency_shards import (
    record_many_cache_dependencies,
)


class CachePublishAborted(RuntimeError):
    """Raised when dependency-cache publishing is no longer safe."""


@dataclass(frozen=True)
class CacheComputeLease:
    """Token proving ownership of a dependency-cache computation."""

    key: str
    token: str


@dataclass(frozen=True)
class PendingDependencyCachePublication:
    """A dependency-cache miss waiting for guarded publication."""

    cache_key: str
    result: Any
    dependencies: frozenset[Dependency]
    cache_backend: DependencyCacheBackend
    timeout: int | None
    started_generation: int
    lease: CacheComputeLease


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
    Checking the owner token first keeps normal recomputes fast while avoiding
    deletion when the lease has already been replaced by another worker.
    """
    if coordination_cache.get(lease.key) != lease.token:
        return
    coordination_cache.delete(lease.key)


def wait_for_cached_dependency_hit(
    cache_backend: DependencyCacheBackend,
    cache_key: str,
    timeout_seconds: float = LOCK_TIMEOUT,
    sentinel: Any = _WAIT_MISS,
) -> Any:
    """Poll for a dependency-cache hit until it appears or the wait expires."""
    deadline = time.monotonic() + timeout_seconds
    delay = WAIT_INITIAL_DELAY

    while True:
        cached_hit = read_dependency_cache_hit(
            cache_backend,
            cache_key,
            sentinel=sentinel,
        )
        if cached_hit is not sentinel:
            return cached_hit

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


def _supports_set_many(
    cache_backend: DependencyCacheBackend,
) -> TypeGuard[DependencyCacheSetManyBackend]:
    return callable(getattr(cache_backend, "set_many", None))


def _set_dependency_cache_entries(
    entries: Iterable[PendingDependencyCachePublication],
) -> None:
    grouped: dict[
        tuple[int, int | None],
        list[PendingDependencyCachePublication],
    ] = defaultdict(list)
    for entry in entries:
        grouped[(id(entry.cache_backend), entry.timeout)].append(entry)

    for group_entries in grouped.values():
        cache_backend = group_entries[0].cache_backend
        timeout = group_entries[0].timeout
        payloads = {
            entry.cache_key: make_dependency_cache_entry(
                entry.result,
                entry.dependencies,
            )
            for entry in group_entries
        }
        if _supports_set_many(cache_backend):
            failed_keys = cache_backend.set_many(payloads, timeout) or ()
            for key in failed_keys:
                if key in payloads:
                    cache_backend.set(key, payloads[key], timeout)
            continue
        for key, payload in payloads.items():
            cache_backend.set(key, payload, timeout)


def publish_dependency_cache_entries(
    entries: Iterable[PendingDependencyCachePublication],
) -> None:
    """Publish dependency metadata and values for current entries in one batch."""
    pending_entries = tuple(entries)
    if not pending_entries:
        return

    acquire_lock_with_retry("publish_dependency_cache_entries")
    try:
        if is_dependency_data_change_active():
            raise CachePublishAborted()

        current_generation = get_dependency_generation()
        publishable_entries = tuple(
            entry
            for entry in pending_entries
            if entry.started_generation == current_generation
        )
        if not publishable_entries:
            return

        record_many_cache_dependencies(
            (entry.cache_key, entry.dependencies)
            for entry in publishable_entries
            if entry.dependencies
        )

        _ensure_publish_current(current_generation)
        _set_dependency_cache_entries(publishable_entries)
    finally:
        release_lock()


def publish_dependency_cache_entry(
    *,
    cache_key: str,
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
            if dependency_set:
                record_many_cache_dependencies([(cache_key, dependency_set)])
            _ensure_publish_current(started_generation)

        cache_backend.set(
            cache_key,
            make_dependency_cache_entry(result, dependency_set),
            timeout,
        )
    finally:
        release_lock()
