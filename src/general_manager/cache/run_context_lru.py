"""Process-local memory accounting for calculation run caches."""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from itertools import islice
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
RUN_CONTEXT_CALIBRATION_INTERVAL = 256
RUN_CONTEXT_CALIBRATION_WINDOW = 8
RUN_CONTEXT_CALIBRATION_HISTORY_LIMIT = 256
RUN_CONTEXT_CALIBRATION_CANDIDATE_LIMIT = 2_048
RUN_CONTEXT_FIXED_POINT_SCALE = 1 << 20
RUN_CONTEXT_TRACK_MAX_REESTIMATES = 3
RUN_CONTEXT_RECENCY_ENABLE_PERCENT = 80
RUN_CONTEXT_RECENCY_DISABLE_PERCENT = 70
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
_ATOMIC_LEAF_TYPE_IDS = frozenset(
    id(candidate_type) for candidate_type in _ATOMIC_LEAF_TYPES
)
_SIZED_BUILTIN_TYPE_IDS = frozenset(
    id(candidate_type) for candidate_type in _SIZED_BUILTIN_TYPES
)
_SEQUENCE_TYPE_IDS = frozenset((id(list), id(tuple)))
_SET_TYPE_IDS = frozenset((id(set), id(frozenset)))
_CONTAINER_TYPE_IDS = frozenset((id(dict), id(tuple), id(list), id(set), id(frozenset)))
_TYPE_MRO_DESCRIPTOR = cast(GetSetDescriptorType, type.__dict__["__mro__"])
_TYPE_DICT_DESCRIPTOR = cast(GetSetDescriptorType, type.__dict__["__dict__"])

RunCacheNamespace = Literal["values", "dependency_hits"]
StorageFamily = Literal[
    "dict",
    "list",
    "tuple",
    "set",
    "frozenset",
    "instance_dict",
    "slots",
    "shallow_leaf",
    "opaque",
]
StratumKey = tuple[RunCacheNamespace, StorageFamily, int]
TrackedKey = tuple[int, RunCacheNamespace, Hashable]
CalibrationPair = tuple[int, int]

logger = get_logger("cache.run_context_lru")


def _eviction_target(max_bytes: int) -> int:
    return max_bytes * 95 // 100


class RunContextCacheOwner(Protocol):
    """Storage that participates in process-wide run-cache budgeting."""

    def _set_run_cache_modes(
        self,
        budget_enabled: bool,
        recency_enabled: bool,
        generation: int,
    ) -> None:
        """Refresh the owner's cached process-budget and recency modes."""

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
    exact_bytes: int
    stratum: StratumKey | None
    shallow_bytes: int


@dataclass
class _StratumState:
    entry_count: int = 0
    shallow_total: int = 0
    admission_count: int = 0
    samples: deque[CalibrationPair] = field(
        default_factory=lambda: deque(maxlen=RUN_CONTEXT_CALIBRATION_WINDOW)
    )
    sampled_shallow: int = field(init=False)
    sampled_residual: int = field(init=False)

    def __post_init__(self) -> None:
        self.sampled_shallow = sum(shallow for shallow, _deep in self.samples)
        self.sampled_residual = sum(
            max(0, deep - MIN_TRACKED_ENTRY_BYTES) for _shallow, deep in self.samples
        )

    def modeled_bytes(self) -> int:
        if self.entry_count == 0:
            return 0
        assert self.samples
        projected_residual = (
            self.shallow_total * self.sampled_residual
            + max(1, self.sampled_shallow)
            - 1
        ) // max(1, self.sampled_shallow)
        return self.entry_count * MIN_TRACKED_ENTRY_BYTES + projected_residual

    def should_calibrate_next(self) -> bool:
        next_admission = self.admission_count + 1
        return self.admission_count == 0 or (
            next_admission % RUN_CONTEXT_CALIBRATION_INTERVAL == 0
        )


@dataclass(frozen=True)
class _CalibrationMetadata:
    admission_count: int
    samples: tuple[CalibrationPair, ...]

    def should_calibrate_next(self) -> bool:
        next_admission = self.admission_count + 1
        return self.admission_count == 0 or (
            next_admission % RUN_CONTEXT_CALIBRATION_INTERVAL == 0
        )


@dataclass(frozen=True)
class _StaticStoragePlan:
    instance_dict_descriptors: tuple[GetSetDescriptorType, ...]
    slot_descriptors: tuple[MemberDescriptorType, ...]


@dataclass(frozen=True)
class _AdmissionSignal:
    stratum: StratumKey | None
    shallow_bytes: int
    exact_bytes: int | None


@dataclass(frozen=True)
class _WeightedCandidate:
    value: object
    weight: int
    ancestor_ids: frozenset[int] = frozenset()


_storage_plan_cache_lock = RLock()
_storage_plan_cache: dict[
    int,
    tuple[ReferenceType[type[object]], _StaticStoragePlan | None],
] = {}
_calibration_visit_observer: Callable[[object], None] | None = None


class ProcessRunContextCacheBudget:
    """Coordinate weighted LRU eviction across live calculation run caches."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._owners: WeakSet[RunContextCacheOwner] = WeakSet()
        self._owner_references: dict[int, ReferenceType[RunContextCacheOwner]] = {}
        self._entries: OrderedDict[TrackedKey, _TrackedEntry] = OrderedDict()
        self._strata: dict[StratumKey, _StratumState] = {}
        self._calibration_history: OrderedDict[StratumKey, _CalibrationMetadata] = (
            OrderedDict()
        )
        self._mru_key: TrackedKey | None = None
        self._exact_total_bytes = 0
        self._total_bytes = 0
        self._max_bytes: int | None = None
        self._configuration_generation = 0
        self._next_admission_generation = 0
        self._owner_lifecycle_generations: dict[int, int] = {}
        self._entry_attempt_generations: dict[TrackedKey, int] = {}
        self._recency_enabled = False
        self._mode_generation = 0
        self._published_modes: tuple[bool, bool] | None = None
        self._mode_publications: deque[
            tuple[tuple[RunContextCacheOwner, ...], bool, bool, int]
        ] = deque()

    @property
    def estimated_bytes(self) -> int:
        """Return the coordinator's current estimated cache footprint."""
        with self._lock:
            return self._total_bytes

    @property
    def is_enabled(self) -> bool:
        """Return whether process-wide run-cache accounting is enabled."""
        return self._max_bytes is not None

    @property
    def is_recency_enabled(self) -> bool:
        """Return whether reads should currently publish LRU recency."""
        return self._recency_enabled

    def _desired_recency_locked(self) -> bool:
        max_bytes = self._max_bytes
        if max_bytes is None or max_bytes <= 0:
            return False
        if self._recency_enabled:
            return (
                self._total_bytes * 100
                >= max_bytes * RUN_CONTEXT_RECENCY_DISABLE_PERCENT
            )
        return self._total_bytes * 100 >= max_bytes * RUN_CONTEXT_RECENCY_ENABLE_PERCENT

    def _capture_mode_publication_locked(
        self,
        *,
        new_owner: RunContextCacheOwner | None = None,
    ) -> None:
        recency_enabled = self._desired_recency_locked()
        modes = (self._max_bytes is not None, recency_enabled)
        if modes != self._published_modes:
            self._recency_enabled = recency_enabled
            self._published_modes = modes
            self._mode_generation += 1
            owners = tuple(self._owners)
        elif new_owner is not None:
            owners = (new_owner,)
        else:
            return
        self._mode_publications.append(
            (owners, modes[0], modes[1], self._mode_generation)
        )

    def _publish_modes(self) -> None:
        if not self._mode_publications:
            return
        is_owned = getattr(self._lock, "_is_owned", None)
        if is_owned is not None and is_owned():
            return
        first_error: BaseException | None = None
        while True:
            with self._lock:
                if not self._mode_publications:
                    if first_error is not None:
                        raise first_error
                    return
                publication = self._mode_publications.popleft()
            owners, budget_enabled, recency_enabled, generation = publication
            for owner in owners:
                try:
                    owner._set_run_cache_modes(
                        budget_enabled,
                        recency_enabled,
                        generation,
                    )
                except BaseException as error:  # noqa: BLE001
                    if first_error is None:
                        first_error = error

    def _reconcile_modes_after_error(self, operation_error: BaseException) -> None:
        with self._lock:
            self._capture_mode_publication_locked()
        try:
            self._publish_modes()
        except BaseException as publication_error:  # noqa: BLE001
            operation_error.add_note(
                f"run-cache mode publication also failed: {publication_error!r}"
            )

    def register(self, owner: RunContextCacheOwner, max_bytes: int | None) -> None:
        """Register an owner and rebuild tracked state when the limit changes."""
        try:
            with self._lock:
                is_new_owner = owner not in self._owners
                self._owners.add(owner)
                self._owner_reference_locked(owner)
                if max_bytes == self._max_bytes:
                    if is_new_owner and max_bytes is not None:
                        self._track_owner_entries_locked(owner)
                    self._capture_mode_publication_locked(
                        new_owner=owner if is_new_owner else None
                    )
                else:
                    previous_max_bytes = self._max_bytes
                    self._max_bytes = max_bytes
                    self._configuration_generation += 1
                    self._calibration_history.clear()
                    if max_bytes is None:
                        self._entries.clear()
                        self._strata.clear()
                        self._mru_key = None
                        self._entry_attempt_generations.clear()
                        self._exact_total_bytes = 0
                        self._total_bytes = 0
                    elif previous_max_bytes is None:
                        self._rebuild_locked()
                    elif is_new_owner:
                        self._track_owner_entries_locked(owner)
                        self._evict_excess_locked()
                    else:
                        self._evict_excess_locked()
                    self._capture_mode_publication_locked()
        except BaseException as error:
            self._reconcile_modes_after_error(error)
            raise
        self._publish_modes()

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
        try:
            self._track_accounting(owner, namespace, key, value)
        except BaseException as error:
            self._reconcile_modes_after_error(error)
            raise
        self._publish_modes()

    def _track_accounting(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        value: object,
    ) -> None:
        if self._max_bytes is None:
            return
        signal = _admission_signal(namespace, key, value)
        with self._lock:
            max_bytes = self._max_bytes
            if max_bytes is None:
                return
            owner_id = id(owner)
            tracked_key = (owner_id, namespace, key)
            if max_bytes == 0:
                self._entry_attempt_generations.pop(tracked_key, None)
                self._remove_entry_accounting_locked(tracked_key)
                owner._evict_run_cache_entry(namespace, key)
                self._capture_mode_publication_locked()
                return

            requires_calibration = False
            if signal.stratum is not None:
                state = self._strata.get(
                    signal.stratum
                ) or self._calibration_history.get(signal.stratum)
                requires_calibration = state is None or state.should_calibrate_next()
            if not requires_calibration:
                self._entry_attempt_generations.pop(tracked_key, None)
                self._publish_entry_locked(
                    owner,
                    namespace,
                    key,
                    signal,
                    estimated_bytes=None,
                    max_bytes=max_bytes,
                )
                self._capture_mode_publication_locked()
                return

            configuration_generation = self._configuration_generation
            owner_lifecycle_generation = self._owner_lifecycle_generation_locked(
                owner_id
            )
            entry_attempt_generation = self._next_admission_generation_locked()
            self._entry_attempt_generations[tracked_key] = entry_attempt_generation

        reestimate_count = 0
        while True:
            try:
                estimated_bytes = estimate_cache_entry_size(
                    key,
                    value,
                    stop_after=max_bytes,
                )
            except BaseException:
                with self._lock:
                    if (
                        owner_lifecycle_generation
                        == self._owner_lifecycle_generations.get(owner_id, 0)
                        and entry_attempt_generation
                        == self._entry_attempt_generations.get(tracked_key, 0)
                    ):
                        self._entry_attempt_generations.pop(tracked_key, None)
                        self._remove_entry_locked(tracked_key)
                        owner._evict_run_cache_entry(namespace, key)
                        self._capture_mode_publication_locked()
                raise

            with self._lock:
                if owner_lifecycle_generation != self._owner_lifecycle_generations.get(
                    owner_id, 0
                ) or entry_attempt_generation != self._entry_attempt_generations.get(
                    tracked_key, 0
                ):
                    return
                if configuration_generation != self._configuration_generation:
                    max_bytes = self._max_bytes
                    configuration_generation = self._configuration_generation
                    if max_bytes is None:
                        return
                    if max_bytes == 0:
                        self._entry_attempt_generations.pop(tracked_key, None)
                        self._remove_entry_accounting_locked(tracked_key)
                        owner._evict_run_cache_entry(namespace, key)
                        self._capture_mode_publication_locked()
                        return
                    if reestimate_count >= RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                        self._entry_attempt_generations.pop(tracked_key, None)
                        self._remove_entry_locked(tracked_key)
                        owner._evict_run_cache_entry(namespace, key)
                        logger.debug(
                            "run cache entry skipped after repeated budget changes",
                            context={"namespace": namespace},
                        )
                        self._capture_mode_publication_locked()
                        return
                    reestimate_count += 1
                    continue
                self._publish_entry_locked(
                    owner,
                    namespace,
                    key,
                    signal,
                    estimated_bytes=estimated_bytes,
                    max_bytes=max_bytes,
                )
                self._capture_mode_publication_locked()
                return

    def touch(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
    ) -> None:
        """Mark one tracked entry as most recently used."""
        self.touch_many(owner, ((namespace, key),))

    def touch_many(
        self,
        owner: RunContextCacheOwner,
        entries: Iterable[tuple[RunCacheNamespace, Hashable]],
    ) -> None:
        """Mark tracked entries as recently used in caller-provided order."""
        if self._max_bytes is None:
            return
        owner_id = id(owner)
        tracked_keys = tuple((owner_id, namespace, key) for namespace, key in entries)
        if not tracked_keys:
            return
        if len(tracked_keys) == 1 and tracked_keys[0] == self._mru_key:
            return
        with self._lock:
            if self._max_bytes is None:
                return
            last_moved_key = None
            for tracked_key in tracked_keys:
                if tracked_key in self._entries:
                    self._entries.move_to_end(tracked_key)
                    last_moved_key = tracked_key
            if last_moved_key is not None:
                self._mru_key = last_moved_key

    def remove(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
    ) -> None:
        """Discard bookkeeping for an entry removed from owner storage."""
        if self._max_bytes is None and not self._entry_attempt_generations:
            return
        with self._lock:
            tracked_key = (id(owner), namespace, key)
            self._entry_attempt_generations.pop(tracked_key, None)
            if self._max_bytes is None:
                return
            self._remove_entry_accounting_locked(tracked_key)
            self._capture_mode_publication_locked()
        self._publish_modes()

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
            self._clear_owner_attempts_locked(owner_id)
            self._remove_owner_entries_locked(owner_id)
            self._owner_references.pop(owner_id, None)
            self._owners.discard(owner)
            self._capture_mode_publication_locked()
        self._publish_modes()

    def _rebuild_locked(self) -> None:
        self._entries.clear()
        self._strata.clear()
        self._calibration_history.clear()
        self._mru_key = None
        self._exact_total_bytes = 0
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
        tracked_key = (id(owner), namespace, key)
        if max_bytes == 0:
            self._remove_entry_accounting_locked(tracked_key)
            owner._evict_run_cache_entry(namespace, key)
            return
        signal = _admission_signal(namespace, key, value)
        estimated_bytes = None
        if signal.stratum is not None:
            state = self._strata.get(signal.stratum)
            if state is None or state.should_calibrate_next():
                estimated_bytes = estimate_cache_entry_size(
                    key,
                    value,
                    stop_after=max_bytes,
                )
        self._publish_entry_locked(
            owner,
            namespace,
            key,
            signal,
            estimated_bytes=estimated_bytes,
            max_bytes=max_bytes,
        )

    def _publish_entry_locked(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        signal: _AdmissionSignal,
        *,
        estimated_bytes: int | None,
        max_bytes: int,
    ) -> None:
        tracked_key = (id(owner), namespace, key)
        old_entry = self._entries.get(tracked_key)
        preserved_state = None
        if (
            old_entry is not None
            and old_entry.stratum is not None
            and old_entry.stratum == signal.stratum
        ):
            preserved_state = self._strata[old_entry.stratum]
        self._remove_entry_accounting_locked(tracked_key)
        if (
            preserved_state is not None
            and signal.stratum is not None
            and signal.stratum not in self._strata
        ):
            self._calibration_history.pop(signal.stratum, None)
            self._strata[signal.stratum] = preserved_state

        if signal.stratum is not None:
            state = self._activate_stratum_locked(signal.stratum)
            if estimated_bytes is not None and state.should_calibrate_next():
                self._publish_calibration_locked(
                    signal.stratum,
                    signal.shallow_bytes,
                    estimated_bytes,
                )

        entry = _TrackedEntry(
            owner=self._owner_reference_locked(owner),
            namespace=namespace,
            key=key,
            exact_bytes=signal.exact_bytes or 0,
            stratum=signal.stratum,
            shallow_bytes=signal.shallow_bytes if signal.stratum is not None else 0,
        )
        self._add_entry_accounting_locked(tracked_key, entry)
        if signal.stratum is not None:
            self._strata[signal.stratum].admission_count += 1
        self._mru_key = tracked_key
        modeled_entry_bytes = self._modeled_entry_bytes_locked(entry)
        self._entry_attempt_generations.pop(tracked_key, None)
        if modeled_entry_bytes > max_bytes:
            self._remove_entry_accounting_locked(tracked_key)
            owner._evict_run_cache_entry(namespace, key)
            logger.debug(
                "run cache entry skipped because it exceeds the process budget",
                context={
                    "namespace": namespace,
                    "estimated_bytes": modeled_entry_bytes,
                    "configured_bytes": max_bytes,
                },
            )
            return
        self._evict_excess_locked()

    def _modeled_entry_bytes_locked(self, entry: _TrackedEntry) -> int:
        if entry.stratum is None:
            return entry.exact_bytes
        state = self._strata[entry.stratum]
        projected_residual = (
            entry.shallow_bytes * state.sampled_residual
            + max(1, state.sampled_shallow)
            - 1
        ) // max(1, state.sampled_shallow)
        return MIN_TRACKED_ENTRY_BYTES + projected_residual

    def _add_entry_accounting_locked(
        self,
        tracked_key: TrackedKey,
        entry: _TrackedEntry,
    ) -> None:
        assert tracked_key not in self._entries
        self._entries[tracked_key] = entry
        if entry.stratum is None:
            self._exact_total_bytes += entry.exact_bytes
            self._total_bytes += entry.exact_bytes
            return

        state = self._strata[entry.stratum]
        old_modeled_bytes = state.modeled_bytes()
        state.entry_count += 1
        state.shallow_total += entry.shallow_bytes
        self._total_bytes += state.modeled_bytes() - old_modeled_bytes

    def _remove_entry_accounting_locked(
        self,
        tracked_key: TrackedKey,
    ) -> _TrackedEntry | None:
        entry = self._entries.pop(tracked_key, None)
        if entry is None:
            return None
        if tracked_key == self._mru_key:
            self._refresh_mru_key_locked()
        if entry.stratum is None:
            self._exact_total_bytes -= entry.exact_bytes
            self._total_bytes -= entry.exact_bytes
        else:
            state = self._strata[entry.stratum]
            old_modeled_bytes = state.modeled_bytes()
            state.entry_count -= 1
            state.shallow_total -= entry.shallow_bytes
            if state.entry_count == 0:
                self._strata.pop(entry.stratum)
                self._retain_calibration_locked(entry.stratum, state)
                new_modeled_bytes = 0
            else:
                new_modeled_bytes = state.modeled_bytes()
            self._total_bytes += new_modeled_bytes - old_modeled_bytes
        assert self._exact_total_bytes >= 0
        assert self._total_bytes >= 0
        return entry

    def _activate_stratum_locked(self, stratum: StratumKey) -> _StratumState:
        state = self._strata.get(stratum)
        if state is not None:
            return state
        retained = self._calibration_history.pop(stratum, None)
        if retained is None:
            state = _StratumState()
        else:
            state = _StratumState(
                admission_count=retained.admission_count,
                samples=deque(
                    retained.samples,
                    maxlen=RUN_CONTEXT_CALIBRATION_WINDOW,
                ),
            )
        self._strata[stratum] = state
        return state

    def _retain_calibration_locked(
        self,
        stratum: StratumKey,
        state: _StratumState,
    ) -> None:
        if not state.samples:
            return
        self._calibration_history[stratum] = _CalibrationMetadata(
            admission_count=state.admission_count,
            samples=tuple(state.samples),
        )
        self._calibration_history.move_to_end(stratum)
        while len(self._calibration_history) > RUN_CONTEXT_CALIBRATION_HISTORY_LIMIT:
            self._calibration_history.popitem(last=False)

    def _publish_calibration_locked(
        self,
        stratum: StratumKey,
        shallow_bytes: int,
        estimated_bytes: int,
    ) -> None:
        state = self._strata.setdefault(stratum, _StratumState())
        old_modeled_bytes = state.modeled_bytes()
        if len(state.samples) == state.samples.maxlen:
            old_shallow, old_deep = state.samples[0]
            state.sampled_shallow -= old_shallow
            state.sampled_residual -= max(0, old_deep - MIN_TRACKED_ENTRY_BYTES)
        state.samples.append((shallow_bytes, estimated_bytes))
        state.sampled_shallow += shallow_bytes
        state.sampled_residual += max(
            0,
            estimated_bytes - MIN_TRACKED_ENTRY_BYTES,
        )
        self._total_bytes += state.modeled_bytes() - old_modeled_bytes

    def _evict_excess_locked(self) -> None:
        while self._entries:
            max_bytes = self._max_bytes
            if max_bytes is None or self._total_bytes <= _eviction_target(max_bytes):
                return
            tracked_key = next(iter(self._entries))
            before = self._total_bytes
            entry = self._remove_entry_accounting_locked(tracked_key)
            assert entry is not None
            removed_bytes = before - self._total_bytes
            owner = entry.owner()
            if owner is None:
                continue
            self._entry_attempt_generations.pop(tracked_key, None)
            owner._evict_run_cache_entry(entry.namespace, entry.key)
            logger.debug(
                "run cache entry evicted by process-wide LRU budget",
                context={
                    "namespace": entry.namespace,
                    "estimated_bytes": removed_bytes,
                    "configured_bytes": max_bytes,
                },
            )

    def _remove_entry_locked(self, tracked_key: TrackedKey) -> None:
        self._remove_entry_accounting_locked(tracked_key)

    def _refresh_mru_key_locked(self) -> None:
        self._mru_key = next(reversed(self._entries), None)

    def _remove_owner_entries_locked(self, owner_id: int) -> None:
        for tracked_key in tuple(self._entries):
            if tracked_key[0] == owner_id:
                self._remove_entry_locked(tracked_key)

    def _next_admission_generation_locked(self) -> int:
        self._next_admission_generation += 1
        return self._next_admission_generation

    def _owner_lifecycle_generation_locked(self, owner_id: int) -> int:
        generation = self._owner_lifecycle_generations.get(owner_id)
        if generation is not None:
            return generation
        generation = self._next_admission_generation_locked()
        self._owner_lifecycle_generations[owner_id] = generation
        return generation

    def _clear_owner_attempts_locked(self, owner_id: int) -> None:
        for tracked_key in tuple(self._entry_attempt_generations):
            if tracked_key[0] == owner_id:
                self._entry_attempt_generations.pop(tracked_key)
        self._owner_lifecycle_generations.pop(owner_id, None)

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
            self._clear_owner_attempts_locked(owner_id)
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
                self._clear_owner_attempts_locked(owner_id)
                self._owner_references.pop(owner_id, None)
                self._capture_mode_publication_locked()
            self._publish_modes()

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


def _storage_plan_finalizer(
    candidate_type_id: int,
) -> Callable[[ReferenceType[type[object]]], None]:
    def remove_finalized_plan(candidate_reference: ReferenceType[type[object]]) -> None:
        with _storage_plan_cache_lock:
            cached = _storage_plan_cache.get(candidate_type_id)
            if cached is not None and cached[0] is candidate_reference:
                _storage_plan_cache.pop(candidate_type_id, None)

    return remove_finalized_plan


def _cached_static_storage_plan(
    candidate_type: type[object],
) -> _StaticStoragePlan | None:
    candidate_type_id = id(candidate_type)
    with _storage_plan_cache_lock:
        cached = _storage_plan_cache.get(candidate_type_id)
        if cached is not None and cached[0]() is candidate_type:
            return cached[1]
    plan = _static_storage_plan(candidate_type)
    try:
        candidate_reference = ref(
            candidate_type,
            _storage_plan_finalizer(candidate_type_id),
        )
    except TypeError:
        return plan
    with _storage_plan_cache_lock:
        cached = _storage_plan_cache.get(candidate_type_id)
        if cached is not None and cached[0]() is candidate_type:
            return cached[1]
        _storage_plan_cache[candidate_type_id] = (candidate_reference, plan)
    return plan


def _length_bucket(length: int) -> int:
    return 1 if length <= 1 else 1 << (length - 1).bit_length()


def _safe_shallow_size(value: object) -> int:
    value_type = type(value)
    try:
        if _is_exact_type(value_type, _SIZED_BUILTIN_TYPE_IDS):
            return sys.getsizeof(value)
        return object.__sizeof__(value)
    except Exception:  # noqa: BLE001 - admission must survive hostile objects.
        return MIN_TRACKED_ENTRY_BYTES


def _is_shallow_leaf_type(candidate_type: type[object]) -> bool:
    candidate_mro = _get_static_type_mro(candidate_type)
    if candidate_mro is None:
        return False
    return any(
        base_type is leaf_type
        for base_type in candidate_mro
        for leaf_type in _SHALLOW_LEAF_TYPES
    )


def _admission_signal(
    namespace: RunCacheNamespace,
    key: object,
    value: object,
) -> _AdmissionSignal:
    """Return a bounded, hook-free signal used to calibrate cache entries."""
    key_type = type(key)
    value_type = type(value)
    if _is_exact_type(key_type, _ATOMIC_LEAF_TYPE_IDS) and _is_exact_type(
        value_type, _ATOMIC_LEAF_TYPE_IDS
    ):
        exact_bytes = _safe_shallow_size(value)
        if key is not value:
            exact_bytes += _safe_shallow_size(key)
        return _AdmissionSignal(
            stratum=None,
            shallow_bytes=exact_bytes,
            exact_bytes=max(MIN_TRACKED_ENTRY_BYTES, exact_bytes),
        )

    shallow_bytes = _safe_shallow_size(value)
    if key is not value:
        shallow_bytes += _safe_shallow_size(key)
    shallow_bytes = max(1, shallow_bytes)

    builtin_families: tuple[tuple[type[object], StorageFamily], ...] = (
        (dict, "dict"),
        (list, "list"),
        (tuple, "tuple"),
        (set, "set"),
        (frozenset, "frozenset"),
    )
    for builtin_type, builtin_family in builtin_families:
        if value_type is builtin_type:
            builtin_value = cast(
                dict[object, object]
                | list[object]
                | tuple[object, ...]
                | set[object]
                | frozenset[object],
                value,
            )
            return _AdmissionSignal(
                stratum=(namespace, builtin_family, _length_bucket(len(builtin_value))),
                shallow_bytes=shallow_bytes,
                exact_bytes=None,
            )

    storage_plan = _cached_static_storage_plan(value_type)
    if storage_plan is None:
        return _AdmissionSignal((namespace, "opaque", 1), shallow_bytes, None)

    for instance_dict_descriptor in storage_plan.instance_dict_descriptors:
        try:
            instance_dict = GetSetDescriptorType.__get__(
                instance_dict_descriptor,
                value,
                value_type,
            )
        except (AttributeError, TypeError):
            continue
        if type(instance_dict) is dict:
            return _AdmissionSignal(
                stratum=(
                    namespace,
                    "instance_dict",
                    _length_bucket(len(instance_dict)),
                ),
                shallow_bytes=shallow_bytes + sys.getsizeof(instance_dict),
                exact_bytes=None,
            )

    if storage_plan.slot_descriptors:
        return _AdmissionSignal(
            stratum=(
                namespace,
                "slots",
                _length_bucket(len(storage_plan.slot_descriptors)),
            ),
            shallow_bytes=shallow_bytes,
            exact_bytes=None,
        )
    if _is_shallow_leaf_type(value_type):
        return _AdmissionSignal(
            stratum=(namespace, "shallow_leaf", 1),
            shallow_bytes=shallow_bytes,
            exact_bytes=None,
        )
    return _AdmissionSignal(
        stratum=(namespace, "opaque", 1),
        shallow_bytes=shallow_bytes,
        exact_bytes=None,
    )


def _stratified_indexes(length: int, sample_count: int) -> tuple[int, ...]:
    if sample_count >= length:
        return tuple(range(length))
    if sample_count == 1:
        return (0,)
    return tuple(
        (index * (length - 1)) // (sample_count - 1) for index in range(sample_count)
    )


def _is_exact_type(candidate_type: type[object], type_ids: frozenset[int]) -> bool:
    return id(candidate_type) in type_ids


def _mul_weight(weight: int, numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return weight
    projected = (weight * numerator + denominator - 1) // denominator
    return min(projected, sys.maxsize * RUN_CONTEXT_FIXED_POINT_SCALE)


def _sample_container_children(
    candidate: _WeightedCandidate,
) -> tuple[_WeightedCandidate, ...]:
    value = candidate.value
    value_type = type(value)
    child_ancestor_ids = candidate.ancestor_ids | frozenset((id(value),))
    if _is_exact_type(value_type, _SEQUENCE_TYPE_IDS):
        sequence = cast(list[object] | tuple[object, ...], value)
        length = len(sequence)
        if length <= RUN_CONTEXT_SIZE_SAMPLE_THRESHOLD:
            return tuple(
                _WeightedCandidate(item, candidate.weight, child_ancestor_ids)
                for item in sequence
            )
        sample_indexes = _stratified_indexes(length, RUN_CONTEXT_SIZE_SAMPLE_COUNT)
        sample_weight = _mul_weight(
            candidate.weight,
            length * 21,
            len(sample_indexes) * 20,
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
        sample_weight = _mul_weight(
            candidate.weight,
            len(mapping) * 21,
            len(sample_entries) * 20,
        )
        return tuple(
            _WeightedCandidate(item, sample_weight, child_ancestor_ids)
            for entry in sample_entries
            for item in entry
        )

    if _is_exact_type(value_type, _SET_TYPE_IDS):
        container = cast(set[object] | frozenset[object], value)
        if len(container) <= RUN_CONTEXT_SIZE_SAMPLE_THRESHOLD:
            return tuple(
                _WeightedCandidate(item, candidate.weight, child_ancestor_ids)
                for item in container
            )
        sample_items = tuple(islice(container, RUN_CONTEXT_SIZE_SAMPLE_COUNT))
        sample_weight = _mul_weight(
            candidate.weight,
            len(container) * 21,
            len(sample_items) * 20,
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
    if _is_exact_type(type(key), _ATOMIC_LEAF_TYPE_IDS) and _is_exact_type(
        type(value), _ATOMIC_LEAF_TYPE_IDS
    ):
        try:
            measured_bytes = sys.getsizeof(value)
        except Exception:  # noqa: BLE001 - preserve conservative sizing fallback.
            measured_bytes = MIN_TRACKED_ENTRY_BYTES
        if stop_after is not None and measured_bytes > stop_after:
            return stop_after + 1
        if key is not value:
            try:
                measured_bytes += sys.getsizeof(key)
            except Exception:  # noqa: BLE001 - preserve conservative sizing fallback.
                measured_bytes += MIN_TRACKED_ENTRY_BYTES
        if stop_after is not None and measured_bytes > stop_after:
            return stop_after + 1
        return max(MIN_TRACKED_ENTRY_BYTES, measured_bytes)

    saturation_limit = stop_after + 1 if stop_after is not None else sys.maxsize
    measured_bytes = 0
    seen_weights: dict[int, int] = {}
    candidates = [
        _WeightedCandidate(key, RUN_CONTEXT_FIXED_POINT_SCALE),
        _WeightedCandidate(value, RUN_CONTEXT_FIXED_POINT_SCALE),
    ]
    visited_candidates = 0
    positive_shallow_sizes: list[int] = []

    while candidates:
        if visited_candidates >= RUN_CONTEXT_CALIBRATION_CANDIDATE_LIMIT:
            remaining_weight = min(
                sum(candidate.weight for candidate in candidates),
                sys.maxsize * RUN_CONTEXT_FIXED_POINT_SCALE,
            )
            if positive_shallow_sizes:
                mean_shallow_size = (
                    sum(positive_shallow_sizes) + len(positive_shallow_sizes) - 1
                ) // len(positive_shallow_sizes)
            else:
                mean_shallow_size = MIN_TRACKED_ENTRY_BYTES
            projected_bytes = (
                mean_shallow_size * remaining_weight + RUN_CONTEXT_FIXED_POINT_SCALE - 1
            ) // RUN_CONTEXT_FIXED_POINT_SCALE
            measured_bytes = min(saturation_limit, measured_bytes + projected_bytes)
            break

        candidate = candidates.pop()
        visited_candidates += 1
        if _calibration_visit_observer is not None:
            _calibration_visit_observer(candidate.value)
        candidate_id = id(candidate.value)
        if candidate_id in candidate.ancestor_ids:
            continue
        previous_weight = seen_weights.get(candidate_id, 0)
        incremental_weight = candidate.weight - previous_weight
        if incremental_weight <= 0:
            continue
        seen_weights[candidate_id] = candidate.weight

        candidate_value = candidate.value
        candidate_type = type(candidate_value)
        try:
            if _is_exact_type(candidate_type, _SIZED_BUILTIN_TYPE_IDS):
                shallow_size = sys.getsizeof(candidate_value)
            else:
                shallow_size = object.__sizeof__(candidate_value)
        except Exception:  # noqa: BLE001 - conservative accounting must survive sizing errors.
            shallow_size = MIN_TRACKED_ENTRY_BYTES
        if shallow_size > 0:
            positive_shallow_sizes.append(shallow_size)
        incremental_bytes = (
            shallow_size * incremental_weight + RUN_CONTEXT_FIXED_POINT_SCALE - 1
        ) // RUN_CONTEXT_FIXED_POINT_SCALE
        measured_bytes = min(saturation_limit, measured_bytes + incremental_bytes)

        if measured_bytes >= saturation_limit:
            return saturation_limit

        if _is_exact_type(candidate_type, _ATOMIC_LEAF_TYPE_IDS):
            continue

        if _is_exact_type(candidate_type, _CONTAINER_TYPE_IDS):
            candidates.extend(
                _sample_container_children(
                    _WeightedCandidate(
                        candidate_value,
                        incremental_weight,
                        candidate.ancestor_ids,
                    )
                )
            )
        else:
            storage_plan = _cached_static_storage_plan(candidate_type)
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
