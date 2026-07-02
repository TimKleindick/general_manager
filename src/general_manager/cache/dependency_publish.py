"""Coordination helpers for dependency-scoped cache publishing."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeGuard

from django.core.cache import cache as coordination_cache

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_cache import (
    DependencyCacheBackend,
    DependencyCacheEntry,
    DependencyCacheHit,
    DependencyCacheSetManyBackend,
    dependency_cache_prefetch_segment_bundle_key,
    dependency_cache_prefetch_segment_index_key,
    dependency_cache_prefetch_segment_token,
    dependency_cache_prefetch_segment_value_bundle_key,
    make_dependency_cache_prefetch_bundle,
    make_dependency_cache_prefetch_value_bundle,
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
    """Raised when dependency-cache publishing is no longer safe.

    Single-entry publication aborts when a data-change barrier is active or when
    the dependency generation differs from the generation observed before the
    cached value was computed. Batch publication aborts on an active barrier or
    a generation change during the batch publish, but stale entries already in
    the batch are skipped rather than raising by themselves.
    """


@dataclass(frozen=True)
class CacheComputeLease:
    """Token proving ownership of a dependency-cache computation.

    Attributes:
        key: Coordination-cache key that stores the lease token.
        token: Random ownership token written to the coordination cache.
    """

    key: str
    token: str


@dataclass(frozen=True)
class PendingDependencyCachePublication:
    """A dependency-cache miss waiting for guarded publication.

    Buffered run-context misses use this value to publish dependency metadata and
    versioned cache payloads after the computation is known to be current. The
    lease is carried so the owner can release it after publish or discard; the
    publish helpers do not validate or release lease ownership.
    """

    cache_key: str
    result: object
    dependencies: frozenset[Dependency]
    cache_backend: DependencyCacheBackend
    timeout: int | None
    started_generation: int
    lease: CacheComputeLease
    dependencies_trusted: bool = False
    prefetch_manifest_key: str | None = None


RecordManyDependenciesFn = Callable[[Iterable[tuple[str, Iterable[Dependency]]]], None]

COMPUTE_LOCK_PREFIX = "dependency_cache_compute_lock"
COMPUTE_LOCK_TIMEOUT = LOCK_TIMEOUT
WAIT_INITIAL_DELAY = 0.01
WAIT_MAX_DELAY = 0.2
_WAIT_MISS = object()
_CALCULATION_IDENTITY_MANAGER_NAMES_CACHE: tuple[tuple[int, ...], frozenset[str]] = (
    (),
    frozenset(),
)


def _compute_lock_key(cache_key: str) -> str:
    return f"{COMPUTE_LOCK_PREFIX}:{cache_key}"


def acquire_compute_lease(
    cache_key: str,
    *,
    timeout: int = COMPUTE_LOCK_TIMEOUT,
) -> CacheComputeLease | None:
    """Acquire a per-cache-key compute lease if no worker currently owns it.

    Args:
        cache_key: Dependency-cache key whose computation should be guarded.
        timeout: Coordination-cache TTL for the lease token.

    Returns:
        A lease token when this worker acquired ownership, otherwise `None`.

    Raises:
        Exception: Errors from the coordination cache `add()` call propagate.
    """
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
    sentinel: object = _WAIT_MISS,
) -> DependencyCacheHit | object:
    """Poll for a dependency-cache hit until it appears or the wait expires.

    Args:
        cache_backend: Backend containing dependency-cache entries.
        cache_key: Key to poll.
        timeout_seconds: Maximum wall-clock seconds to wait.
        sentinel: Object returned when no hit appears before timeout.

    Returns:
        A `DependencyCacheHit` when another worker publishes a compatible value,
        otherwise `sentinel`. Falsey cached values are returned as hits through
        `DependencyCacheHit`.

    Raises:
        Exception: Backend read errors, legacy dependency conversion errors, and
            monotonic/sleep errors propagate.
    """
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


def _calculation_identity_manager_names() -> frozenset[str]:
    from general_manager.interface.interfaces.calculation import CalculationInterface
    from general_manager.manager.meta import GeneralManagerMeta

    global _CALCULATION_IDENTITY_MANAGER_NAMES_CACHE

    manager_classes = tuple(GeneralManagerMeta.all_classes)
    fingerprint = tuple(id(manager_class) for manager_class in manager_classes)
    cached_fingerprint, cached_names = _CALCULATION_IDENTITY_MANAGER_NAMES_CACHE
    if fingerprint == cached_fingerprint:
        return cached_names

    names = frozenset(
        manager_class.__name__
        for manager_class in manager_classes
        if isinstance(getattr(manager_class, "Interface", None), type)
        and issubclass(manager_class.Interface, CalculationInterface)
    )
    _CALCULATION_IDENTITY_MANAGER_NAMES_CACHE = (fingerprint, names)
    return names


def _metadata_dependency_set(
    dependencies: Iterable[Dependency],
    calculation_manager_names: frozenset[str] | None = None,
) -> set[Dependency]:
    dependency_set = set(dependencies)
    if not dependency_set:
        return dependency_set

    if calculation_manager_names is None:
        calculation_manager_names = _calculation_identity_manager_names()
    if not calculation_manager_names:
        return dependency_set

    return {
        dependency
        for dependency in dependency_set
        if not (
            dependency[1] == "identification"
            and dependency[0] in calculation_manager_names
        )
    }


def _segment_tokens(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, (tuple, list, frozenset, set)):
        return ()
    return tuple(token for token in payload if isinstance(token, str))


def _set_prefetch_segment_entries(
    cache_backend: DependencyCacheBackend,
    timeout: int | None,
    manifest_key: str,
    bundle_entries: dict[str, DependencyCacheEntry],
) -> None:
    if not bundle_entries:
        return
    ordered_cache_keys = tuple(bundle_entries)
    segment_token = dependency_cache_prefetch_segment_token(ordered_cache_keys)
    segment_index_key = dependency_cache_prefetch_segment_index_key(manifest_key)
    segment_tokens = _segment_tokens(cache_backend.get(segment_index_key, ()))
    if segment_token not in segment_tokens:
        cache_backend.set(
            segment_index_key,
            (*segment_tokens, segment_token),
            timeout,
        )
    cache_backend.set(
        dependency_cache_prefetch_segment_bundle_key(manifest_key, segment_token),
        make_dependency_cache_prefetch_bundle(bundle_entries),
        timeout,
    )
    cache_backend.set(
        dependency_cache_prefetch_segment_value_bundle_key(
            manifest_key,
            segment_token,
        ),
        make_dependency_cache_prefetch_value_bundle(bundle_entries),
        timeout,
    )


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
                trusted_dependencies=entry.dependencies_trusted,
            )
            for entry in group_entries
        }
        if _supports_set_many(cache_backend):
            failed_keys = cache_backend.set_many(payloads, timeout) or ()
            for key in failed_keys:
                if key in payloads:
                    cache_backend.set(key, payloads[key], timeout)
        else:
            for key, payload in payloads.items():
                cache_backend.set(key, payload, timeout)

        manifest_payloads: dict[str, list[str]] = defaultdict(list)
        for entry in group_entries:
            if entry.prefetch_manifest_key is not None:
                manifest_payloads[entry.prefetch_manifest_key].append(entry.cache_key)
        for manifest_key, cache_keys in manifest_payloads.items():
            ordered_cache_keys = tuple(dict.fromkeys(cache_keys))
            bundle_payloads = {
                cache_key: payloads[cache_key]
                for cache_key in ordered_cache_keys
                if cache_key in payloads
            }
            cache_backend.set(manifest_key, tuple(bundle_payloads), timeout)
            _set_prefetch_segment_entries(
                cache_backend,
                timeout,
                manifest_key,
                bundle_payloads,
            )


def _prefetch_bundle_dependency_entries_from_metadata(
    grouped_dependencies: dict[str, set[Dependency]],
    grouped_cache_keys: dict[str, list[str]],
) -> tuple[tuple[str, set[Dependency]], ...]:
    return tuple(
        bundle_entry
        for manifest_key, dependencies in grouped_dependencies.items()
        for segment_token in (
            dependency_cache_prefetch_segment_token(
                tuple(dict.fromkeys(grouped_cache_keys[manifest_key]))
            ),
        )
        for bundle_entry in (
            (
                dependency_cache_prefetch_segment_bundle_key(
                    manifest_key,
                    segment_token,
                ),
                dependencies,
            ),
            (
                dependency_cache_prefetch_segment_value_bundle_key(
                    manifest_key,
                    segment_token,
                ),
                dependencies,
            ),
        )
        if dependencies
    )


def publish_dependency_cache_entries(
    entries: Iterable[PendingDependencyCachePublication],
) -> None:
    """Publish dependency metadata and values for current entries in one batch.

    The active data-change barrier is checked before stale entries are filtered;
    if it is active, the whole batch aborts. Otherwise entries whose
    `started_generation` no longer matches the current dependency generation are
    skipped. If the generation changes or a barrier begins after dependency
    metadata is recorded but before values are stored, `CachePublishAborted` is
    raised and cache payloads are not written.
    Dependency metadata for non-empty dependency sets is recorded before any
    cache value becomes visible. Backends with `set_many()` are written in
    groups by backend instance and timeout; failed keys returned by `set_many()`
    are retried with individual `set()` calls. Entry leases are not inspected or
    released by this helper; the caller that owns the lease remains responsible
    for releasing it.

    Raises:
        CachePublishAborted: If publishing is unsafe because a data change is
            active.
        Exception: Lock, dependency-index, and cache backend errors propagate.
    """
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

        calculation_manager_names = _calculation_identity_manager_names()
        dependency_entries: list[tuple[str, Iterable[Dependency]]] = []
        prefetch_dependencies: dict[str, set[Dependency]] = defaultdict(set)
        prefetch_cache_keys: dict[str, list[str]] = defaultdict(list)
        for entry in publishable_entries:
            if entry.dependencies:
                metadata_dependencies = _metadata_dependency_set(
                    entry.dependencies,
                    calculation_manager_names,
                )
                if metadata_dependencies:
                    dependency_entries.append((entry.cache_key, metadata_dependencies))
                    if entry.prefetch_manifest_key is not None:
                        prefetch_dependencies[entry.prefetch_manifest_key].update(
                            metadata_dependencies
                        )
                        prefetch_cache_keys[entry.prefetch_manifest_key].append(
                            entry.cache_key
                        )
        dependency_entries.extend(
            _prefetch_bundle_dependency_entries_from_metadata(
                prefetch_dependencies,
                prefetch_cache_keys,
            )
        )
        record_many_cache_dependencies(dependency_entries)

        _ensure_publish_current(current_generation)
        _set_dependency_cache_entries(publishable_entries)
    finally:
        release_lock()


def publish_dependency_cache_entry(
    *,
    cache_key: str,
    result: object,
    dependencies: Iterable[Dependency],
    cache_backend: DependencyCacheBackend,
    timeout: int | None,
    started_generation: int,
    record_many_fn: RecordManyDependenciesFn | None = None,
    prefetch_manifest_key: str | None = None,
) -> None:
    """Publish dependency metadata and value only if the computation is current.

    When supplied, ``record_many_fn`` is called while the dependency-index lock
    acquired by ``acquire_lock_with_retry`` is still held. Custom callbacks must
    not acquire that lock again or call helpers that do so. The cache decorator
    passes ``None`` for the default ``record_dependencies`` implementation so
    this function can use the non-reentrant locked helper directly.

    Args:
        cache_key: Dependency-cache key to publish.
        result: Cached function result to persist.
        dependencies: Dependencies captured during computation.
        cache_backend: Backend that stores the versioned dependency-cache entry.
        timeout: Backend timeout for the cached value.
        started_generation: Dependency generation observed before computation.
        record_many_fn: Optional dependency-recording callback. When omitted,
            the built-in shard recorder is used.

    Raises:
        CachePublishAborted: If publishing is unsafe before metadata recording,
            after a custom/built-in dependency record, or before the value write.
        Exception: Lock, dependency-index, custom recorder, and cache backend
            errors propagate.
    """
    dependencies_trusted = DependencyTracker._dependencies_are_tracker_captured(
        dependencies
    )
    dependency_set = set(dependencies)

    acquire_lock_with_retry("publish_dependency_cache_entry")
    try:
        _ensure_publish_current(started_generation)

        if record_many_fn is not None:
            if dependency_set:
                record_many_fn([(cache_key, dependency_set)])
            _ensure_publish_current(started_generation)
        else:
            metadata_dependencies = _metadata_dependency_set(dependency_set)
            dependency_entries = (
                [(cache_key, metadata_dependencies)] if metadata_dependencies else []
            )
            if prefetch_manifest_key is not None and metadata_dependencies:
                segment_token = dependency_cache_prefetch_segment_token((cache_key,))
                dependency_entries.extend(
                    [
                        (
                            dependency_cache_prefetch_segment_bundle_key(
                                prefetch_manifest_key,
                                segment_token,
                            ),
                            metadata_dependencies,
                        ),
                        (
                            dependency_cache_prefetch_segment_value_bundle_key(
                                prefetch_manifest_key,
                                segment_token,
                            ),
                            metadata_dependencies,
                        ),
                    ]
                )
            record_many_cache_dependencies(dependency_entries)
            _ensure_publish_current(started_generation)

        payload = make_dependency_cache_entry(
            result,
            dependency_set,
            trusted_dependencies=dependencies_trusted,
        )
        prefetch_bundle_entries = {cache_key: payload}

        cache_backend.set(cache_key, payload, timeout)
        if prefetch_manifest_key is not None:
            if record_many_fn is None:
                cache_backend.set(prefetch_manifest_key, (cache_key,), timeout)
                _set_prefetch_segment_entries(
                    cache_backend,
                    timeout,
                    prefetch_manifest_key,
                    prefetch_bundle_entries,
                )
            else:
                cache_backend.set(prefetch_manifest_key, (cache_key,), timeout)
    finally:
        release_lock()
