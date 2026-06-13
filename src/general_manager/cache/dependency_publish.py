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
    get_dependency_generation,
    is_dependency_data_change_active,
    record_many_dependencies,
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
    """Release a compute lease only when its token still owns the lock key."""
    if coordination_cache.get(lease.key) == lease.token:
        coordination_cache.delete(lease.key)


def wait_for_cached_dependency_value(
    cache_backend: DependencyCacheBackend,
    cache_key: str,
    timeout_seconds: float = LOCK_TIMEOUT,
    sentinel: Any = None,
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


def publish_dependency_cache_entry(
    *,
    cache_key: str,
    deps_key: str,
    result: Any,
    dependencies: Iterable[Dependency],
    cache_backend: DependencyCacheBackend,
    timeout: int | None,
    started_generation: int,
    record_many_fn: RecordManyDependenciesFn = record_many_dependencies,
) -> None:
    """Publish dependency metadata and value only if the computation is current."""
    if is_dependency_data_change_active():
        raise CachePublishAborted()
    if get_dependency_generation() != started_generation:
        raise CachePublishAborted()

    dependency_set = set(dependencies)
    if dependency_set:
        record_many_fn([(cache_key, dependency_set)])

    cache_backend.set(deps_key, dependency_set, timeout)
    cache_backend.set(cache_key, result, timeout)
