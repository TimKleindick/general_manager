"""Process-local memory accounting for calculation run caches."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
import sys
from threading import RLock
from types import CodeType, FunctionType, MethodType, ModuleType
from typing import Callable, Literal, Protocol, cast
from weakref import ReferenceType, WeakSet, ref

from django.core.exceptions import ImproperlyConfigured

from general_manager.conf import get_setting
from general_manager.logging import get_logger

RUN_CONTEXT_CACHE_MAX_BYTES_SETTING = "RUN_CONTEXT_CACHE_MAX_BYTES"
MIN_TRACKED_ENTRY_BYTES = 256
_INVALID_MAX_BYTES_MESSAGE = (
    'GENERAL_MANAGER["RUN_CONTEXT_CACHE_MAX_BYTES"] must be None or a '
    "non-negative integer number of bytes."
)

_SHALLOW_LEAF_TYPES = (ModuleType, type, FunctionType, MethodType, CodeType)
_SIZED_BUILTIN_TYPES = (
    type(None),
    bool,
    int,
    float,
    complex,
    str,
    bytes,
    bytearray,
    range,
    dict,
    tuple,
    list,
    set,
    frozenset,
)

RunCacheNamespace = Literal["values", "dependency_hits"]
TrackedKey = tuple[int, RunCacheNamespace, Hashable]

logger = get_logger("cache.run_context_lru")


class RunContextCacheOwner(Protocol):
    """Storage that participates in process-wide run-cache budgeting."""

    def _iter_run_cache_entries(
        self,
    ) -> Iterable[tuple[RunCacheNamespace, Hashable, object]]:
        """Yield every currently stored run-cache entry."""

    def _evict_run_cache_entry(
        self, namespace: RunCacheNamespace, key: Hashable
    ) -> None:
        """Remove one entry selected by the process-wide coordinator."""


@dataclass(frozen=True)
class _TrackedEntry:
    owner: ReferenceType[RunContextCacheOwner]
    namespace: RunCacheNamespace
    key: Hashable
    size: int


class ProcessRunContextCacheBudget:
    """Coordinate weighted LRU eviction across live calculation run caches."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._owners: WeakSet[RunContextCacheOwner] = WeakSet()
        self._owner_references: dict[int, ReferenceType[RunContextCacheOwner]] = {}
        self._entries: OrderedDict[TrackedKey, _TrackedEntry] = OrderedDict()
        self._total_bytes = 0
        self._max_bytes: int | None = None

    @property
    def estimated_bytes(self) -> int:
        """Return the coordinator's current estimated cache footprint."""
        with self._lock:
            return self._total_bytes

    def register(self, owner: RunContextCacheOwner, max_bytes: int | None) -> None:
        """Register an owner and rebuild tracked state when the limit changes."""
        with self._lock:
            self._owners.add(owner)
            self._owner_reference_locked(owner)
            if max_bytes == self._max_bytes:
                return

            previous_max_bytes = self._max_bytes
            self._max_bytes = max_bytes
            if max_bytes is None:
                self._entries.clear()
                self._total_bytes = 0
                return

            if previous_max_bytes is None:
                self._rebuild_locked()
            else:
                self._evict_excess_locked()

    def track(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        value: object,
    ) -> None:
        """Record a stored entry and evict least-recently-used entries if needed."""
        with self._lock:
            self._track_locked(owner, namespace, key, value)

    def touch(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
    ) -> None:
        """Mark one tracked entry as most recently used."""
        with self._lock:
            if self._max_bytes is None:
                return
            tracked_key = (id(owner), namespace, key)
            if tracked_key in self._entries:
                self._entries.move_to_end(tracked_key)

    def remove(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
    ) -> None:
        """Discard bookkeeping for an entry removed from owner storage."""
        with self._lock:
            self._remove_entry_locked((id(owner), namespace, key))

    def refresh(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        value: object,
    ) -> None:
        """Re-estimate an already stored mutable value."""
        self.track(owner, namespace, key, value)

    def clear_context(self, owner: RunContextCacheOwner) -> None:
        """Remove one owner's accounting without changing its cache storage."""
        with self._lock:
            owner_id = id(owner)
            self._remove_owner_entries_locked(owner_id)
            self._owner_references.pop(owner_id, None)
            self._owners.discard(owner)

    def _rebuild_locked(self) -> None:
        self._entries.clear()
        self._total_bytes = 0
        for owner in tuple(self._owners):
            entries = tuple(owner._iter_run_cache_entries())
            for namespace, key, value in entries:
                self._track_locked(owner, namespace, key, value)

    def _track_locked(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        value: object,
    ) -> None:
        max_bytes = self._max_bytes
        if max_bytes is None:
            return

        tracked_key = (id(owner), namespace, key)
        self._remove_entry_locked(tracked_key)
        owner_reference = self._owner_reference_locked(owner)
        estimated_bytes = estimate_cache_entry_size(
            key,
            value,
            stop_after=max_bytes,
        )
        if estimated_bytes > max_bytes:
            owner._evict_run_cache_entry(namespace, key)
            logger.debug(
                "run cache entry skipped because it exceeds the process budget",
                context={
                    "namespace": namespace,
                    "estimated_bytes": estimated_bytes,
                    "configured_bytes": max_bytes,
                },
            )
            return

        self._entries[tracked_key] = _TrackedEntry(
            owner=owner_reference,
            namespace=namespace,
            key=key,
            size=estimated_bytes,
        )
        self._total_bytes += estimated_bytes
        self._evict_excess_locked()

    def _evict_excess_locked(self) -> None:
        while self._entries:
            max_bytes = self._max_bytes
            if max_bytes is None or self._total_bytes <= max_bytes:
                return
            _, entry = self._entries.popitem(last=False)
            self._total_bytes -= entry.size
            owner = entry.owner()
            if owner is None:
                continue
            owner._evict_run_cache_entry(entry.namespace, entry.key)
            logger.debug(
                "run cache entry evicted by process-wide LRU budget",
                context={
                    "namespace": entry.namespace,
                    "estimated_bytes": entry.size,
                    "configured_bytes": max_bytes,
                },
            )

    def _remove_entry_locked(self, tracked_key: TrackedKey) -> None:
        entry = self._entries.pop(tracked_key, None)
        if entry is not None:
            self._total_bytes -= entry.size

    def _remove_owner_entries_locked(self, owner_id: int) -> None:
        for tracked_key in tuple(self._entries):
            if tracked_key[0] == owner_id:
                self._remove_entry_locked(tracked_key)

    def _owner_reference_locked(
        self,
        owner: RunContextCacheOwner,
    ) -> ReferenceType[RunContextCacheOwner]:
        owner_id = id(owner)
        owner_reference = self._owner_references.get(owner_id)
        if owner_reference is not None and owner_reference() is owner:
            return owner_reference

        if owner_reference is not None:
            self._remove_owner_entries_locked(owner_id)
        owner_reference = ref(owner, self._owner_finalizer(owner_id))
        self._owner_references[owner_id] = owner_reference
        return owner_reference

    def _owner_finalizer(
        self,
        owner_id: int,
    ) -> Callable[[ReferenceType[RunContextCacheOwner]], None]:
        def remove_dead_owner(
            owner_reference: ReferenceType[RunContextCacheOwner],
        ) -> None:
            with self._lock:
                if self._owner_references.get(owner_id) is not owner_reference:
                    return
                self._remove_owner_entries_locked(owner_id)
                self._owner_references.pop(owner_id, None)

        return remove_dead_owner


def resolve_run_context_cache_max_bytes() -> int | None:
    configured = get_setting(RUN_CONTEXT_CACHE_MAX_BYTES_SETTING)
    if configured is None:
        return None
    if isinstance(configured, bool) or not isinstance(configured, int):
        raise ImproperlyConfigured(_INVALID_MAX_BYTES_MESSAGE)
    if configured < 0:
        raise ImproperlyConfigured(_INVALID_MAX_BYTES_MESSAGE)
    return configured


def estimate_cache_entry_size(
    key: object,
    value: object,
    *,
    stop_after: int | None,
) -> int:
    """Estimate owned bytes for one cache entry without unbounded traversal."""
    measured_bytes = 0
    seen: set[int] = set()
    candidates = [key, value]

    while candidates:
        candidate = candidates.pop()
        candidate_id = id(candidate)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)

        candidate_type = type(candidate)
        try:
            if candidate_type in _SIZED_BUILTIN_TYPES:
                measured_bytes += sys.getsizeof(candidate)
            else:
                measured_bytes += object.__sizeof__(candidate)
        except Exception:  # noqa: BLE001 - conservative accounting must survive sizing errors.
            measured_bytes += MIN_TRACKED_ENTRY_BYTES

        if stop_after is not None and measured_bytes > stop_after:
            return stop_after + 1

        if isinstance(candidate, _SHALLOW_LEAF_TYPES):
            continue
        if candidate_type is dict:
            mapping = cast(dict[object, object], candidate)
            for item_key, item_value in mapping.items():
                candidates.extend((item_key, item_value))
        elif candidate_type in (tuple, list, set, frozenset):
            candidates.extend(cast(Iterable[object], candidate))
        else:
            try:
                candidates.append(object.__getattribute__(candidate, "__dict__"))
            except (AttributeError, TypeError):
                pass

            for cls in type.__getattribute__(candidate_type, "__mro__"):
                class_dict = type.__getattribute__(cls, "__dict__")
                slots = class_dict.get("__slots__")
                if type(slots) is str:
                    slots = (slots,)
                if type(slots) not in (tuple, list, set, frozenset):
                    continue
                for slot in slots:
                    if type(slot) is not str:
                        continue
                    if slot in {"__dict__", "__weakref__"}:
                        continue
                    if slot.startswith("__") and not slot.endswith("__"):
                        class_name = type.__getattribute__(cls, "__name__")
                        slot = f"_{class_name.lstrip('_')}{slot}"
                    try:
                        candidates.append(object.__getattribute__(candidate, slot))
                    except (AttributeError, TypeError):
                        pass

    return max(MIN_TRACKED_ENTRY_BYTES, measured_bytes)


run_context_cache_budget = ProcessRunContextCacheBudget()
