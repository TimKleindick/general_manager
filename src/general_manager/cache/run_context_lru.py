"""Process-local memory accounting for calculation run caches."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass
from itertools import islice
import math
import sys
from threading import RLock
from types import (
    CodeType,
    FunctionType,
    GetSetDescriptorType,
    MappingProxyType,
    MemberDescriptorType,
    MethodType,
    ModuleType,
)
from typing import Callable, Literal, Protocol, cast
from weakref import ReferenceType, WeakSet, ref

from django.core.exceptions import ImproperlyConfigured

from general_manager.conf import get_setting
from general_manager.logging import get_logger

RUN_CONTEXT_CACHE_MAX_BYTES_SETTING = "RUN_CONTEXT_CACHE_MAX_BYTES"
MIN_TRACKED_ENTRY_BYTES = 256
RUN_CONTEXT_SIZE_SAMPLE_THRESHOLD = 128
RUN_CONTEXT_SIZE_SAMPLE_COUNT = 64
RUN_CONTEXT_SIZE_SAFETY_MARGIN = 1.05
_INVALID_MAX_BYTES_MESSAGE = (
    'GENERAL_MANAGER["RUN_CONTEXT_CACHE_MAX_BYTES"] must be None or a '
    "non-negative integer number of bytes."
)

_SHALLOW_LEAF_TYPES = (ModuleType, type, FunctionType, MethodType, CodeType)
_ATOMIC_LEAF_TYPES = (
    type(None),
    bool,
    int,
    float,
    complex,
    str,
    bytes,
    bytearray,
    range,
)
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
_TYPE_MRO_DESCRIPTOR = cast(GetSetDescriptorType, type.__dict__["__mro__"])
_TYPE_DICT_DESCRIPTOR = cast(GetSetDescriptorType, type.__dict__["__dict__"])

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


@dataclass(frozen=True)
class _StaticStoragePlan:
    instance_dict_descriptors: tuple[GetSetDescriptorType, ...]
    slot_descriptors: tuple[MemberDescriptorType, ...]


@dataclass(frozen=True)
class _WeightedCandidate:
    value: object
    weight: float
    ancestor_ids: frozenset[int] = frozenset()


class ProcessRunContextCacheBudget:
    """Coordinate weighted LRU eviction across live calculation run caches."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._owners: WeakSet[RunContextCacheOwner] = WeakSet()
        self._owner_references: dict[int, ReferenceType[RunContextCacheOwner]] = {}
        self._entries: OrderedDict[TrackedKey, _TrackedEntry] = OrderedDict()
        self._total_bytes = 0
        self._max_bytes: int | None = None
        self._configuration_generation = 0

    @property
    def estimated_bytes(self) -> int:
        """Return the coordinator's current estimated cache footprint."""
        with self._lock:
            return self._total_bytes

    def register(self, owner: RunContextCacheOwner, max_bytes: int | None) -> None:
        """Register an owner and rebuild tracked state when the limit changes."""
        with self._lock:
            is_new_owner = owner not in self._owners
            self._owners.add(owner)
            self._owner_reference_locked(owner)
            if max_bytes == self._max_bytes:
                if is_new_owner and max_bytes is not None:
                    self._track_owner_entries_locked(owner)
                return

            previous_max_bytes = self._max_bytes
            self._max_bytes = max_bytes
            self._configuration_generation += 1
            if max_bytes is None:
                self._entries.clear()
                self._total_bytes = 0
                return

            if previous_max_bytes is None:
                self._rebuild_locked()
            elif is_new_owner:
                self._track_owner_entries_locked(owner)
                self._evict_excess_locked()
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
        if self._max_bytes is None:
            return
        while True:
            with self._lock:
                max_bytes = self._max_bytes
                generation = self._configuration_generation
            if max_bytes is None:
                return

            estimated_bytes = estimate_cache_entry_size(
                key,
                value,
                stop_after=max_bytes,
            )

            with self._lock:
                if generation != self._configuration_generation:
                    continue
                self._track_estimated_locked(
                    owner,
                    namespace,
                    key,
                    estimated_bytes,
                    max_bytes,
                )
                return

    def touch(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
    ) -> None:
        """Mark one tracked entry as most recently used."""
        if self._max_bytes is None:
            return
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
        if self._max_bytes is None:
            return
        with self._lock:
            if self._max_bytes is None:
                return
            self._remove_entry_locked((id(owner), namespace, key))

    def refresh(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        value: object,
    ) -> None:
        """Re-estimate an already stored mutable value."""
        if self._max_bytes is None:
            return
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
            self._track_owner_entries_locked(owner)

    def _track_owner_entries_locked(self, owner: RunContextCacheOwner) -> None:
        entries = tuple(owner._iter_run_cache_entries())
        for namespace, key, value in entries:
            self._track_value_locked(owner, namespace, key, value)

    def _track_value_locked(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        value: object,
    ) -> None:
        """Estimate rebuild entries while locked; normal track() admission is unlocked.

        Only configuration rebuilds estimate while holding the coordinator lock.
        """
        max_bytes = self._max_bytes
        if max_bytes is None:
            return
        estimated_bytes = estimate_cache_entry_size(
            key,
            value,
            stop_after=max_bytes,
        )
        self._track_estimated_locked(
            owner,
            namespace,
            key,
            estimated_bytes,
            max_bytes,
        )

    def _track_estimated_locked(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        estimated_bytes: int,
        max_bytes: int,
    ) -> None:
        tracked_key = (id(owner), namespace, key)
        self._remove_entry_locked(tracked_key)
        owner_reference = self._owner_reference_locked(owner)
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


def _is_native_descriptor_for_storage(
    descriptor: object,
    descriptor_type: type[object],
    declaring_class: type[object],
    storage_name: str,
) -> bool:
    if type(descriptor) is not descriptor_type:
        return False
    try:
        return (
            object.__getattribute__(descriptor, "__objclass__") is declaring_class
            and object.__getattribute__(descriptor, "__name__") == storage_name
        )
    except (AttributeError, TypeError):
        return False


def _get_static_type_mro(
    candidate_type: type[object],
) -> tuple[type[object], ...] | None:
    try:
        candidate_mro = GetSetDescriptorType.__get__(
            _TYPE_MRO_DESCRIPTOR,
            candidate_type,
            type,
        )
    except (AttributeError, TypeError):
        return None
    if type(candidate_mro) is not tuple:
        return None
    return cast(tuple[type[object], ...], candidate_mro)


def _get_static_class_metadata(
    candidate_mro: tuple[type[object], ...],
) -> tuple[tuple[type[object], Mapping[str, object]], ...] | None:
    metadata: list[tuple[type[object], Mapping[str, object]]] = []
    for cls in candidate_mro:
        try:
            class_dict = GetSetDescriptorType.__get__(
                _TYPE_DICT_DESCRIPTOR,
                cls,
                type,
            )
        except (AttributeError, TypeError):
            return None
        if type(class_dict) is not MappingProxyType:
            return None
        metadata.append(
            (
                cls,
                cast(Mapping[str, object], class_dict),
            )
        )
    return tuple(metadata)


def _static_storage_plan(
    candidate_type: type[object],
) -> _StaticStoragePlan | None:
    candidate_mro = _get_static_type_mro(candidate_type)
    if candidate_mro is None:
        return None
    if any(
        base_type is leaf_type
        for base_type in candidate_mro
        for leaf_type in _SHALLOW_LEAF_TYPES
    ):
        return _StaticStoragePlan((), ())
    class_metadata = _get_static_class_metadata(candidate_mro)
    if class_metadata is None:
        return None

    instance_dict_descriptors: list[GetSetDescriptorType] = []
    slot_descriptors: list[MemberDescriptorType] = []
    for cls, class_dict in class_metadata:
        descriptor = class_dict.get("__dict__")
        if _is_native_descriptor_for_storage(
            descriptor, GetSetDescriptorType, cls, "__dict__"
        ):
            instance_dict_descriptors.append(cast(GetSetDescriptorType, descriptor))
        for storage_name, slot_descriptor in class_dict.items():
            if _is_native_descriptor_for_storage(
                slot_descriptor, MemberDescriptorType, cls, storage_name
            ):
                slot_descriptors.append(cast(MemberDescriptorType, slot_descriptor))
    return _StaticStoragePlan(tuple(instance_dict_descriptors), tuple(slot_descriptors))


def _stratified_indexes(length: int, sample_count: int) -> tuple[int, ...]:
    if sample_count >= length:
        return tuple(range(length))
    return tuple(
        (index * (length - 1)) // (sample_count - 1) for index in range(sample_count)
    )


def _is_exact_type(
    candidate_type: type[object], types: tuple[type[object], ...]
) -> bool:
    return any(candidate_type is expected_type for expected_type in types)


def _sample_container_children(
    candidate: _WeightedCandidate,
) -> tuple[_WeightedCandidate, ...]:
    value = candidate.value
    value_type = type(value)
    child_ancestor_ids = candidate.ancestor_ids | frozenset((id(value),))
    if _is_exact_type(value_type, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        length = len(sequence)
        if length <= RUN_CONTEXT_SIZE_SAMPLE_THRESHOLD:
            return tuple(
                _WeightedCandidate(item, candidate.weight, child_ancestor_ids)
                for item in sequence
            )
        sample_indexes = _stratified_indexes(length, RUN_CONTEXT_SIZE_SAMPLE_COUNT)
        sample_weight = (
            candidate.weight
            * length
            / len(sample_indexes)
            * RUN_CONTEXT_SIZE_SAFETY_MARGIN
        )
        return tuple(
            _WeightedCandidate(sequence[index], sample_weight, child_ancestor_ids)
            for index in sample_indexes
        )

    if value_type is dict:
        mapping = cast(dict[object, object], value)
        if len(mapping) <= RUN_CONTEXT_SIZE_SAMPLE_THRESHOLD:
            return tuple(
                _WeightedCandidate(item, candidate.weight, child_ancestor_ids)
                for entry in mapping.items()
                for item in entry
            )
        first_entries = tuple(
            islice(mapping.items(), RUN_CONTEXT_SIZE_SAMPLE_COUNT // 2)
        )
        last_entries = tuple(
            islice(reversed(mapping.items()), RUN_CONTEXT_SIZE_SAMPLE_COUNT // 2)
        )
        sample_entries: list[tuple[object, object]] = []
        for item_key, item_value in (*first_entries, *last_entries):
            if all(item_key is not existing_key for existing_key, _ in sample_entries):
                sample_entries.append((item_key, item_value))
        sample_weight = (
            candidate.weight
            * len(mapping)
            / len(sample_entries)
            * RUN_CONTEXT_SIZE_SAFETY_MARGIN
        )
        return tuple(
            _WeightedCandidate(item, sample_weight, child_ancestor_ids)
            for entry in sample_entries
            for item in entry
        )

    if _is_exact_type(value_type, (set, frozenset)):
        container = cast(set[object] | frozenset[object], value)
        if len(container) <= RUN_CONTEXT_SIZE_SAMPLE_THRESHOLD:
            return tuple(
                _WeightedCandidate(item, candidate.weight, child_ancestor_ids)
                for item in container
            )
        sample_items = tuple(islice(container, RUN_CONTEXT_SIZE_SAMPLE_COUNT))
        sample_weight = (
            candidate.weight
            * len(container)
            / len(sample_items)
            * RUN_CONTEXT_SIZE_SAFETY_MARGIN
        )
        return tuple(
            _WeightedCandidate(item, sample_weight, child_ancestor_ids)
            for item in sample_items
        )

    return ()


def estimate_cache_entry_size(
    key: object,
    value: object,
    *,
    stop_after: int | None,
) -> int:
    """Estimate owned bytes for one cache entry without unbounded traversal."""
    measured_bytes = 0
    seen_weights: dict[int, float] = {}
    candidates = [_WeightedCandidate(key, 1.0), _WeightedCandidate(value, 1.0)]
    storage_plans: dict[int, _StaticStoragePlan | None] = {}

    while candidates:
        candidate = candidates.pop()
        candidate_id = id(candidate.value)
        if candidate_id in candidate.ancestor_ids:
            continue
        previous_weight = seen_weights.get(candidate_id, 0.0)
        incremental_weight = candidate.weight - previous_weight
        if incremental_weight <= 0:
            continue
        seen_weights[candidate_id] = candidate.weight

        candidate_value = candidate.value
        candidate_type = type(candidate_value)
        try:
            if _is_exact_type(candidate_type, _SIZED_BUILTIN_TYPES):
                shallow_size = sys.getsizeof(candidate_value)
            else:
                shallow_size = object.__sizeof__(candidate_value)
        except Exception:  # noqa: BLE001 - conservative accounting must survive sizing errors.
            shallow_size = MIN_TRACKED_ENTRY_BYTES
        measured_bytes += math.ceil(shallow_size * incremental_weight)

        if stop_after is not None and measured_bytes > stop_after:
            return stop_after + 1

        if _is_exact_type(candidate_type, _ATOMIC_LEAF_TYPES):
            continue

        if _is_exact_type(candidate_type, (dict, tuple, list, set, frozenset)):
            candidates.extend(
                _sample_container_children(
                    _WeightedCandidate(candidate_value, incremental_weight)
                )
            )
        else:
            candidate_type_id = id(candidate_type)
            if candidate_type_id not in storage_plans:
                storage_plans[candidate_type_id] = _static_storage_plan(candidate_type)
            storage_plan = storage_plans[candidate_type_id]
            if storage_plan is None:
                continue
            for instance_dict_descriptor in storage_plan.instance_dict_descriptors:
                try:
                    candidates.append(
                        _WeightedCandidate(
                            GetSetDescriptorType.__get__(
                                instance_dict_descriptor,
                                candidate_value,
                                candidate_type,
                            ),
                            incremental_weight,
                            candidate.ancestor_ids | frozenset((candidate_id,)),
                        )
                    )
                except (AttributeError, TypeError):
                    pass

            for slot_descriptor in storage_plan.slot_descriptors:
                try:
                    candidates.append(
                        _WeightedCandidate(
                            MemberDescriptorType.__get__(
                                slot_descriptor,
                                candidate_value,
                                candidate_type,
                            ),
                            incremental_weight,
                            candidate.ancestor_ids | frozenset((candidate_id,)),
                        )
                    )
                except (AttributeError, TypeError):
                    pass

    return max(MIN_TRACKED_ENTRY_BYTES, measured_bytes)


run_context_cache_budget = ProcessRunContextCacheBudget()
