"""Internal helpers for dependency-scoped cache entry reads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, TypeGuard, cast

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import Dependency

DEPENDENCY_CACHE_ENTRY_VERSION = 2
TRUSTED_DEPENDENCY_CACHE_ENTRY_VERSION = 3
DEPENDENCY_CACHE_PREFETCH_BUNDLE_VERSION = 1
DEPENDENCY_CACHE_PREFETCH_VALUE_BUNDLE_VERSION = 1
_VALID_DEPENDENCY_ACTIONS = frozenset(
    {"filter", "exclude", "identification", "request_query", "all"}
)


class _MissingSentinel:
    """Private marker for absent dependency-cache payloads."""


_MISSING = _MissingSentinel()


@dataclass(frozen=True, slots=True)
class DependencyCacheEntry:
    """Persisted dependency-cache payload stored at the main cache key.

    Attributes:
        version: Payload schema version. Unknown versions are treated as cache
            misses by the readers.
        value: Cached function result.
        dependencies: Dependency set that must be replayed on cache hits and
            used by invalidation metadata.
    """

    version: int
    value: object
    dependencies: frozenset[Dependency]

    def __reduce__(
        self,
    ) -> tuple[
        object,
        tuple[int, object, frozenset[Dependency]],
    ]:
        """Pickle entries through constructor args instead of slot state."""
        return (
            _rebuild_dependency_cache_entry,
            (self.version, self.value, self.dependencies),
        )


def _rebuild_dependency_cache_entry(
    version: int,
    value: object,
    dependencies: frozenset[Dependency],
) -> DependencyCacheEntry:
    return DependencyCacheEntry(
        version=version,
        value=value,
        dependencies=dependencies,
    )


@dataclass(frozen=True, slots=True)
class DependencyCachePrefetchBundle:
    """Private payload containing multiple dependency-cache entries."""

    version: int
    entries: Mapping[str, DependencyCacheEntry]

    def __reduce__(
        self,
    ) -> tuple[
        object,
        tuple[int, dict[str, DependencyCacheEntry]],
    ]:
        """Pickle bundles through constructor args instead of slot state."""
        return (
            _rebuild_dependency_cache_prefetch_bundle,
            (self.version, dict(self.entries)),
        )


def _rebuild_dependency_cache_prefetch_bundle(
    version: int,
    entries: dict[str, DependencyCacheEntry],
) -> DependencyCachePrefetchBundle:
    return DependencyCachePrefetchBundle(version=version, entries=entries)


@dataclass(frozen=True, slots=True)
class DependencyCachePrefetchValueBundle:
    """Private payload containing values for inactive dependency tracking."""

    version: int
    values: Mapping[str, object]

    def __reduce__(
        self,
    ) -> tuple[
        object,
        tuple[int, dict[str, object]],
    ]:
        """Pickle value bundles through constructor args instead of slot state."""
        return (
            _rebuild_dependency_cache_prefetch_value_bundle,
            (self.version, dict(self.values)),
        )


def _rebuild_dependency_cache_prefetch_value_bundle(
    version: int,
    values: dict[str, object],
) -> DependencyCachePrefetchValueBundle:
    return DependencyCachePrefetchValueBundle(version=version, values=values)


@dataclass(frozen=True, slots=True)
class DependencyCacheHit:
    """In-memory representation of a dependency-cache hit.

    `value` is the cached function result. `dependencies` are replayed into any
    active `DependencyTracker` scope before the caller returns `value`.
    """

    value: object
    dependencies: frozenset[Dependency]


class _TrustedDependencyCacheHit(DependencyCacheHit):
    """Cache hit whose dependencies were captured by DependencyTracker."""


class DependencyCacheBackend(Protocol):
    """Minimal cache backend shape for dependency-cache reads and writes."""

    def get(self, key: str, default: object = None) -> object:
        """Return a cached value or `default` when absent."""
        ...

    def set(self, key: str, value: object, timeout: int | None = None) -> None:
        """Store a cached value."""
        ...


class DependencyCacheGetManyBackend(DependencyCacheBackend, Protocol):
    """Cache backend that can bulk-read dependency-cache payloads."""

    def get_many(self, keys: Iterable[str]) -> Mapping[str, object]:
        """Return cached values for all found keys."""
        ...


class DependencyCacheSetManyBackend(DependencyCacheBackend, Protocol):
    """Cache backend that can bulk-write dependency-cache payloads."""

    def set_many(
        self,
        data: Mapping[str, object],
        timeout: int | None = None,
    ) -> Iterable[str] | None:
        """Store cached values for many keys."""
        ...


def make_dependency_cache_entry(
    value: object,
    dependencies: Iterable[Dependency],
    *,
    trusted_dependencies: bool | None = None,
) -> DependencyCacheEntry:
    """Build the current persisted dependency-cache payload.

    Args:
        value: Cached function result to persist.
        dependencies: Dependencies captured while computing the value.
        trusted_dependencies: Whether dependencies are known to come from
            `DependencyTracker`. When omitted, tracker-captured sets are
            detected automatically.

    Returns:
        A versioned `DependencyCacheEntry` with dependencies frozen.
    """
    if trusted_dependencies is None:
        trusted_dependencies = DependencyTracker._dependencies_are_tracker_captured(
            dependencies
        )
    return DependencyCacheEntry(
        version=(
            TRUSTED_DEPENDENCY_CACHE_ENTRY_VERSION
            if trusted_dependencies
            else DEPENDENCY_CACHE_ENTRY_VERSION
        ),
        value=value,
        dependencies=frozenset(dependencies),
    )


def dependency_cache_prefetch_bundle_key(manifest_key: str) -> str:
    """Return the private bundle key paired with a prefetch manifest key."""
    return f"{manifest_key}:bundle"


def dependency_cache_prefetch_value_bundle_key(manifest_key: str) -> str:
    """Return the private value-bundle key paired with a prefetch manifest key."""
    return f"{manifest_key}:values"


def dependency_cache_prefetch_segment_index_key(manifest_key: str) -> str:
    """Return the private segment-index key paired with a prefetch manifest."""
    return f"{manifest_key}:segments"


def dependency_cache_prefetch_segment_token(cache_keys: Iterable[str]) -> str:
    """Return a stable token for one prefetch segment's ordered cache keys."""
    digest = sha256(usedforsecurity=False)
    for cache_key in cache_keys:
        encoded_key = cache_key.encode()
        digest.update(len(encoded_key).to_bytes(8, "big"))
        digest.update(encoded_key)
    return digest.hexdigest()


def dependency_cache_prefetch_segment_bundle_key(
    manifest_key: str,
    segment_token: str,
) -> str:
    """Return the private full bundle key for one prefetch segment."""
    return f"{manifest_key}:segment:{segment_token}:bundle"


def dependency_cache_prefetch_segment_value_bundle_key(
    manifest_key: str,
    segment_token: str,
) -> str:
    """Return the private value bundle key for one prefetch segment."""
    return f"{manifest_key}:segment:{segment_token}:values"


def make_dependency_cache_prefetch_bundle(
    entries: Mapping[str, DependencyCacheEntry],
) -> DependencyCachePrefetchBundle:
    """Build a private prefetch bundle from dependency-cache entry payloads."""
    return DependencyCachePrefetchBundle(
        version=DEPENDENCY_CACHE_PREFETCH_BUNDLE_VERSION,
        entries=dict(entries),
    )


def make_dependency_cache_prefetch_value_bundle(
    entries: Mapping[str, DependencyCacheEntry],
) -> DependencyCachePrefetchValueBundle:
    """Build a private value-only bundle from dependency-cache entries."""
    return DependencyCachePrefetchValueBundle(
        version=DEPENDENCY_CACHE_PREFETCH_VALUE_BUNDLE_VERSION,
        values={cache_key: entry.value for cache_key, entry in entries.items()},
    )


def read_dependency_cache_prefetch_bundle_hits(
    cache_backend: DependencyCacheBackend,
    bundle_key: str,
) -> dict[str, DependencyCacheHit]:
    """Read dependency-cache hits from one private prefetch bundle payload."""
    entries = read_dependency_cache_prefetch_bundle_entries(cache_backend, bundle_key)
    if not entries:
        return {}

    hits: dict[str, DependencyCacheHit] = {}
    for cache_key, entry in entries.items():
        hit = _combined_payload_to_hit(entry)
        if isinstance(hit, DependencyCacheHit):
            hits[cache_key] = hit
    return hits


def read_dependency_cache_prefetch_bundle_entries(
    cache_backend: DependencyCacheBackend,
    bundle_key: str,
) -> dict[str, DependencyCacheEntry]:
    """Read raw dependency-cache entries from one private prefetch bundle."""
    payload = cache_backend.get(bundle_key, _MISSING)
    if not isinstance(payload, DependencyCachePrefetchBundle):
        return {}
    if payload.version != DEPENDENCY_CACHE_PREFETCH_BUNDLE_VERSION:
        return {}
    if not isinstance(payload.entries, Mapping):
        return {}
    return {
        cache_key: entry
        for cache_key, entry in payload.entries.items()
        if isinstance(cache_key, str) and isinstance(entry, DependencyCacheEntry)
    }


def read_dependency_cache_prefetch_bundle_values(
    cache_backend: DependencyCacheBackend,
    value_bundle_key: str,
) -> dict[str, object]:
    """Read value-only dependency-cache hits from one private bundle payload."""
    payload = cache_backend.get(value_bundle_key, _MISSING)
    if not isinstance(payload, DependencyCachePrefetchValueBundle):
        return {}
    if payload.version != DEPENDENCY_CACHE_PREFETCH_VALUE_BUNDLE_VERSION:
        return {}
    if not isinstance(payload.values, Mapping):
        return {}
    return {
        cache_key: value
        for cache_key, value in payload.values.items()
        if isinstance(cache_key, str)
    }


def read_many_dependency_cache_prefetch_bundle_hits(
    cache_backend: DependencyCacheBackend,
    bundle_keys: Iterable[str],
) -> dict[str, DependencyCacheHit]:
    """Read dependency-cache hits from multiple private prefetch bundles."""
    payloads = _get_many_or_loop(cache_backend, tuple(dict.fromkeys(bundle_keys)))
    hits: dict[str, DependencyCacheHit] = {}
    for payload in payloads.values():
        if not isinstance(payload, DependencyCachePrefetchBundle):
            continue
        if payload.version != DEPENDENCY_CACHE_PREFETCH_BUNDLE_VERSION:
            continue
        if not isinstance(payload.entries, Mapping):
            continue
        for cache_key, entry in payload.entries.items():
            if not isinstance(cache_key, str):
                continue
            hit = _combined_payload_to_hit(entry)
            if isinstance(hit, DependencyCacheHit):
                hits[cache_key] = hit
    return hits


def read_many_dependency_cache_prefetch_bundle_values(
    cache_backend: DependencyCacheBackend,
    value_bundle_keys: Iterable[str],
) -> dict[str, object]:
    """Read values from multiple private prefetch value bundles."""
    payloads = _get_many_or_loop(cache_backend, tuple(dict.fromkeys(value_bundle_keys)))
    values: dict[str, object] = {}
    for payload in payloads.values():
        if not isinstance(payload, DependencyCachePrefetchValueBundle):
            continue
        if payload.version != DEPENDENCY_CACHE_PREFETCH_VALUE_BUNDLE_VERSION:
            continue
        if not isinstance(payload.values, Mapping):
            continue
        for cache_key, value in payload.values.items():
            if isinstance(cache_key, str):
                values[cache_key] = value
    return values


def read_dependency_cache_hit(
    cache_backend: DependencyCacheBackend,
    cache_key: str,
    *,
    sentinel: object = _MISSING,
) -> DependencyCacheHit | object:
    """Read one dependency-cache entry, including legacy split entries.

    Combined entries store a `DependencyCacheEntry` at `cache_key`. Legacy split
    entries store the cached value at `cache_key` and dependencies at
    `{cache_key}:deps`. A present legacy value with a missing dependency key is
    a hit with an empty dependency set. Legacy dependency payloads must be
    iterable dependency tuples; malformed payloads are treated as misses.
    Unknown future combined-entry versions are treated as misses and return
    `sentinel`.

    Args:
        cache_backend: Backend used for value and legacy dependency reads.
        cache_key: Main cache key to read.
        sentinel: Object returned when no compatible cache hit exists.

    Returns:
        A `DependencyCacheHit` for combined or legacy entries, or `sentinel`
        when the main key is absent or holds an unsupported future entry
        version.

    Raises:
        Exception: Backend `get()` errors propagate unchanged.
    """
    payload = cache_backend.get(cache_key, sentinel)
    if payload is sentinel:
        return sentinel
    combined_hit = _combined_payload_to_hit(payload)
    if combined_hit is _MISSING:
        return sentinel
    if combined_hit is not None:
        return combined_hit
    dependency_payload = cache_backend.get(_legacy_deps_key(cache_key), ())
    dependencies = _legacy_dependency_set(dependency_payload)
    if dependencies is None:
        return sentinel
    return DependencyCacheHit(
        value=payload,
        dependencies=dependencies,
    )


def read_many_dependency_cache_hits(
    cache_backend: DependencyCacheBackend,
    cache_keys: Iterable[str],
) -> dict[str, DependencyCacheHit]:
    """Bulk-read dependency-cache hits for known keys.

    Duplicate cache keys are collapsed while preserving first-seen order.
    Backends with `get_many()` use one bulk read for main payloads and, when
    legacy split entries are present, one additional bulk read for dependency
    payloads. A present legacy value whose dependency key is absent from that
    second bulk read is returned as a hit with an empty dependency set. Backends
    without `get_many()` fall back to single-key reads. Missing main keys and
    unknown future combined-entry versions are omitted. Legacy entries with
    malformed dependency payloads are omitted.

    Args:
        cache_backend: Backend used for cache reads.
        cache_keys: Cache keys to inspect.

    Returns:
        Mapping of cache keys that had compatible hits to their
        `DependencyCacheHit` values.

    Raises:
        Exception: Backend `get()`/`get_many()` errors propagate unchanged.
    """
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
            dependencies = _legacy_dependency_set(legacy_deps.get(deps_key, ()))
            if dependencies is None:
                continue
            hits[key] = DependencyCacheHit(
                value=payloads[key],
                dependencies=dependencies,
            )
    return hits


def replay_dependency_cache_hit(hit: DependencyCacheHit) -> None:
    """Replay cached dependencies into active dependency tracking scopes.

    Args:
        hit: Cache hit whose dependency tuples should be tracked.

    Raises:
        TypeError: Propagated from `DependencyTracker.track()` if a dependency
            tuple has malformed value types.
        ValueError: Propagated from `DependencyTracker.track()` if a dependency
            tuple uses an unsupported operation.
    """
    if isinstance(hit, _TrustedDependencyCacheHit):
        DependencyTracker._track_many_validated(hit.dependencies)
        return
    for class_name, operation, identifier in hit.dependencies:
        DependencyTracker.track(class_name, operation, identifier)


def _legacy_deps_key(cache_key: str) -> str:
    return f"{cache_key}:deps"


def _legacy_dependency_set(payload: object) -> frozenset[Dependency] | None:
    if isinstance(payload, (str, bytes, Mapping)):
        return None
    try:
        dependencies = tuple(cast(Iterable[object], payload))
    except TypeError:
        return None
    if not all(_is_dependency_tuple(dependency) for dependency in dependencies):
        return None
    return frozenset(cast(tuple[Dependency, ...], dependencies))


def _is_dependency_tuple(value: object) -> TypeGuard[Dependency]:
    return (
        isinstance(value, tuple)
        and len(value) == 3
        and isinstance(value[0], str)
        and isinstance(value[1], str)
        and value[1] in _VALID_DEPENDENCY_ACTIONS
        and isinstance(value[2], str)
    )


def _combined_payload_to_hit(
    payload: object,
) -> DependencyCacheHit | None | _MissingSentinel:
    if not isinstance(payload, DependencyCacheEntry):
        return None
    if payload.version == DEPENDENCY_CACHE_ENTRY_VERSION:
        # Version 2 entries are structurally checked here and still validate
        # each dependency during replay.
        if not isinstance(payload.dependencies, frozenset):
            return _MISSING
        dependencies = payload.dependencies
        hit_type = DependencyCacheHit
    elif payload.version == TRUSTED_DEPENDENCY_CACHE_ENTRY_VERSION:
        # Version 3 entries are written only for dependency sets captured by
        # DependencyTracker, so replay can bulk-merge them without per-tuple
        # validation.
        if not isinstance(payload.dependencies, frozenset):
            return _MISSING
        dependencies = payload.dependencies
        hit_type = _TrustedDependencyCacheHit
    else:
        return _MISSING
    return hit_type(
        value=payload.value,
        dependencies=dependencies,
    )


def _supports_get_many(
    cache_backend: DependencyCacheBackend,
) -> TypeGuard[DependencyCacheGetManyBackend]:
    return callable(getattr(cache_backend, "get_many", None))


def _get_many_or_loop(
    cache_backend: DependencyCacheBackend,
    keys: tuple[str, ...],
) -> Mapping[str, object]:
    if not keys:
        return {}
    if _supports_get_many(cache_backend):
        return cache_backend.get_many(keys)
    values: dict[str, object] = {}
    for key in keys:
        value = cache_backend.get(key, _MISSING)
        if value is not _MISSING:
            values[key] = value
    return values


def _read_many_without_get_many(
    cache_backend: DependencyCacheBackend,
    cache_keys: tuple[str, ...],
) -> dict[str, DependencyCacheHit]:
    hits: dict[str, DependencyCacheHit] = {}
    for key in cache_keys:
        hit = read_dependency_cache_hit(cache_backend, key, sentinel=_MISSING)
        if isinstance(hit, DependencyCacheHit):
            hits[key] = hit
    return hits
