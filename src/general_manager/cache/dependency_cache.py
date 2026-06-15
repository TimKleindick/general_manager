"""Internal helpers for dependency-scoped cache entry reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, TypeGuard

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import Dependency

DEPENDENCY_CACHE_ENTRY_VERSION = 1
_MISSING = object()


@dataclass(frozen=True, slots=True)
class DependencyCacheEntry:
    """Persisted dependency-cache payload stored at the main cache key."""

    version: int
    value: Any
    dependencies: frozenset[Dependency]


@dataclass(frozen=True, slots=True)
class DependencyCacheHit:
    """In-memory representation of a dependency-cache hit."""

    value: Any
    dependencies: frozenset[Dependency]


class DependencyCacheBackend(Protocol):
    def get(self, key: str, default: Any = None) -> Any:
        """Return a cached value or *default* when absent."""
        ...

    def set(self, key: str, value: Any, timeout: int | None = None) -> None:
        """Store a cached value."""
        ...


class DependencyCacheGetManyBackend(DependencyCacheBackend, Protocol):
    def get_many(self, keys: Iterable[str]) -> Mapping[str, Any]:
        """Return cached values for all found keys."""
        ...


class DependencyCacheSetManyBackend(DependencyCacheBackend, Protocol):
    def set_many(
        self,
        data: Mapping[str, Any],
        timeout: int | None = None,
    ) -> Any:
        """Store cached values for many keys."""
        ...


def make_dependency_cache_entry(
    value: Any,
    dependencies: Iterable[Dependency],
) -> DependencyCacheEntry:
    """Build the current persisted dependency-cache payload."""
    return DependencyCacheEntry(
        version=DEPENDENCY_CACHE_ENTRY_VERSION,
        value=value,
        dependencies=frozenset(dependencies),
    )


def read_dependency_cache_hit(
    cache_backend: DependencyCacheBackend,
    cache_key: str,
    *,
    sentinel: Any = _MISSING,
) -> DependencyCacheHit | Any:
    """Read one dependency-cache entry, including legacy split entries."""
    payload = cache_backend.get(cache_key, sentinel)
    if payload is sentinel:
        return sentinel
    combined_hit = _combined_payload_to_hit(payload)
    if combined_hit is _MISSING:
        return sentinel
    if combined_hit is not None:
        return combined_hit
    dependency_payload = cache_backend.get(_legacy_deps_key(cache_key), ())
    return DependencyCacheHit(
        value=payload,
        dependencies=frozenset(dependency_payload or ()),
    )


def read_many_dependency_cache_hits(
    cache_backend: DependencyCacheBackend,
    cache_keys: Iterable[str],
) -> dict[str, DependencyCacheHit]:
    """Bulk-read dependency-cache hits for known keys."""
    keys = tuple(dict.fromkeys(cache_keys))
    if not keys:
        return {}
    if not _supports_get_many(cache_backend):
        return _read_many_without_get_many(cache_backend, keys)

    payloads = cache_backend.get_many(keys)
    hits: dict[str, DependencyCacheHit] = {}
    legacy_keys: list[str] = []
    for key in keys:
        if key not in payloads:
            continue
        combined_hit = _combined_payload_to_hit(payloads[key])
        if combined_hit is _MISSING:
            continue
        if isinstance(combined_hit, DependencyCacheHit):
            hits[key] = combined_hit
            continue
        legacy_keys.append(key)

    if legacy_keys:
        deps_keys = {_legacy_deps_key(key): key for key in legacy_keys}
        legacy_deps = cache_backend.get_many(deps_keys.keys())
        for deps_key, key in deps_keys.items():
            hits[key] = DependencyCacheHit(
                value=payloads[key],
                dependencies=frozenset(legacy_deps.get(deps_key, ()) or ()),
            )
    return hits


def replay_dependency_cache_hit(hit: DependencyCacheHit) -> None:
    """Replay cached dependencies into active dependency tracking scopes."""
    for class_name, operation, identifier in hit.dependencies:
        DependencyTracker.track(class_name, operation, identifier)


def _legacy_deps_key(cache_key: str) -> str:
    return f"{cache_key}:deps"


def _combined_payload_to_hit(payload: Any) -> DependencyCacheHit | None | object:
    if not isinstance(payload, DependencyCacheEntry):
        return None
    if payload.version != DEPENDENCY_CACHE_ENTRY_VERSION:
        return _MISSING
    return DependencyCacheHit(
        value=payload.value,
        dependencies=payload.dependencies,
    )


def _supports_get_many(
    cache_backend: DependencyCacheBackend,
) -> TypeGuard[DependencyCacheGetManyBackend]:
    return callable(getattr(cache_backend, "get_many", None))


def _read_many_without_get_many(
    cache_backend: DependencyCacheBackend,
    cache_keys: tuple[str, ...],
) -> dict[str, DependencyCacheHit]:
    hits: dict[str, DependencyCacheHit] = {}
    for key in cache_keys:
        hit = read_dependency_cache_hit(cache_backend, key, sentinel=_MISSING)
        if hit is not _MISSING:
            hits[key] = hit
    return hits
