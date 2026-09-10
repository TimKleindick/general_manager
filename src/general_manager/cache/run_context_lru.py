"""Process-local memory accounting for calculation run caches."""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass, field, replace
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
from typing import TYPE_CHECKING, Callable, Iterator, Literal, Protocol, cast
from weakref import ReferenceType, WeakSet, ref

from django.core.exceptions import ImproperlyConfigured

from general_manager.conf import get_setting
from general_manager.logging import get_logger

if TYPE_CHECKING:
    from general_manager.cache._dependency_graph import DependencySnapshot

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
RUN_CONTEXT_SAMPLE_MARGIN_NUMERATOR = 21
RUN_CONTEXT_SAMPLE_MARGIN_DENOMINATOR = 20
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
_BUILTIN_STORAGE_FAMILIES: dict[int, StorageFamily] = {
    id(dict): "dict",
    id(list): "list",
    id(tuple): "tuple",
    id(set): "set",
    id(frozenset): "frozenset",
}
StratumKey = tuple[RunCacheNamespace, StorageFamily, int]
# Coordinator maps use a private integer handle rather than the public cache
# key.  Resolving a handle may invoke arbitrary ``Hashable`` hooks, so it
# happens before admission publication.  From then through graph/accounting
# commit, map operations hash and compare only built-in immutable values.
CanonicalKey = int
TrackedKey = tuple[int, RunCacheNamespace, CanonicalKey]
CalibrationPair = tuple[int, int]

logger = get_logger("cache.run_context_lru")


def _eviction_target(max_bytes: int) -> int:
    """Return the 95% eviction threshold that preserves a 5% reserve."""
    return max_bytes * 95 // 100


def _should_calibrate_next(admission_count: int) -> bool:
    next_admission = admission_count + 1
    return admission_count == 0 or (
        next_admission % RUN_CONTEXT_CALIBRATION_INTERVAL == 0
    )


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
    fixed_bytes: int = 0
    dependency_root: DependencySnapshot | None = None
    admission_generation: int = 0


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
        return _should_calibrate_next(self.admission_count)


@dataclass(frozen=True)
class _CalibrationMetadata:
    admission_count: int
    samples: tuple[CalibrationPair, ...]

    def should_calibrate_next(self) -> bool:
        return _should_calibrate_next(self.admission_count)


@dataclass(frozen=True)
class _StaticStoragePlan:
    instance_dict_descriptors: tuple[GetSetDescriptorType, ...]
    slot_descriptors: tuple[MemberDescriptorType, ...]


@dataclass(frozen=True)
class _AdmissionSignal:
    stratum: StratumKey | None
    shallow_bytes: int
    exact_bytes: int | None
    fixed_bytes: int = 0
    dependency_root: DependencySnapshot | None = None


@dataclass(frozen=True)
class _AdmissionAttempt:
    """One owner/key generation protected across reentrant sizing work."""

    configuration_generation: int
    owner_id: int
    owner_lifecycle_generation: int
    tracked_key: TrackedKey
    entry_attempt_generation: int


@dataclass(slots=True, eq=False)
class _CanonicalHandle:
    """One public cache key resolved before coordinator publication."""

    identity: CanonicalKey
    raw_hash: int
    key: Hashable


# A handle retains the slotted record plus an integer entry in the authoritative
# handle map and the owner/namespace/hash collision index.  This is a bounded
# logical estimate (rather than allocator RSS), applied once per live handle
# outside sampled payload calibration.
_CANONICAL_HANDLE_FIXED_BYTES = 128


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

# Logical retained-ledger records are budgeted separately from their graph
# objects.  These are bounded built-in estimates, rather than RSS probes: dict
# tables do not shrink predictably after deletions, while the cache budget must
# release accounting when the last logical record disappears.
_LEDGER_INT_BYTES = sys.getsizeof(0)
_LEDGER_MAPPING_RECORD_BYTES = sys.getsizeof({0: 0}) - sys.getsizeof({})
_LEDGER_REFERENCE_RECORD_BYTES = _LEDGER_MAPPING_RECORD_BYTES + 2 * _LEDGER_INT_BYTES
_LEDGER_NODE_RECORD_BYTES = 2 * _LEDGER_REFERENCE_RECORD_BYTES
_LEDGER_BLOCK_RECORD_BYTES = 2 * _LEDGER_REFERENCE_RECORD_BYTES
_LEDGER_DEPENDENCY_RECORD_BYTES = 2 * _LEDGER_REFERENCE_RECORD_BYTES
# Each admitted entry retains one canonical-handle record plus bounded index
# slots.  This is a logical built-in estimate, charged separately from sampled
# payload residuals so registry metadata is neither omitted nor multiplied.
# Every admission already carries ``MIN_TRACKED_ENTRY_BYTES`` before sampled
# payload residuals.  That conservative per-entry floor covers the bounded
# canonical-handle bucket/index records as well as the private tracked-entry
# record; charging it again would double the coordinator metadata for every
# sampled value.


class _StaleGraphPreparation(Exception):
    """A reentrant transition removed a root while a candidate was prepared."""


@dataclass(frozen=True)
class _PreparedGraphRetention:
    """Candidate-only graph changes ready to commit to one ledger."""

    root: DependencySnapshot | None
    epoch: int
    node_visits: tuple[DependencySnapshot, ...]
    node_sizes: dict[int, int]
    block_sizes: dict[int, int]
    dependency_sizes: dict[int, int]
    added_bytes: int
    node_iterator: Iterator[DependencySnapshot]


@dataclass(frozen=True)
class _PreparedGraphRelease:
    """A release traversal materialized before live graph maps change."""

    root: DependencySnapshot | None
    node_iterator: Iterator[DependencySnapshot]


@dataclass(frozen=True)
class _PreparedPublication:
    """All allocation-prone accounting state for one candidate admission.

    The coordinator can run arbitrary finalizers while building this object.
    Publication validates ``revision`` immediately before it mutates the graph
    or accounting maps, then uses only ready objects and native map updates.
    """

    revision: int
    old_entry: _TrackedEntry | None
    old_state: _StratumState | None
    old_metadata: _CalibrationMetadata | None
    old_history_trim_key: StratumKey | None
    incoming_state: _StratumState | None
    incoming_state_is_new: bool
    sample: CalibrationPair | None
    sample_drop: CalibrationPair | None
    old_state_after: tuple[int, int] | None
    incoming_state_final: tuple[int, int, int, int] | None
    modeled_total_delta: int
    entry: _TrackedEntry
    old_release: _PreparedGraphRelease
    transfers_root: bool


@dataclass(frozen=True)
class _PreparedEntryRemoval:
    """All allocation-prone data needed to remove one tracked entry."""

    entry: _TrackedEntry
    state: _StratumState | None
    metadata: _CalibrationMetadata | None
    history_trim_key: StratumKey | None
    release: _PreparedGraphRelease
    revision: int
    mru_after: TrackedKey | None


@dataclass
class _SharedGraphLedger:
    """Identity reference counts for dependency metadata shared by cache entries.

    The coordinator deliberately retains ids and byte counts only.  Snapshot
    objects remain owned by the entries and their parents, so this bookkeeping
    cannot itself keep a graph alive.
    """

    root_references: dict[int, int] = field(default_factory=dict)
    node_references: dict[int, int] = field(default_factory=dict)
    node_bytes: dict[int, int] = field(default_factory=dict)
    block_references: dict[int, int] = field(default_factory=dict)
    block_bytes: dict[int, int] = field(default_factory=dict)
    dependency_references: dict[int, int] = field(default_factory=dict)
    dependency_bytes: dict[int, int] = field(default_factory=dict)
    total_bytes: int = 0
    epoch: int = 0

    def retain(self, root: DependencySnapshot | None) -> int:
        """Retain an entry root and return newly charged graph bytes."""
        while True:
            prepared = self.prepare_retain(root)
            added_bytes = self.commit_retain(prepared)
            if added_bytes is not None:
                return added_bytes

    def prepare_retain(
        self,
        root: DependencySnapshot | None,
    ) -> _PreparedGraphRetention:
        """Size one candidate graph without mutating retained accounting.

        Dependency payload sizing can run finalizers.  Keep it before every
        live-map mutation so a reentrant coordinator transition can discard
        this candidate safely.  Local counts cover only this candidate graph;
        the existing ledger is never copied or scanned.
        """
        preparation_epoch = self.epoch
        if root is None:
            return _PreparedGraphRetention(
                None,
                preparation_epoch,
                (),
                {},
                {},
                {},
                0,
                iter(()),
            )

        from general_manager.cache._dependency_graph import DependencySnapshot

        root_id = id(root)
        root_record_bytes = (
            _LEDGER_REFERENCE_RECORD_BYTES
            if not self.root_references.get(root_id, 0)
            else 0
        )
        local_node_references: dict[int, int] = {}
        local_block_references: dict[int, int] = {}
        local_dependency_references: dict[int, int] = {}
        node_visits: list[DependencySnapshot] = []
        node_sizes: dict[int, int] = {}
        block_sizes: dict[int, int] = {}
        dependency_sizes: dict[int, int] = {}
        added_bytes = root_record_bytes
        pending = [root]
        while pending:
            node = pending.pop()
            node_id = id(node)
            node_count = self.node_references.get(
                node_id, 0
            ) + local_node_references.get(node_id, 0)
            local_node_references[node_id] = local_node_references.get(node_id, 0) + 1
            node_visits.append(node)
            if node_count:
                continue

            node_size = (
                _safe_shallow_size(node)
                + _safe_shallow_size(node.children)
                + _LEDGER_NODE_RECORD_BYTES
            )
            node_sizes[node_id] = node_size
            added_bytes += node_size

            block = node.block
            if block is not None:
                block_id = id(block)
                block_count = self.block_references.get(
                    block_id, 0
                ) + local_block_references.get(block_id, 0)
                local_block_references[block_id] = (
                    local_block_references.get(block_id, 0) + 1
                )
                if not block_count:
                    block_size = _safe_shallow_size(block) + _LEDGER_BLOCK_RECORD_BYTES
                    block_sizes[block_id] = block_size
                    added_bytes += block_size

                dependencies = block.dependencies
                dependencies_id = id(dependencies)
                dependency_count = self.dependency_references.get(
                    dependencies_id, 0
                ) + local_dependency_references.get(dependencies_id, 0)
                local_dependency_references[dependencies_id] = (
                    local_dependency_references.get(dependencies_id, 0) + 1
                )
                if not dependency_count:
                    dependency_size = (
                        _estimate_payload_cache_entry_size(
                            None,
                            dependencies,
                            stop_after=None,
                        )
                        + _LEDGER_DEPENDENCY_RECORD_BYTES
                    )
                    dependency_sizes[dependencies_id] = dependency_size
                    added_bytes += dependency_size

            pending.extend(
                child for child in node.children if type(child) is DependencySnapshot
            )

        visits = tuple(node_visits)
        return _PreparedGraphRetention(
            root,
            preparation_epoch,
            visits,
            node_sizes,
            block_sizes,
            dependency_sizes,
            added_bytes,
            iter(visits),
        )

    def commit_retain(self, prepared: _PreparedGraphRetention) -> int | None:
        """Commit a prepared candidate, or reject it after a reentrant change."""
        if prepared.epoch != self.epoch:
            return None
        root = prepared.root
        if root is None:
            return 0

        if prepared.epoch != self.epoch:
            return None
        root_id = id(root)
        root_count = self.root_references.get(root_id, 0)
        self.root_references[root_id] = root_count + 1
        for node in prepared.node_iterator:
            node_id = id(node)
            node_count = self.node_references.get(node_id, 0)
            self.node_references[node_id] = node_count + 1
            if node_count:
                continue
            self.node_bytes[node_id] = prepared.node_sizes[node_id]
            block = node.block
            if block is None:
                continue
            block_id = id(block)
            block_count = self.block_references.get(block_id, 0)
            self.block_references[block_id] = block_count + 1
            if not block_count:
                self.block_bytes[block_id] = prepared.block_sizes[block_id]
            dependencies_id = id(block.dependencies)
            dependency_count = self.dependency_references.get(dependencies_id, 0)
            self.dependency_references[dependencies_id] = dependency_count + 1
            if not dependency_count:
                self.dependency_bytes[dependencies_id] = prepared.dependency_sizes[
                    dependencies_id
                ]

        self.total_bytes += prepared.added_bytes
        self.epoch += 1
        return prepared.added_bytes

    def prepare_release(
        self,
        root: DependencySnapshot | None,
        *,
        retained_nodes: tuple[DependencySnapshot, ...] = (),
    ) -> _PreparedGraphRelease:
        """Materialize a release walk while no graph accounting is mutated."""
        if root is None:
            return _PreparedGraphRelease(None, iter(()))
        retained_counts: dict[int, int] = {}
        for retained in retained_nodes:
            retained_id = id(retained)
            retained_counts[retained_id] = retained_counts.get(retained_id, 0) + 1
        local_releases: dict[int, int] = {}
        pending = [root]
        visits: list[DependencySnapshot] = []
        while pending:
            node = pending.pop()
            node_id = id(node)
            node_count = (
                self.node_references.get(node_id, 0)
                + retained_counts.get(node_id, 0)
                - local_releases.get(node_id, 0)
            )
            # A node can be encountered through a diamond more than once.  It
            # is released for every incoming root/edge, but children become
            # releasable only when this occurrence drops the final reference.
            if node_count <= 0:
                raise _StaleGraphPreparation
            local_releases[node_id] = local_releases.get(node_id, 0) + 1
            visits.append(node)
            if node_count == 1:
                pending.extend(node.children)
        materialized_visits = tuple(visits)
        return _PreparedGraphRelease(root, iter(materialized_visits))

    def release(self, root: DependencySnapshot | None) -> int:
        """Release an entry root and return graph bytes no longer retained."""
        return self.commit_release(self.prepare_release(root))

    def commit_release(self, prepared: _PreparedGraphRelease) -> int:
        """Release a pre-materialized walk using only live map mutations."""
        root = prepared.root
        if root is None:
            return 0
        root_id = id(root)
        root_count = self.root_references.get(root_id, 0)
        assert root_count > 0
        if root_count == 1:
            self.root_references.pop(root_id)
        else:
            self.root_references[root_id] = root_count - 1
        root_record_bytes = 0
        if root_count == 1:
            root_record_bytes = _LEDGER_REFERENCE_RECORD_BYTES
            self.total_bytes -= root_record_bytes
        released_bytes = root_record_bytes + self._release_nodes(prepared.node_iterator)
        self.epoch += 1
        return released_bytes

    def _release_nodes(self, nodes: Iterator[DependencySnapshot]) -> int:
        released_bytes = 0
        for node in nodes:
            node_id = id(node)
            node_count = self.node_references[node_id]
            assert node_count > 0
            if node_count > 1:
                self.node_references[node_id] = node_count - 1
                continue
            self.node_references.pop(node_id)
            node_size = self.node_bytes.pop(node_id)
            self.total_bytes -= node_size
            released_bytes += node_size

            block = node.block
            if block is not None:
                block_id = id(block)
                block_count = self.block_references[block_id]
                if block_count == 1:
                    self.block_references.pop(block_id)
                    block_size = self.block_bytes.pop(block_id)
                    self.total_bytes -= block_size
                    released_bytes += block_size
                else:
                    self.block_references[block_id] = block_count - 1

                dependencies_id = id(block.dependencies)
                dependency_count = self.dependency_references[dependencies_id]
                if dependency_count == 1:
                    self.dependency_references.pop(dependencies_id)
                    dependency_size = self.dependency_bytes.pop(dependencies_id)
                    self.total_bytes -= dependency_size
                    released_bytes += dependency_size
                else:
                    self.dependency_references[dependencies_id] = dependency_count - 1
        return released_bytes


class ProcessRunContextCacheBudget:
    """Coordinate weighted LRU eviction across live calculation run caches."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._owners: WeakSet[RunContextCacheOwner] = WeakSet()
        self._owner_references: dict[int, ReferenceType[RunContextCacheOwner]] = {}
        self._entries: OrderedDict[TrackedKey, _TrackedEntry] = OrderedDict()
        # Bumps for every retained-entry or stratum transition.  Graph epochs
        # do not cover scalar admissions, so candidate publication uses this
        # revision to reject preparation invalidated by any other key.
        self._accounting_revision = 0
        self._strata: dict[StratumKey, _StratumState] = {}
        self._calibration_history: OrderedDict[StratumKey, _CalibrationMetadata] = (
            OrderedDict()
        )
        self._mru_key: TrackedKey | None = None
        self._exact_total_bytes = 0
        self._total_bytes = 0
        self._graph_ledger = _SharedGraphLedger()
        self._max_bytes: int | None = None
        self._configuration_generation = 0
        self._next_admission_generation = 0
        self._owner_lifecycle_generations: dict[int, int] = {}
        self._entry_attempt_generations: dict[TrackedKey, int] = {}
        self._canonical_keys: dict[
            int, dict[RunCacheNamespace, dict[int, list[_CanonicalHandle]]]
        ] = {}
        self._canonical_handles: dict[CanonicalKey, _CanonicalHandle] = {}
        self._next_canonical_key = 0
        # Changes whenever a public-key resolution bucket changes.  Resolving
        # a user supplied Hashable is deliberately outside publication, but a
        # hash/equality hook can still reenter and invalidate local bucket
        # references before resolution has returned.
        self._canonical_revision = 0
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
                        enrollment_generation = self._configuration_generation
                        self._track_owner_entries_locked(owner)
                        if enrollment_generation != self._configuration_generation:
                            self._rebuild_locked()
                    self._capture_mode_publication_locked(
                        new_owner=owner if is_new_owner else None
                    )
                else:
                    previous_max_bytes = self._max_bytes
                    self._max_bytes = max_bytes
                    self._configuration_generation += 1
                    self._calibration_history.clear()
                    if max_bytes is None:
                        reset_configuration_generation = self._configuration_generation
                        replacement_ledger = _SharedGraphLedger()
                        if (
                            reset_configuration_generation
                            != self._configuration_generation
                            or self._max_bytes is not None
                        ):
                            self._capture_mode_publication_locked()
                            return
                        self._entries.clear()
                        self._strata.clear()
                        self._mru_key = None
                        self._entry_attempt_generations.clear()
                        self._canonical_keys.clear()
                        self._canonical_handles.clear()
                        self._canonical_revision += 1
                        self._exact_total_bytes = 0
                        self._total_bytes = 0
                        self._graph_ledger = replacement_ledger
                        self._accounting_revision += 1
                    elif previous_max_bytes is None:
                        self._rebuild_locked()
                    elif is_new_owner:
                        enrollment_generation = self._configuration_generation
                        self._track_owner_entries_locked(owner)
                        if (
                            enrollment_generation != self._configuration_generation
                            and self._max_bytes is not None
                        ):
                            self._rebuild_locked()
                        self._evict_excess_locked()
                    else:
                        self._evict_excess_locked()
                    self._capture_mode_publication_locked()
        except BaseException as error:
            with self._lock:
                if self._max_bytes is not None:
                    self._evict_untracked_rebuild_entries_locked()
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
        *,
        configuration_retry_count: int = 0,
    ) -> None:
        if self._max_bytes is None:
            return
        with self._lock:
            max_bytes = self._max_bytes
            if max_bytes is None:
                return
            owner_id = id(owner)
            resolution_revision = self._accounting_revision
            tracked_key = self._tracked_key_locked(owner_id, namespace, key)
            if (
                resolution_revision != self._accounting_revision
                or max_bytes != self._max_bytes
            ):
                self._reconcile_stale_resolution_locked(
                    owner,
                    configuration_retry_count=configuration_retry_count,
                )
                self._forget_unused_canonical_key_locked(tracked_key)
                return
            if max_bytes == 0:
                attempt = self._begin_admission_attempt_locked(
                    owner_id,
                    tracked_key,
                    reuse_existing=True,
                )
                self._remove_entry_accounting_locked(tracked_key)
                if self._admission_attempt_key_is_current_locked(attempt):
                    self._entry_attempt_generations.pop(tracked_key, None)
                    owner._evict_run_cache_entry(namespace, key)
                    self._forget_unused_canonical_key_locked(tracked_key)
                self._capture_mode_publication_locked()
                return

            attempt = self._begin_admission_attempt_locked(owner_id, tracked_key)
            signal = _admission_signal(namespace, key, value)
            if not self._admission_attempt_is_current_locked(attempt):
                if (
                    self._admission_attempt_stale_result_locked(attempt)
                    == "retry_configuration"
                    and configuration_retry_count < RUN_CONTEXT_TRACK_MAX_REESTIMATES
                ):
                    self._track_accounting(
                        owner,
                        namespace,
                        key,
                        value,
                        configuration_retry_count=configuration_retry_count + 1,
                    )
                    return
                self._reject_zero_attempt_locked(attempt, owner, namespace, key)
                return

            requires_calibration = False
            if signal.stratum is not None:
                state = self._strata.get(
                    signal.stratum
                ) or self._calibration_history.get(signal.stratum)
                requires_calibration = state is None or state.should_calibrate_next()
            if not requires_calibration:
                published = self._publish_entry_locked(
                    owner,
                    namespace,
                    key,
                    signal,
                    tracked_key=tracked_key,
                    estimated_bytes=None,
                    max_bytes=max_bytes,
                    attempt=attempt,
                )
                if published == "retry_configuration":
                    if configuration_retry_count >= RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                        self._fail_admission_attempt_locked(
                            attempt,
                            owner,
                            namespace,
                            key,
                            allow_configuration_change=True,
                        )
                    else:
                        self._track_accounting(
                            owner,
                            namespace,
                            key,
                            value,
                            configuration_retry_count=configuration_retry_count + 1,
                        )
                self._capture_mode_publication_locked()
                return

        reestimate_count = 0
        while True:
            try:
                estimated_bytes = _estimate_admission_payload_size(
                    key,
                    value,
                    stop_after=max_bytes,
                )
            except BaseException:
                with self._lock:
                    self._fail_admission_attempt_locked(
                        attempt,
                        owner,
                        namespace,
                        key,
                        allow_configuration_change=(
                            self._max_bytes == 0
                            or self._admission_attempt_stale_result_locked(attempt)
                            == "retry_configuration"
                        ),
                    )
                    self._capture_mode_publication_locked()
                raise

            with self._lock:
                if not self._admission_attempt_key_is_current_locked(attempt):
                    return
                if attempt.configuration_generation != self._configuration_generation:
                    max_bytes = self._max_bytes
                    if max_bytes is None:
                        return
                    if max_bytes == 0:
                        self._fail_admission_attempt_locked(
                            attempt,
                            owner,
                            namespace,
                            key,
                            allow_configuration_change=True,
                        )
                        self._capture_mode_publication_locked()
                        return
                    if reestimate_count >= RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                        self._fail_admission_attempt_locked(
                            attempt,
                            owner,
                            namespace,
                            key,
                            allow_configuration_change=True,
                        )
                        logger.debug(
                            "run cache entry skipped after repeated budget changes",
                            context={"namespace": namespace},
                        )
                        self._capture_mode_publication_locked()
                        return
                    reestimate_count += 1
                    attempt = replace(
                        attempt,
                        configuration_generation=self._configuration_generation,
                    )
                    continue
                published = self._publish_entry_locked(
                    owner,
                    namespace,
                    key,
                    signal,
                    tracked_key=tracked_key,
                    estimated_bytes=estimated_bytes,
                    max_bytes=max_bytes,
                    attempt=attempt,
                )
                if published == "retry_configuration":
                    if configuration_retry_count >= RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                        self._fail_admission_attempt_locked(
                            attempt,
                            owner,
                            namespace,
                            key,
                            allow_configuration_change=True,
                        )
                    else:
                        self._track_accounting(
                            owner,
                            namespace,
                            key,
                            value,
                            configuration_retry_count=configuration_retry_count + 1,
                        )
                self._capture_mode_publication_locked()
                return

    def touch(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        *,
        mode_generation: int | None = None,
    ) -> None:
        """Mark one tracked entry as most recently used."""
        # This identity-only fast path preserves the hot MRU read without
        # hashing or comparing a public key.  Equal-but-distinct keys take the
        # locked canonical-resolution path below, where their hooks run before
        # any coordinator publication.
        mru_key = self._mru_key
        if mru_key is not None:
            mru_entry = self._entries.get(mru_key)
            if (
                mru_entry is not None
                and mru_entry.owner() is owner
                and mru_entry.namespace == namespace
                and mru_entry.key is key
            ):
                return
        self.touch_many(
            owner,
            ((namespace, key),),
            mode_generation=mode_generation,
        )

    def touch_many(
        self,
        owner: RunContextCacheOwner,
        entries: Iterable[tuple[RunCacheNamespace, Hashable]],
        *,
        mode_generation: int | None = None,
    ) -> None:
        """Mark tracked entries as recently used in caller-provided order."""
        if self._max_bytes is None:
            return
        with self._lock:
            if self._max_bytes is None:
                return
            owner_id = id(owner)
            resolution_revision = self._accounting_revision
            tracked_keys = tuple(
                self._tracked_key_locked(owner_id, namespace, key)
                for namespace, key in entries
            )
            if resolution_revision == self._accounting_revision and (
                mode_generation is None
                or (mode_generation == self._mode_generation and self._recency_enabled)
            ):
                last_moved_key = None
                for tracked_key in tracked_keys:
                    if tracked_key in self._entries:
                        self._entries.move_to_end(tracked_key)
                        last_moved_key = tracked_key
                if last_moved_key is not None:
                    self._mru_key = last_moved_key
                    self._accounting_revision += 1
            for tracked_key in tracked_keys:
                self._forget_unused_canonical_key_locked(tracked_key)
        # Key resolution can queue mode changes while the outer lock is held,
        # even when the resulting touch is stale or empty.
        self._publish_modes()

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
            removal_generation = self._next_admission_generation
            tracked_key = self._tracked_key_locked(id(owner), namespace, key)
            current_entry = self._entries.get(tracked_key)
            pending_generation = self._entry_attempt_generations.get(tracked_key, 0)
            # A different key may change global accounting during resolution.
            # Only a newer admission for this key supersedes this removal.
            if pending_generation <= removal_generation and (
                current_entry is None
                or current_entry.admission_generation <= removal_generation
            ):
                self._entry_attempt_generations.pop(tracked_key, None)
                if self._max_bytes is not None:
                    self._remove_entry_accounting_locked(tracked_key)
            self._forget_unused_canonical_key_locked(tracked_key)
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
            self._forget_canonical_owner_keys_locked(owner_id)
            self._capture_mode_publication_locked()
        self._publish_modes()

    def _rebuild_locked(self) -> None:
        # Construct the replacement before clearing live accounting.  Object
        # construction can run a finalizer that changes the configured cap and
        # performs its own rebuild; the stale outer reset must not overwrite
        # that new ledger.
        rebuild_configuration_generation = self._configuration_generation
        replacement_ledger = _SharedGraphLedger()
        if (
            rebuild_configuration_generation != self._configuration_generation
            or self._max_bytes is None
        ):
            return
        self._entries.clear()
        self._strata.clear()
        self._calibration_history.clear()
        self._mru_key = None
        self._exact_total_bytes = 0
        self._total_bytes = 0
        self._graph_ledger = replacement_ledger
        self._accounting_revision += 1
        rebuild_ledger = self._graph_ledger
        rebuild_reestimate_count = 0
        while True:
            rebuild_configuration_generation = self._configuration_generation
            for owner in tuple(self._owners):
                if (
                    self._graph_ledger is not rebuild_ledger
                    or self._max_bytes is None
                    or rebuild_configuration_generation
                    != self._configuration_generation
                ):
                    break
                self._track_owner_entries_locked(owner)
            if self._graph_ledger is not rebuild_ledger or self._max_bytes is None:
                return
            if rebuild_configuration_generation == self._configuration_generation:
                return
            if rebuild_reestimate_count >= RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                self._evict_untracked_rebuild_entries_locked()
                return
            rebuild_reestimate_count += 1

    def _evict_untracked_rebuild_entries_locked(self) -> None:
        """Reject entries left without accounting after repeated cap changes."""
        for owner in tuple(self._owners):
            self._reject_untracked_owner_entries_locked(owner)

    def _owner_identity_is_current_locked(
        self,
        owner_id: int,
        owner: RunContextCacheOwner,
        owner_reference: ReferenceType[RunContextCacheOwner],
        owner_lifecycle_generation: int,
    ) -> bool:
        """Return whether a fresh owner snapshot can still change storage."""
        return (
            self._max_bytes is not None
            and self._owner_references.get(owner_id) is owner_reference
            and owner_reference() is owner
            and owner_lifecycle_generation
            == self._owner_lifecycle_generations.get(owner_id, 0)
        )

    def _reject_untracked_owner_entries_locked(
        self,
        owner: RunContextCacheOwner,
    ) -> None:
        """Evict current storage entries that never received accounting.

        This is the bounded terminal path after configuration or public-key
        reentry invalidates every admission snapshot.  It only acts while the
        same registered owner and lifecycle still own the fresh storage.
        """
        owner_id = id(owner)
        owner_reference = self._owner_references.get(owner_id)
        if owner_reference is None:
            return
        owner_lifecycle_generation = self._owner_lifecycle_generation_locked(owner_id)
        if not self._owner_identity_is_current_locked(
            owner_id,
            owner,
            owner_reference,
            owner_lifecycle_generation,
        ):
            return
        entries = tuple(owner._iter_run_cache_entries())
        for namespace, key, _value in entries:
            if not self._owner_identity_is_current_locked(
                owner_id,
                owner,
                owner_reference,
                owner_lifecycle_generation,
            ):
                return
            tracked_key = self._tracked_key_locked(owner_id, namespace, key)
            try:
                if not self._owner_identity_is_current_locked(
                    owner_id,
                    owner,
                    owner_reference,
                    owner_lifecycle_generation,
                ):
                    return
                if (
                    tracked_key not in self._entries
                    and tracked_key not in self._entry_attempt_generations
                ):
                    canonical_handle = self._canonical_handles.get(tracked_key[2])
                    if canonical_handle is None:
                        continue
                    # An equal transient snapshot key may run user hooks in a
                    # framework owner's storage lookup.  The canonical handle
                    # retains the key originally admitted to that storage.
                    owner._evict_run_cache_entry(namespace, canonical_handle.key)
            finally:
                # Resolution can have created a temporary canonical handle
                # before a clear invalidated this terminal snapshot.
                self._forget_unused_canonical_key_locked(tracked_key)

    def _reconcile_stale_resolution_locked(
        self,
        owner: RunContextCacheOwner,
        *,
        configuration_retry_count: int,
    ) -> None:
        """Rebuild an owner from fresh storage after raw-key hooks reenter.

        A public ``hash`` or ``==`` hook runs before an admission token exists.
        If it changes a peer or the finite cap, returning would leave the
        caller's already-stored value outside accounting.  Re-snapshotting the
        owner uses canonical ids and therefore admits only the value still in
        storage; it cannot publish the stale argument passed to ``track``.
        """
        owner_id = id(owner)
        owner_reference = self._owner_references.get(owner_id)
        if owner_reference is None:
            return
        owner_lifecycle_generation = self._owner_lifecycle_generation_locked(owner_id)
        if not self._owner_identity_is_current_locked(
            owner_id,
            owner,
            owner_reference,
            owner_lifecycle_generation,
        ):
            return
        if configuration_retry_count < RUN_CONTEXT_TRACK_MAX_REESTIMATES:
            self._track_owner_entries_locked(owner)
            return
        # A hostile key hook can repeatedly invalidate every fresh snapshot.
        # Reject only entries that still have no accounting record; current
        # tokens and reentrant replacements remain protected by this helper.
        self._reject_untracked_owner_entries_locked(owner)

    def _track_owner_entries_locked(
        self,
        owner: RunContextCacheOwner,
        *,
        snapshot_retry_count: int = 0,
    ) -> None:
        """Admit a current owner snapshot without retaining stale key handles."""
        owner_id = id(owner)
        owner_reference = self._owner_references.get(owner_id)
        if owner_reference is None or owner_reference() is not owner:
            return
        configuration_generation = self._configuration_generation
        snapshot_revision = self._accounting_revision
        owner_lifecycle_generation = self._owner_lifecycle_generation_locked(owner_id)
        provisional_keys: set[TrackedKey] = set()
        current_key: TrackedKey | None = None

        def snapshot_is_current() -> bool:
            return (
                configuration_generation == self._configuration_generation
                and owner_lifecycle_generation
                == self._owner_lifecycle_generations.get(owner_id, 0)
                and snapshot_revision == self._accounting_revision
                and self._owner_references.get(owner_id) is owner_reference
                and owner_reference() is owner
            )

        def owner_lifecycle_is_current() -> bool:
            return (
                configuration_generation == self._configuration_generation
                and owner_lifecycle_generation
                == self._owner_lifecycle_generations.get(owner_id, 0)
                and self._owner_references.get(owner_id) is owner_reference
                and owner_reference() is owner
            )

        def owner_identity_is_current() -> bool:
            return (
                self._owner_references.get(owner_id) is owner_reference
                and owner_reference() is owner
                and owner_lifecycle_generation
                == self._owner_lifecycle_generations.get(owner_id, 0)
            )

        def retry_current_snapshot() -> None:
            if (
                snapshot_retry_count < RUN_CONTEXT_TRACK_MAX_REESTIMATES
                and owner_identity_is_current()
            ):
                self._track_owner_entries_locked(
                    owner,
                    snapshot_retry_count=snapshot_retry_count + 1,
                )
            elif owner_identity_is_current():
                # Retry exhaustion must not strand a current resident value
                # outside the finite budget.  The maximum reconciliation path
                # takes one fresh snapshot and cannot recurse further.
                self._reconcile_stale_resolution_locked(
                    owner,
                    configuration_retry_count=RUN_CONTEXT_TRACK_MAX_REESTIMATES,
                )

        try:
            entries = tuple(owner._iter_run_cache_entries())
            if not snapshot_is_current() or not entries:
                retry_current_snapshot()
                return
            snapshot_attempts: dict[TrackedKey, int] = {}
            captured_entries: list[tuple[TrackedKey, object]] = []
            for namespace, key, value in entries:
                current_key = self._tracked_key_locked(owner_id, namespace, key)
                provisional_keys.add(current_key)
                if not snapshot_is_current():
                    retry_current_snapshot()
                    return
                snapshot_attempts[current_key] = (
                    self._next_admission_generation_locked()
                )
                captured_entries.append((current_key, value))

            current_entries = tuple(owner._iter_run_cache_entries())
            current_snapshot: list[tuple[TrackedKey, object]] = []
            for namespace, key, value in current_entries:
                current_key = self._tracked_key_locked(owner_id, namespace, key)
                provisional_keys.add(current_key)
                if not snapshot_is_current():
                    retry_current_snapshot()
                    return
                current_snapshot.append((current_key, value))
            if len(captured_entries) != len(current_snapshot) or any(
                captured_key != snapshot_key or captured_value is not snapshot_value
                for (captured_key, captured_value), (
                    snapshot_key,
                    snapshot_value,
                ) in zip(captured_entries, current_snapshot, strict=True)
            ):
                retry_current_snapshot()
                return
            # ``any(zip(...))`` may allocate after the last per-key check.
            if not snapshot_is_current():
                retry_current_snapshot()
                return
            self._entry_attempt_generations.update(snapshot_attempts)
            try:
                for (tracked_key, value), (namespace, key, _snapshot_value) in zip(
                    captured_entries, entries, strict=True
                ):
                    if not owner_lifecycle_is_current():
                        return
                    if snapshot_attempts[
                        tracked_key
                    ] != self._entry_attempt_generations.get(tracked_key, 0):
                        continue
                    self._track_value_locked(owner, namespace, key, value)
            except BaseException:
                # A rebuild snapshot is all-or-nothing from the owner's point
                # of view: do not leave later resident values unaccounted.
                for (tracked_key, _value), (namespace, key, _snapshot_value) in zip(
                    captured_entries, entries, strict=True
                ):
                    if (
                        snapshot_attempts[tracked_key]
                        == self._entry_attempt_generations.get(tracked_key, 0)
                        and tracked_key not in self._entries
                    ):
                        self._entry_attempt_generations.pop(tracked_key, None)
                        owner._evict_run_cache_entry(namespace, key)
                raise
            finally:
                for tracked_key, attempt_generation in snapshot_attempts.items():
                    if attempt_generation == self._entry_attempt_generations.get(
                        tracked_key, 0
                    ):
                        self._entry_attempt_generations.pop(tracked_key, None)
        finally:
            # Resolution can allocate a new canonical handle before a clear or
            # owner replacement invalidates this snapshot.  Release every
            # provisional id on all exits; live entries, pending attempts, and
            # reentrant replacements make this a no-op for their handle.
            if current_key is not None:
                self._forget_unused_canonical_key_locked(current_key)
            for tracked_key in provisional_keys:
                if tracked_key != current_key:
                    self._forget_unused_canonical_key_locked(tracked_key)

    def _track_value_locked(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        value: object,
        *,
        configuration_retry_count: int = 0,
    ) -> None:
        """Estimate rebuild entries while locked; normal track() admission is unlocked.

        Only configuration rebuilds estimate while holding the coordinator lock.
        """
        max_bytes = self._max_bytes
        if max_bytes is None:
            return
        resolution_revision = self._accounting_revision
        tracked_key = self._tracked_key_locked(id(owner), namespace, key)
        if (
            resolution_revision != self._accounting_revision
            or max_bytes != self._max_bytes
        ):
            self._reconcile_stale_resolution_locked(
                owner,
                configuration_retry_count=configuration_retry_count,
            )
            self._forget_unused_canonical_key_locked(tracked_key)
            return
        if max_bytes == 0:
            attempt = self._begin_admission_attempt_locked(
                id(owner), tracked_key, reuse_existing=True
            )
            self._remove_entry_accounting_locked(tracked_key)
            if self._admission_attempt_key_is_current_locked(attempt):
                self._entry_attempt_generations.pop(tracked_key, None)
                owner._evict_run_cache_entry(namespace, key)
                self._forget_unused_canonical_key_locked(tracked_key)
            return
        attempt = self._begin_admission_attempt_locked(
            id(owner),
            tracked_key,
            reuse_existing=True,
        )
        signal = _admission_signal(namespace, key, value)
        if not self._admission_attempt_is_current_locked(attempt):
            if (
                self._admission_attempt_stale_result_locked(attempt)
                == "retry_configuration"
                and configuration_retry_count < RUN_CONTEXT_TRACK_MAX_REESTIMATES
            ):
                self._track_value_locked(
                    owner,
                    namespace,
                    key,
                    value,
                    configuration_retry_count=configuration_retry_count + 1,
                )
                return
            self._reject_zero_attempt_locked(attempt, owner, namespace, key)
            return
        estimated_bytes = None
        if signal.stratum is not None:
            state = self._strata.get(signal.stratum)
            if state is None or state.should_calibrate_next():
                try:
                    estimated_bytes = _estimate_admission_payload_size(
                        key,
                        value,
                        stop_after=max_bytes,
                    )
                except BaseException:
                    self._fail_admission_attempt_locked(
                        attempt,
                        owner,
                        namespace,
                        key,
                        allow_configuration_change=(
                            self._max_bytes == 0
                            or self._admission_attempt_stale_result_locked(attempt)
                            == "retry_configuration"
                        ),
                    )
                    raise
                if not self._admission_attempt_is_current_locked(attempt):
                    if (
                        self._admission_attempt_stale_result_locked(attempt)
                        == "retry_configuration"
                    ):
                        if (
                            configuration_retry_count
                            >= RUN_CONTEXT_TRACK_MAX_REESTIMATES
                        ):
                            self._fail_admission_attempt_locked(
                                attempt,
                                owner,
                                namespace,
                                key,
                                allow_configuration_change=True,
                            )
                        else:
                            self._track_value_locked(
                                owner,
                                namespace,
                                key,
                                value,
                                configuration_retry_count=configuration_retry_count + 1,
                            )
                    return
        published = self._publish_entry_locked(
            owner,
            namespace,
            key,
            signal,
            tracked_key=tracked_key,
            estimated_bytes=estimated_bytes,
            max_bytes=max_bytes,
            attempt=attempt,
        )
        if published == "retry_configuration":
            if configuration_retry_count >= RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                self._fail_admission_attempt_locked(
                    attempt,
                    owner,
                    namespace,
                    key,
                    allow_configuration_change=True,
                )
                return
            self._track_value_locked(
                owner,
                namespace,
                key,
                value,
                configuration_retry_count=configuration_retry_count + 1,
            )

    def _publish_entry_locked(
        self,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        signal: _AdmissionSignal,
        *,
        tracked_key: TrackedKey,
        estimated_bytes: int | None,
        max_bytes: int,
        attempt: _AdmissionAttempt,
    ) -> Literal["published", "stale", "retry_configuration"]:
        """Publish one admission after safely retaining its graph candidate."""
        entry = _TrackedEntry(
            owner=self._owner_reference_locked(owner),
            namespace=namespace,
            key=key,
            exact_bytes=signal.exact_bytes or 0,
            stratum=signal.stratum,
            shallow_bytes=signal.shallow_bytes if signal.stratum is not None else 0,
            fixed_bytes=signal.fixed_bytes + _CANONICAL_HANDLE_FIXED_BYTES,
            dependency_root=signal.dependency_root,
            admission_generation=attempt.entry_attempt_generation,
        )
        graph_reestimate_count = 0
        while True:
            if not self._admission_attempt_is_current_locked(attempt):
                self._reject_zero_attempt_locked(attempt, owner, namespace, key)
                return self._admission_attempt_stale_result_locked(attempt)
            try:
                publication = self._prepare_publication_locked(
                    tracked_key,
                    entry,
                    signal,
                    estimated_bytes,
                )
            except _StaleGraphPreparation:
                if self._admission_attempt_is_current_locked(attempt):
                    if graph_reestimate_count < RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                        graph_reestimate_count += 1
                        continue
                    self._fail_admission_attempt_locked(attempt, owner, namespace, key)
                else:
                    self._reject_zero_attempt_locked(attempt, owner, namespace, key)
                return self._admission_attempt_stale_result_locked(attempt)
            if not self._publication_is_current_locked(publication, tracked_key):
                if self._admission_attempt_is_current_locked(attempt):
                    if graph_reestimate_count >= RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                        self._fail_admission_attempt_locked(
                            attempt, owner, namespace, key
                        )
                        return "stale"
                    graph_reestimate_count += 1
                    continue
                self._reject_zero_attempt_locked(attempt, owner, namespace, key)
                return self._admission_attempt_stale_result_locked(attempt)
            ledger = self._graph_ledger
            try:
                prepared = (
                    ledger.prepare_retain(signal.dependency_root)
                    if not publication.transfers_root
                    else None
                )
                if prepared is not None:
                    publication = replace(
                        publication,
                        old_release=ledger.prepare_release(
                            (
                                publication.old_entry.dependency_root
                                if publication.old_entry is not None
                                else None
                            ),
                            retained_nodes=prepared.node_visits,
                        ),
                    )
            except _StaleGraphPreparation:
                if self._admission_attempt_is_current_locked(attempt):
                    if graph_reestimate_count < RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                        graph_reestimate_count += 1
                        continue
                    self._fail_admission_attempt_locked(attempt, owner, namespace, key)
                else:
                    self._reject_zero_attempt_locked(attempt, owner, namespace, key)
                return self._admission_attempt_stale_result_locked(attempt)
            except BaseException:
                self._fail_admission_attempt_locked(
                    attempt,
                    owner,
                    namespace,
                    key,
                    allow_configuration_change=(
                        self._max_bytes == 0
                        or self._admission_attempt_stale_result_locked(attempt)
                        == "retry_configuration"
                    ),
                )
                raise
            modeled_entry_bytes = self._prepared_modeled_entry_bytes(publication)
            candidate_graph_bytes = 0 if prepared is None else prepared.added_bytes
            if not self._admission_attempt_is_current_locked(
                attempt
            ) or not self._publication_is_current_locked(publication, tracked_key):
                if self._admission_attempt_is_current_locked(attempt):
                    if graph_reestimate_count >= RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                        self._fail_admission_attempt_locked(
                            attempt, owner, namespace, key
                        )
                        return "stale"
                    graph_reestimate_count += 1
                    continue
                self._reject_zero_attempt_locked(attempt, owner, namespace, key)
                return self._admission_attempt_stale_result_locked(attempt)
            if modeled_entry_bytes + candidate_graph_bytes > max_bytes:
                # Reject before live graph mutation.  If this replaces an
                # existing storage value, remove only the still-current old
                # accounting record and never evict a reentrant replacement.
                self._remove_entry_accounting_locked(tracked_key)
                if self._admission_attempt_key_is_current_locked(attempt):
                    self._entry_attempt_generations.pop(tracked_key, None)
                    owner._evict_run_cache_entry(namespace, key)
                    self._forget_unused_canonical_key_locked(tracked_key)
                logger.debug(
                    "run cache entry skipped because it exceeds the process budget",
                    context={
                        "namespace": namespace,
                        "estimated_bytes": modeled_entry_bytes,
                        "configured_bytes": max_bytes,
                    },
                )
                return "published"
            graph_bytes_added = (
                0 if prepared is None else ledger.commit_retain(prepared)
            )
            if graph_bytes_added is None:
                # A different key admitted while dependency sizing ran.  Its
                # retained graph may make this candidate cheaper, so retry
                # against current membership without disturbing that entry.
                if graph_reestimate_count >= RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                    self._fail_admission_attempt_locked(
                        attempt,
                        owner,
                        namespace,
                        key,
                    )
                    logger.debug(
                        "run cache entry skipped after repeated graph changes",
                        context={"namespace": namespace},
                    )
                    return "stale"
                graph_reestimate_count += 1
                continue
            expected_epoch = (
                ledger.epoch
                if prepared is None
                else prepared.epoch + (1 if signal.dependency_root is not None else 0)
            )
            if (
                not self._admission_attempt_is_current_locked(attempt)
                or not self._publication_is_current_locked(publication, tracked_key)
                or ledger.epoch != expected_epoch
            ):
                # If a finalizer replaced the coordinator ledger, the old
                # ledger is already unreachable.  Otherwise drop only this
                # candidate's extra root reference; any reentrant replacement
                # keeps its own reference intact.
                if ledger is self._graph_ledger and not publication.transfers_root:
                    ledger.release(signal.dependency_root)
                self._reject_zero_attempt_locked(attempt, owner, namespace, key)
                stale_result = self._admission_attempt_stale_result_locked(attempt)
                if stale_result == "retry_configuration":
                    return stale_result
                if (
                    ledger is self._graph_ledger
                    and self._admission_attempt_is_current_locked(attempt)
                    and ledger.epoch != expected_epoch
                ):
                    if graph_reestimate_count >= RUN_CONTEXT_TRACK_MAX_REESTIMATES:
                        self._fail_admission_attempt_locked(
                            attempt,
                            owner,
                            namespace,
                            key,
                        )
                        return "stale"
                    graph_reestimate_count += 1
                    continue
                return stale_result
            break

        # No object construction or user callbacks occur below this point.
        # The graph root is retained before the old root is released; all
        # stratum and calibration objects were built in ``publication``.
        self._total_bytes += graph_bytes_added
        self._apply_prepared_entry_removal_locked(tracked_key, publication)
        self._apply_prepared_entry_addition_locked(tracked_key, publication)
        self._total_bytes += publication.modeled_total_delta
        if not publication.transfers_root:
            graph_bytes_released = ledger.commit_release(publication.old_release)
            self._total_bytes -= graph_bytes_released
        self._accounting_revision += 1
        self._entry_attempt_generations.pop(tracked_key, None)
        self._evict_excess_locked()
        return "published"

    def _prepare_publication_locked(
        self,
        tracked_key: TrackedKey,
        entry: _TrackedEntry,
        signal: _AdmissionSignal,
        estimated_bytes: int | None,
    ) -> _PreparedPublication:
        """Allocate all stratum and release state before graph publication."""
        preparation_revision = self._accounting_revision
        old_entry = self._entries.get(tracked_key)
        old_state = (
            self._strata.get(old_entry.stratum)
            if old_entry is not None and old_entry.stratum is not None
            else None
        )
        incoming_state: _StratumState | None = None
        incoming_state_is_new = False
        if signal.stratum is not None:
            incoming_state = self._strata.get(signal.stratum)
            if incoming_state is None:
                retained = self._calibration_history.get(signal.stratum)
                incoming_state_is_new = True
                if retained is None:
                    incoming_state = _StratumState()
                else:
                    incoming_state = _StratumState(
                        admission_count=retained.admission_count,
                        samples=deque(
                            retained.samples,
                            maxlen=RUN_CONTEXT_CALIBRATION_WINDOW,
                        ),
                    )

        old_metadata = None
        old_history_trim_key = None
        if (
            old_entry is not None
            and old_state is not None
            and old_entry.stratum != signal.stratum
            and old_state.entry_count == 1
            and old_state.samples
        ):
            old_metadata = _CalibrationMetadata(
                admission_count=old_state.admission_count,
                samples=tuple(old_state.samples),
            )
            if (
                old_entry.stratum not in self._calibration_history
                and len(self._calibration_history)
                >= RUN_CONTEXT_CALIBRATION_HISTORY_LIMIT
            ):
                history_iterator = iter(self._calibration_history)
                if preparation_revision != self._accounting_revision:
                    raise _StaleGraphPreparation
                try:
                    old_history_trim_key = next(history_iterator)
                except RuntimeError:
                    if preparation_revision != self._accounting_revision:
                        raise _StaleGraphPreparation from None
                    raise
                if preparation_revision != self._accounting_revision:
                    raise _StaleGraphPreparation
        sample = (
            (signal.shallow_bytes, estimated_bytes)
            if (
                estimated_bytes is not None
                and incoming_state is not None
                and incoming_state.should_calibrate_next()
            )
            else None
        )
        sample_drop = None
        if (
            sample is not None
            and incoming_state is not None
            and len(incoming_state.samples) == incoming_state.samples.maxlen
        ):
            sample_drop = incoming_state.samples[0]

        # Simulate the stratum transitions while preparation is still allowed
        # to allocate or collect.  Commit only assigns these precomputed
        # integer fields; in particular it never calls ``max``/``modeled``
        # after an entry or graph map has changed.
        def modeled_bytes(
            entry_count: int,
            shallow_total: int,
            sampled_shallow: int,
            sampled_residual: int,
        ) -> int:
            if entry_count == 0:
                return 0
            denominator = sampled_shallow if sampled_shallow > 0 else 1
            return (
                entry_count * MIN_TRACKED_ENTRY_BYTES
                + (shallow_total * sampled_residual + denominator - 1) // denominator
            )

        modeled_total_delta = 0
        old_state_after: tuple[int, int] | None = None
        # These are the values immediately before an incoming addition.  When
        # a refresh stays in one stratum, removal must feed that same state.
        incoming_count: int | None = None
        incoming_shallow_total: int | None = None
        incoming_sampled_shallow: int | None = None
        incoming_sampled_residual: int | None = None
        incoming_admission_count: int | None = None
        if old_entry is not None:
            if old_entry.stratum is None:
                modeled_total_delta -= old_entry.exact_bytes + old_entry.fixed_bytes
            else:
                assert old_state is not None
                old_modeled = modeled_bytes(
                    old_state.entry_count,
                    old_state.shallow_total,
                    old_state.sampled_shallow,
                    old_state.sampled_residual,
                )
                removed_count = old_state.entry_count - 1
                removed_shallow_total = (
                    old_state.shallow_total - old_entry.shallow_bytes
                )
                old_state_after = (removed_count, removed_shallow_total)
                removed_modeled = (
                    0
                    if (removed_count == 0 and old_entry.stratum != signal.stratum)
                    else modeled_bytes(
                        removed_count,
                        removed_shallow_total,
                        old_state.sampled_shallow,
                        old_state.sampled_residual,
                    )
                )
                modeled_total_delta += (
                    removed_modeled - old_modeled - old_entry.fixed_bytes
                )
                if old_state is incoming_state:
                    incoming_count = removed_count
                    incoming_shallow_total = removed_shallow_total
                    incoming_sampled_shallow = old_state.sampled_shallow
                    incoming_sampled_residual = old_state.sampled_residual
                    incoming_admission_count = old_state.admission_count

        incoming_state_final: tuple[int, int, int, int] | None = None
        if incoming_state is not None:
            if incoming_count is None:
                incoming_count = incoming_state.entry_count
                incoming_shallow_total = incoming_state.shallow_total
                incoming_sampled_shallow = incoming_state.sampled_shallow
                incoming_sampled_residual = incoming_state.sampled_residual
                incoming_admission_count = incoming_state.admission_count
            assert incoming_shallow_total is not None
            assert incoming_sampled_shallow is not None
            assert incoming_sampled_residual is not None
            assert incoming_admission_count is not None
            before_add = modeled_bytes(
                incoming_count,
                incoming_shallow_total,
                incoming_sampled_shallow,
                incoming_sampled_residual,
            )
            if sample is not None:
                sample_shallow, sample_deep = sample
                if sample_drop is not None:
                    dropped_shallow, dropped_deep = sample_drop
                    incoming_sampled_shallow -= dropped_shallow
                    incoming_sampled_residual -= max(
                        0, dropped_deep - MIN_TRACKED_ENTRY_BYTES
                    )
                incoming_sampled_shallow += sample_shallow
                incoming_sampled_residual += max(
                    0, sample_deep - MIN_TRACKED_ENTRY_BYTES
                )
            incoming_count += 1
            incoming_shallow_total += entry.shallow_bytes
            incoming_admission_count += 1
            after_add = modeled_bytes(
                incoming_count,
                incoming_shallow_total,
                incoming_sampled_shallow,
                incoming_sampled_residual,
            )
            modeled_total_delta += after_add - before_add + entry.fixed_bytes
            incoming_state_final = (
                incoming_count,
                incoming_shallow_total,
                incoming_sampled_shallow,
                incoming_sampled_residual,
            )
        else:
            modeled_total_delta += entry.exact_bytes + entry.fixed_bytes
        transfers_root = (
            old_entry is not None
            and old_entry.dependency_root is signal.dependency_root
        )
        return _PreparedPublication(
            revision=preparation_revision,
            old_entry=old_entry,
            old_state=old_state,
            old_metadata=old_metadata,
            old_history_trim_key=old_history_trim_key,
            incoming_state=incoming_state,
            incoming_state_is_new=incoming_state_is_new,
            sample=sample,
            sample_drop=sample_drop,
            old_state_after=old_state_after,
            incoming_state_final=incoming_state_final,
            modeled_total_delta=modeled_total_delta,
            entry=entry,
            old_release=_PreparedGraphRelease(None, iter(())),
            transfers_root=transfers_root,
        )

    def _publication_is_current_locked(
        self,
        publication: _PreparedPublication,
        tracked_key: TrackedKey,
    ) -> bool:
        return (
            publication.revision == self._accounting_revision
            and self._entries.get(tracked_key) is publication.old_entry
            and (
                publication.old_entry is None
                or publication.old_entry.stratum is None
                or self._strata.get(publication.old_entry.stratum)
                is publication.old_state
            )
        )

    def _prepared_modeled_entry_bytes(
        self,
        publication: _PreparedPublication,
    ) -> int:
        entry = publication.entry
        if entry.stratum is None:
            return entry.exact_bytes + entry.fixed_bytes
        state = publication.incoming_state
        assert state is not None
        shallow_total = state.sampled_shallow
        residual_total = state.sampled_residual
        if publication.sample is not None:
            shallow_bytes, deep_bytes = publication.sample
            if len(state.samples) == state.samples.maxlen:
                old_shallow, old_deep = state.samples[0]
                shallow_total -= old_shallow
                residual_total -= max(0, old_deep - MIN_TRACKED_ENTRY_BYTES)
            shallow_total += shallow_bytes
            residual_total += max(0, deep_bytes - MIN_TRACKED_ENTRY_BYTES)
        return (
            MIN_TRACKED_ENTRY_BYTES
            + (entry.shallow_bytes * residual_total + max(1, shallow_total) - 1)
            // max(1, shallow_total)
            + entry.fixed_bytes
        )

    def _apply_prepared_entry_removal_locked(
        self,
        tracked_key: TrackedKey,
        publication: _PreparedPublication,
    ) -> None:
        entry = publication.old_entry
        if entry is None:
            return
        self._entries.pop(tracked_key)
        # The incoming entry becomes MRU below, so replacement never needs a
        # transient reversed-OrderedDict iterator here.
        if entry.stratum is None:
            self._exact_total_bytes -= entry.exact_bytes
            return
        state = publication.old_state
        assert state is not None
        old_state_after = publication.old_state_after
        assert old_state_after is not None
        state.entry_count, state.shallow_total = old_state_after
        if state.entry_count == 0 and entry.stratum != publication.entry.stratum:
            self._strata.pop(entry.stratum)
            if publication.old_metadata is not None:
                self._calibration_history[entry.stratum] = publication.old_metadata
                self._calibration_history.move_to_end(entry.stratum)
                if publication.old_history_trim_key is not None:
                    self._calibration_history.pop(
                        publication.old_history_trim_key, None
                    )

    def _apply_prepared_entry_addition_locked(
        self,
        tracked_key: TrackedKey,
        publication: _PreparedPublication,
    ) -> None:
        entry = publication.entry
        if entry.stratum is None:
            self._entries[tracked_key] = entry
            self._exact_total_bytes += entry.exact_bytes
        else:
            state = publication.incoming_state
            assert state is not None
            if publication.incoming_state_is_new:
                self._calibration_history.pop(entry.stratum, None)
                self._strata[entry.stratum] = state
            if publication.sample is not None:
                state.samples.append(publication.sample)
            final_state = publication.incoming_state_final
            assert final_state is not None
            (
                state.entry_count,
                state.shallow_total,
                state.sampled_shallow,
                state.sampled_residual,
            ) = final_state
            state.admission_count += 1
            self._entries[tracked_key] = entry
        self._mru_key = tracked_key

    def _modeled_entry_bytes_locked(self, entry: _TrackedEntry) -> int:
        if entry.stratum is None:
            return entry.exact_bytes + entry.fixed_bytes
        state = self._strata[entry.stratum]
        projected_residual = (
            entry.shallow_bytes * state.sampled_residual
            + max(1, state.sampled_shallow)
            - 1
        ) // max(1, state.sampled_shallow)
        return MIN_TRACKED_ENTRY_BYTES + projected_residual + entry.fixed_bytes

    def _add_entry_accounting_locked(
        self,
        tracked_key: TrackedKey,
        entry: _TrackedEntry,
    ) -> None:
        assert tracked_key not in self._entries
        self._entries[tracked_key] = entry
        if entry.stratum is None:
            self._exact_total_bytes += entry.exact_bytes
            self._total_bytes += entry.exact_bytes + entry.fixed_bytes
            return

        state = self._strata[entry.stratum]
        old_modeled_bytes = state.modeled_bytes()
        state.entry_count += 1
        state.shallow_total += entry.shallow_bytes
        self._total_bytes += (
            state.modeled_bytes() - old_modeled_bytes + entry.fixed_bytes
        )

    def _remove_entry_accounting_locked(
        self,
        tracked_key: TrackedKey,
    ) -> _TrackedEntry | None:
        entry = self._entries.get(tracked_key)
        if entry is None:
            return None
        # The release traversal and optional retained calibration metadata may
        # collect an unrelated cycle, so build them while this entry remains
        # fully live.
        while True:
            try:
                prepared = self._prepare_entry_removal_locked(tracked_key, entry)
            except _StaleGraphPreparation:
                # An unrelated admission or recency change can invalidate
                # preparation while this entry still needs to be removed.
                if self._entries.get(tracked_key) is entry:
                    continue
                return None
            if self._entries.get(tracked_key) is not entry:
                # A finalizer published a newer generation for this key.  A
                # stale eviction/clear must never remove that replacement.
                return None
            if prepared.revision == self._accounting_revision:
                break
        self._entries.pop(tracked_key)
        if tracked_key == self._mru_key:
            self._mru_key = prepared.mru_after
        if entry.stratum is None:
            self._exact_total_bytes -= entry.exact_bytes
            self._total_bytes -= entry.exact_bytes + entry.fixed_bytes
        else:
            state = prepared.state
            assert state is not None
            old_modeled_bytes = state.modeled_bytes()
            state.entry_count -= 1
            state.shallow_total -= entry.shallow_bytes
            if state.entry_count == 0:
                self._strata.pop(entry.stratum)
                if prepared.metadata is not None:
                    self._calibration_history[entry.stratum] = prepared.metadata
                    self._calibration_history.move_to_end(entry.stratum)
                    if prepared.history_trim_key is not None:
                        self._calibration_history.pop(prepared.history_trim_key, None)
                new_modeled_bytes = 0
            else:
                new_modeled_bytes = state.modeled_bytes()
            self._total_bytes += (
                new_modeled_bytes - old_modeled_bytes - entry.fixed_bytes
            )
        graph_bytes_released = self._graph_ledger.commit_release(prepared.release)
        self._total_bytes -= graph_bytes_released
        self._accounting_revision += 1
        assert self._exact_total_bytes >= 0
        assert self._total_bytes >= 0
        return entry

    def _prepare_entry_removal_locked(
        self,
        tracked_key: TrackedKey,
        entry: _TrackedEntry,
    ) -> _PreparedEntryRemoval:
        """Allocate release and calibration records before removing an entry."""
        preparation_revision = self._accounting_revision
        state = self._strata.get(entry.stratum) if entry.stratum is not None else None
        metadata = None
        history_trim_key = None
        if state is not None and state.entry_count == 1 and state.samples:
            metadata = _CalibrationMetadata(
                admission_count=state.admission_count,
                samples=tuple(state.samples),
            )
            if (
                entry.stratum not in self._calibration_history
                and len(self._calibration_history)
                >= RUN_CONTEXT_CALIBRATION_HISTORY_LIMIT
            ):
                history_iterator = iter(self._calibration_history)
                if preparation_revision != self._accounting_revision:
                    raise _StaleGraphPreparation
                try:
                    history_trim_key = next(history_iterator)
                except RuntimeError:
                    if preparation_revision != self._accounting_revision:
                        raise _StaleGraphPreparation from None
                    raise
                if preparation_revision != self._accounting_revision:
                    raise _StaleGraphPreparation
        mru_after = None
        if tracked_key == self._mru_key:
            # Construct and consume a bounded reverse iterator before any
            # mutation.  Validate between those steps because construction can
            # run a GC finalizer.
            reverse_entries = reversed(self._entries)
            if preparation_revision != self._accounting_revision:
                raise _StaleGraphPreparation
            try:
                newest = next(reverse_entries)
                if preparation_revision == self._accounting_revision:
                    mru_after = (
                        next(reverse_entries, None) if newest == tracked_key else None
                    )
            except RuntimeError:
                if preparation_revision != self._accounting_revision:
                    raise _StaleGraphPreparation from None
                raise
            if preparation_revision != self._accounting_revision:
                raise _StaleGraphPreparation
        return _PreparedEntryRemoval(
            entry=entry,
            state=state,
            metadata=metadata,
            history_trim_key=history_trim_key,
            release=self._graph_ledger.prepare_release(entry.dependency_root),
            revision=preparation_revision,
            mru_after=mru_after,
        )

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
            if entry is None:
                # Preparation ran a finalizer that replaced this key.  The
                # outer eviction is stale; reconsider current LRU state.
                continue
            removed_bytes = before - self._total_bytes
            owner = entry.owner()
            if owner is None:
                continue
            self._entry_attempt_generations.pop(tracked_key, None)
            owner._evict_run_cache_entry(entry.namespace, entry.key)
            self._forget_unused_canonical_key_locked(tracked_key)
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
        while True:
            # Building the filtered tuple can run a finalizer.  Iterate a
            # stable key snapshot so a reentrant reset cannot invalidate it.
            owner_keys = tuple(
                tracked_key
                for tracked_key in tuple(self._entries)
                if tracked_key[0] == owner_id
            )
            if not owner_keys:
                return
            for tracked_key in owner_keys:
                self._remove_entry_locked(tracked_key)

    def _next_admission_generation_locked(self) -> int:
        self._next_admission_generation += 1
        return self._next_admission_generation

    def _tracked_key_locked(
        self,
        owner_id: int,
        namespace: RunCacheNamespace,
        key: Hashable,
    ) -> TrackedKey:
        """Resolve a public key before entering accounting publication.

        ``dict`` lookup here intentionally retains normal cache-key equality
        semantics.  All coordinator maps subsequently use the returned
        integer, avoiding user-defined hashing or equality in the commit
        window.
        """
        raw_hash = hash(key)
        matched_handle: _CanonicalHandle | None = None
        # ``hash`` above and ``==`` below are user code.  A reentrant cache
        # operation may remove an empty bucket while this lookup is in
        # progress, so retry from the authoritative maps rather than append
        # to a detached list.  This remains bounded like other admission
        # retries; callers separately reject a stale owner/configuration.
        for _ in range(RUN_CONTEXT_TRACK_MAX_REESTIMATES + 1):
            owner_keys = self._canonical_keys.get(owner_id)
            if owner_keys is None:
                owner_keys = {}
                self._canonical_keys[owner_id] = owner_keys
                self._canonical_revision += 1
                continue
            namespace_keys = owner_keys.get(namespace)
            if namespace_keys is None:
                namespace_keys = {}
                owner_keys[namespace] = namespace_keys
                self._canonical_revision += 1
                continue
            bucket = namespace_keys.get(raw_hash)
            if bucket is None:
                bucket = []
                namespace_keys[raw_hash] = bucket
                self._canonical_revision += 1
                continue
            resolution_revision = self._canonical_revision
            # Materializing the short collision bucket before comparison keeps
            # a reentrant list mutation from changing iteration mid-lookup.
            handles = tuple(bucket)
            if resolution_revision != self._canonical_revision:
                continue
            for handle in handles:
                if handle.key is key or handle.key == key:
                    if resolution_revision == self._canonical_revision:
                        return (owner_id, namespace, handle.identity)
                    # The equality result is still useful if a peer changed
                    # only another canonical bucket.  The bounded fallback
                    # below can reuse this exact handle after an
                    # identity-only authority check, instead of admitting a
                    # duplicate equal key.
                    matched_handle = handle
                    break
            else:
                if resolution_revision != self._canonical_revision:
                    continue
                self._next_canonical_key += 1
                handle = _CanonicalHandle(self._next_canonical_key, raw_hash, key)
                if resolution_revision != self._canonical_revision:
                    continue
                # These two maps contain integer/object identities only.  A
                # finalizer here can publish through the same handle, which is
                # safe; an invalidated outer caller checks its accounting
                # revision before entering publication.
                bucket.append(handle)
                self._canonical_handles[handle.identity] = handle
                self._canonical_revision += 1
                return (owner_id, namespace, handle.identity)
        # A hostile equality hook can keep mutating the same bucket.  Prepare
        # the entire fallback before reading a live canonical registry: its
        # constructor and container allocations may run a finalizer.  The
        # subsequent native inserts use only freshly fetched hierarchy nodes,
        # never a bucket captured before reentry.
        self._next_canonical_key += 1
        fallback_identity = self._next_canonical_key
        fallback_result = (owner_id, namespace, fallback_identity)
        fallback_handle = _CanonicalHandle(
            fallback_identity,
            raw_hash,
            key,
        )
        prepared_owner_keys: dict[
            RunCacheNamespace, dict[int, list[_CanonicalHandle]]
        ] = {}
        prepared_namespace_keys: dict[int, list[_CanonicalHandle]] = {}
        prepared_bucket: list[_CanonicalHandle] = []

        if (
            matched_handle is not None
            and self._canonical_handle_is_authoritative_locked(
                owner_id,
                namespace,
                matched_handle,
            )
        ):
            return (owner_id, namespace, matched_handle.identity)

        owner_keys = self._canonical_keys.get(owner_id)
        if owner_keys is None:
            # A constructor finalizer can unregister this owner.  Return the
            # unregistered prepared identity so the caller's existing
            # accounting/lifecycle guard rejects the stale operation; neither
            # registry retains the temporary handle.
            if owner_id not in self._owner_references:
                return fallback_result
            owner_keys = prepared_owner_keys
            self._canonical_keys[owner_id] = owner_keys
            self._canonical_revision += 1
        namespace_keys = owner_keys.get(namespace)
        if namespace_keys is None:
            namespace_keys = prepared_namespace_keys
            owner_keys[namespace] = namespace_keys
            self._canonical_revision += 1
        bucket = namespace_keys.get(raw_hash)
        if bucket is None:
            bucket = prepared_bucket
            namespace_keys[raw_hash] = bucket
            self._canonical_revision += 1
        bucket.append(fallback_handle)
        self._canonical_handles[fallback_handle.identity] = fallback_handle
        self._canonical_revision += 1
        return fallback_result

    def _canonical_handle_is_authoritative_locked(
        self,
        owner_id: int,
        namespace: RunCacheNamespace,
        handle: _CanonicalHandle,
    ) -> bool:
        """Return whether ``handle`` remains in its current collision bucket."""
        if self._canonical_handles.get(handle.identity) is not handle:
            return False
        owner_keys = self._canonical_keys.get(owner_id)
        if owner_keys is None:
            return False
        namespace_keys = owner_keys.get(namespace)
        if namespace_keys is None:
            return False
        bucket = namespace_keys.get(handle.raw_hash)
        if bucket is None:
            return False
        index = 0
        while index < len(bucket):
            if bucket[index] is handle:
                return True
            index += 1
        return False

    def _forget_canonical_owner_keys_locked(self, owner_id: int) -> None:
        """Drop public-key handles only after an owner leaves the coordinator."""
        owner_keys = self._canonical_keys.pop(owner_id, None)
        if owner_keys is None:
            return
        for namespace_keys in owner_keys.values():
            for bucket in namespace_keys.values():
                for handle in bucket:
                    self._canonical_handles.pop(handle.identity, None)
        self._canonical_revision += 1

    def _forget_unused_canonical_key_locked(self, tracked_key: TrackedKey) -> None:
        """Drop a handle once no entry or pending attempt references it."""
        owner_id, namespace, canonical_key = tracked_key
        while True:
            if (
                tracked_key in self._entries
                or tracked_key in self._entry_attempt_generations
            ):
                return
            preparation_revision = self._canonical_revision
            handle = self._canonical_handles.get(canonical_key)
            if handle is None:
                return
            owner_keys = self._canonical_keys.get(owner_id)
            namespace_keys = (
                owner_keys.get(namespace) if owner_keys is not None else None
            )
            bucket = (
                namespace_keys.get(handle.raw_hash)
                if namespace_keys is not None
                else None
            )
            # Locate the integer identity before touching any live registry.
            # The direct index loop avoids an ``enumerate`` allocation that can
            # run a finalizer between the two coupled maps.
            index = 0
            while (
                bucket is not None
                and index < len(bucket)
                and bucket[index].identity != canonical_key
            ):
                index += 1
            if (
                preparation_revision != self._canonical_revision
                or self._canonical_handles.get(canonical_key) is not handle
            ):
                # A peer may have changed a bucket while preparation ran.  If
                # this handle is still unused, rebuild the local references;
                # a current replacement instead ends cleanup immediately.
                continue
            if (
                tracked_key in self._entries
                or tracked_key in self._entry_attempt_generations
            ):
                return
            if bucket is None or index == len(bucket):
                # An interrupted fallback can leave only the authoritative
                # handle-map record after its owner hierarchy was cleared.
                # It is safe to remove that targeted orphan once the same
                # revision and object identity have been revalidated above.
                self._canonical_handles.pop(canonical_key)
                self._canonical_revision += 1
                return
            assert owner_keys is not None
            assert namespace_keys is not None
            self._canonical_handles.pop(canonical_key)
            bucket.pop(index)
            if not bucket:
                namespace_keys.pop(handle.raw_hash)
            if not namespace_keys:
                owner_keys.pop(namespace)
                if not owner_keys:
                    self._canonical_keys.pop(owner_id)
            self._canonical_revision += 1
            return

    def _begin_admission_attempt_locked(
        self,
        owner_id: int,
        tracked_key: TrackedKey,
        *,
        reuse_existing: bool = False,
    ) -> _AdmissionAttempt:
        """Assign one token before any admission work can reenter the budget."""
        entry_attempt_generation = (
            self._entry_attempt_generations.get(tracked_key) if reuse_existing else None
        )
        if entry_attempt_generation is None:
            entry_attempt_generation = self._next_admission_generation_locked()
            self._entry_attempt_generations[tracked_key] = entry_attempt_generation
        return _AdmissionAttempt(
            configuration_generation=self._configuration_generation,
            owner_id=owner_id,
            owner_lifecycle_generation=self._owner_lifecycle_generation_locked(
                owner_id
            ),
            tracked_key=tracked_key,
            entry_attempt_generation=entry_attempt_generation,
        )

    def _admission_attempt_is_current_locked(
        self,
        attempt: _AdmissionAttempt,
    ) -> bool:
        return (
            attempt.configuration_generation == self._configuration_generation
            and self._admission_attempt_key_is_current_locked(attempt)
        )

    def _admission_attempt_key_is_current_locked(
        self,
        attempt: _AdmissionAttempt,
    ) -> bool:
        return (
            attempt.owner_lifecycle_generation
            == self._owner_lifecycle_generations.get(attempt.owner_id, 0)
            and attempt.entry_attempt_generation
            == self._entry_attempt_generations.get(attempt.tracked_key, 0)
        )

    def _admission_attempt_stale_result_locked(
        self,
        attempt: _AdmissionAttempt,
    ) -> Literal["stale", "retry_configuration"]:
        """Classify stale work without evicting a newer key generation."""
        max_bytes = self._max_bytes
        if (
            self._admission_attempt_key_is_current_locked(attempt)
            and attempt.configuration_generation != self._configuration_generation
            and max_bytes is not None
            and max_bytes > 0
        ):
            return "retry_configuration"
        return "stale"

    def _reject_zero_attempt_locked(
        self,
        attempt: _AdmissionAttempt,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
    ) -> None:
        """Evict a candidate that remains current after a zero-cap transition."""
        if self._max_bytes != 0:
            return
        self._fail_admission_attempt_locked(
            attempt,
            owner,
            namespace,
            key,
            allow_configuration_change=True,
        )

    def _fail_admission_attempt_locked(
        self,
        attempt: _AdmissionAttempt,
        owner: RunContextCacheOwner,
        namespace: RunCacheNamespace,
        key: Hashable,
        *,
        allow_configuration_change: bool = False,
    ) -> None:
        """Evict a failed estimate only when it still owns this key's token."""
        is_current = (
            self._admission_attempt_key_is_current_locked(attempt)
            if allow_configuration_change
            else self._admission_attempt_is_current_locked(attempt)
        )
        if not is_current:
            return
        self._remove_entry_accounting_locked(attempt.tracked_key)
        if not self._admission_attempt_key_is_current_locked(attempt):
            return
        self._entry_attempt_generations.pop(attempt.tracked_key, None)
        owner._evict_run_cache_entry(namespace, key)
        self._forget_unused_canonical_key_locked(attempt.tracked_key)

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
                self._forget_canonical_owner_keys_locked(owner_id)
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


def _framework_cache_payload(
    value: object,
) -> tuple[object, DependencySnapshot | None, int]:
    """Split exact framework metadata from the ordinary cached payload."""
    from general_manager.cache.dependency_cache import (
        DependencyCacheHit,
        _TrustedDependencyCacheHit,
    )
    from general_manager.cache.run_context import _RunCacheEntry

    if type(value) is _RunCacheEntry:
        return value.value, value.dependency_root, _framework_wrapper_size(value)
    if type(value) is DependencyCacheHit or type(value) is _TrustedDependencyCacheHit:
        # Do not read ``dependency_root`` from arbitrary subclasses: a budget
        # admission must not invoke user-overridable properties.
        fixed_bytes = _framework_wrapper_size(value)
        if value._dependency_root is None:
            # Empty hits do not have a graph block to own this immutable
            # container, but the hit still retains it.
            fixed_bytes += _safe_shallow_size(value.dependencies)
        return value.value, value._dependency_root, fixed_bytes
    return value, None, 0


def _framework_wrapper_size(value: object) -> int:
    """Measure an exact private framework wrapper without user size hooks."""
    try:
        return sys.getsizeof(value)
    except Exception:  # noqa: BLE001 - preserve bounded admission fallback.
        return MIN_TRACKED_ENTRY_BYTES


def _admission_signal(
    namespace: RunCacheNamespace,
    key: object,
    value: object,
) -> _AdmissionSignal:
    """Build a payload-only admission signal plus shared graph metadata."""
    payload, dependency_root, fixed_bytes = _framework_cache_payload(value)
    return replace(
        _payload_admission_signal(namespace, key, payload),
        fixed_bytes=fixed_bytes,
        dependency_root=dependency_root,
    )


def _payload_admission_signal(
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

    builtin_family = _BUILTIN_STORAGE_FAMILIES.get(id(value_type))
    if builtin_family is not None:
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
            length * RUN_CONTEXT_SAMPLE_MARGIN_NUMERATOR,
            len(sample_indexes) * RUN_CONTEXT_SAMPLE_MARGIN_DENOMINATOR,
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
            len(mapping) * RUN_CONTEXT_SAMPLE_MARGIN_NUMERATOR,
            len(sample_entries) * RUN_CONTEXT_SAMPLE_MARGIN_DENOMINATOR,
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
            len(container) * RUN_CONTEXT_SAMPLE_MARGIN_NUMERATOR,
            len(sample_items) * RUN_CONTEXT_SAMPLE_MARGIN_DENOMINATOR,
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
    """Estimate one framework entry including its private dependency graph.

    Coordinator admission uses the payload-only helper below, then shares graph
    costs through its ledger.  This public helper keeps its historical utility
    for callers that need to derive a whole-entry budget in isolation.
    """
    payload, dependency_root, fixed_bytes = _framework_cache_payload(value)
    payload_size = _estimate_raw_payload_cache_entry_size(
        key,
        payload,
        stop_after=stop_after,
    )
    total_size = payload_size + fixed_bytes
    if stop_after is not None and total_size > stop_after:
        return stop_after + 1
    if dependency_root is None:
        return total_size
    ledger = _SharedGraphLedger()
    graph_size = ledger.retain(dependency_root)
    if stop_after is not None and total_size + graph_size > stop_after:
        return stop_after + 1
    return total_size + graph_size


def _estimate_admission_payload_size(
    key: object,
    value: object,
    *,
    stop_after: int | None,
) -> int:
    """Estimate admission payloads while keeping legacy generic hooks intact."""
    _payload, dependency_root, fixed_bytes = _framework_cache_payload(value)
    if dependency_root is None and fixed_bytes == 0:
        return estimate_cache_entry_size(key, value, stop_after=stop_after)
    # The helper strips recognized framework wrappers itself, preserving the
    # legacy estimator seam while fixed wrapper bytes remain outside samples.
    return _estimate_payload_cache_entry_size(key, value, stop_after=stop_after)


def _estimate_payload_cache_entry_size(
    key: object,
    value: object,
    *,
    stop_after: int | None,
) -> int:
    """Estimate owned bytes for one cache entry without unbounded traversal."""
    # Framework graph metadata is charged by ``_SharedGraphLedger``.  Keep the
    # estimator payload-only on every coordinator admission/calibration/rebuild
    # path.
    value, _dependency_root, _fixed_bytes = _framework_cache_payload(value)
    return _estimate_raw_payload_cache_entry_size(key, value, stop_after=stop_after)


def _estimate_raw_payload_cache_entry_size(
    key: object,
    value: object,
    *,
    stop_after: int | None,
) -> int:
    """Estimate a payload already projected from its outer framework wrapper."""
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
