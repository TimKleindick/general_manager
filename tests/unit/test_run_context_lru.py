from collections import OrderedDict, deque
from collections.abc import Hashable, Iterable, Iterator
from datetime import date, timedelta
from threading import Event, Thread
from unittest import mock
import math
import sys
from types import ModuleType
from typing import Callable, cast
from weakref import ref

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from general_manager.cache.run_context_lru import (
    MIN_TRACKED_ENTRY_BYTES,
    ProcessRunContextCacheBudget,
    RunCacheNamespace,
    _TrackedEntry,
    _stratified_indexes,
    estimate_cache_entry_size,
    resolve_run_context_cache_max_bytes,
)
from general_manager.cache import run_context_lru

HANDOFF_TIMEOUT_SECONDS = 5


class CacheOwner:
    def __init__(self) -> None:
        self.entries: dict[tuple[RunCacheNamespace, Hashable], object] = {}
        self.budget_enabled = False

    def _set_run_cache_budget_enabled(self, enabled: bool) -> None:
        self.budget_enabled = enabled

    def _set_run_cache_modes(
        self,
        budget_enabled: bool,
        recency_enabled: bool,
        generation: int,
    ) -> None:
        del recency_enabled, generation
        self.budget_enabled = budget_enabled

    def store(
        self,
        namespace: RunCacheNamespace,
        key: Hashable,
        value: object,
    ) -> None:
        self.entries[(namespace, key)] = value

    def _iter_run_cache_entries(
        self,
    ) -> Iterable[tuple[RunCacheNamespace, Hashable, object]]:
        for (namespace, key), value in self.entries.items():
            yield namespace, key, value

    def _evict_run_cache_entry(
        self,
        namespace: RunCacheNamespace,
        key: Hashable,
    ) -> None:
        self.entries.pop((namespace, key), None)


SEEDED_DUE_STRATUM = cast(run_context_lru.StratumKey, ("values", "list", 1))
SEEDED_CALIBRATION_WINDOW = ((100, 1_000),) * 8


def _seed_due_stratum_with_peer(
    budget: ProcessRunContextCacheBudget,
    peer_owner: CacheOwner,
    pending_owner: CacheOwner,
) -> tuple[run_context_lru._StratumState, tuple[int, RunCacheNamespace, Hashable]]:
    budget.register(peer_owner, 100_000)
    budget.register(pending_owner, 100_000)
    state = run_context_lru._StratumState(
        admission_count=255,
        samples=deque(SEEDED_CALIBRATION_WINDOW, maxlen=8),
    )
    budget._strata[SEEDED_DUE_STRATUM] = state
    peer_key = (id(peer_owner), "values", "peer")
    peer_owner.store("values", "peer", ["peer"])
    with budget._lock:
        budget._add_entry_accounting_locked(
            peer_key,
            _TrackedEntry(
                owner=budget._owner_reference_locked(peer_owner),
                namespace="values",
                key="peer",
                exact_bytes=0,
                stratum=SEEDED_DUE_STRATUM,
                shallow_bytes=100,
            ),
        )
        budget._mru_key = peer_key
    assert budget.estimated_bytes == 1_000
    return state, peer_key


def _assert_seeded_due_stratum_unchanged(
    budget: ProcessRunContextCacheBudget,
    peer_owner: CacheOwner,
    state: run_context_lru._StratumState,
    peer_key: tuple[int, RunCacheNamespace, Hashable],
) -> None:
    assert budget._strata == {SEEDED_DUE_STRATUM: state}
    assert list(state.samples) == list(SEEDED_CALIBRATION_WINDOW)
    assert state.admission_count == 255
    assert state.entry_count == 1
    assert state.shallow_total == 100
    assert set(budget._entries) == {peer_key}
    assert peer_owner.entries == {("values", "peer"): ["peer"]}
    assert budget.estimated_bytes == 1_000


class LimitRaisingCacheOwner(CacheOwner):
    def __init__(
        self,
        budget: ProcessRunContextCacheBudget,
        replacement_limit: int,
    ) -> None:
        super().__init__()
        self._budget = budget
        self._replacement_limit = replacement_limit
        self._eviction_count = 0

    def _evict_run_cache_entry(
        self,
        namespace: RunCacheNamespace,
        key: Hashable,
    ) -> None:
        super()._evict_run_cache_entry(namespace, key)
        self._eviction_count += 1
        if self._eviction_count == 1:
            self._budget.register(self, self._replacement_limit)


class OrderedOwnerRegistry:
    def __init__(self, owners: Iterable[CacheOwner]) -> None:
        self._owners = list(owners)

    def add(self, owner: CacheOwner) -> None:
        if owner not in self._owners:
            self._owners.append(owner)

    def discard(self, owner: CacheOwner) -> None:
        if owner in self._owners:
            self._owners.remove(owner)

    def __iter__(self) -> Iterator[CacheOwner]:
        return iter(self._owners)


class RaisingLock:
    def __enter__(self) -> None:
        raise AssertionError("unexpected coordinator lock entry")  # noqa: TRY003

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        return None


class ModeRecordingCacheOwner(CacheOwner):
    def __init__(self) -> None:
        super().__init__()
        self.mode_updates: list[tuple[bool, bool, int]] = []
        self.mode_generation = -1

    def _set_run_cache_modes(
        self,
        budget_enabled: bool,
        recency_enabled: bool,
        generation: int,
    ) -> None:
        if generation < self.mode_generation:
            return
        self.mode_generation = generation
        self.budget_enabled = budget_enabled
        self.mode_updates.append((budget_enabled, recency_enabled, generation))


def _track_exact_bytes(
    budget: ProcessRunContextCacheBudget,
    owner: CacheOwner,
    key: str,
    exact_bytes: int,
) -> None:
    owner.store("values", key, key)
    with mock.patch.object(
        run_context_lru,
        "_admission_signal",
        return_value=run_context_lru._AdmissionSignal(
            stratum=None,
            shallow_bytes=0,
            exact_bytes=exact_bytes,
        ),
    ):
        budget.track(owner, "values", key, key)


class CountingHashKey:
    def __init__(self) -> None:
        self.hash_calls = 0

    def __hash__(self) -> int:
        self.hash_calls += 1
        return 1


class RepresentativeRow:
    def __init__(self, index: int) -> None:
        self.identifier = index
        self.name = f"row-{index}"
        self.active = index % 2 == 0
        self.score = index * 3


class RepresentativeManager:
    __slots__ = ("inputs", "label")

    def __init__(self, index: int) -> None:
        self.inputs = (index, index + 1)
        self.label = f"manager-{index % 8}"


class RepresentativeMetric:
    __slots__ = ("count", "name", "value")

    def __init__(self, count: int, name: str, value: float) -> None:
        self.count = count
        self.name = name
        self.value = value


def _exhaustive_representative_entry_size(key: object, value: object) -> int:
    """Test-owned v0.69-style exhaustive ownership traversal."""
    atomic_types = (
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
    sized_builtin_types = (*atomic_types, dict, tuple, list, set, frozenset)
    candidates = [key, value]
    seen: set[int] = set()
    measured_bytes = 0

    while candidates:
        candidate = candidates.pop()
        candidate_id = id(candidate)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        candidate_type = type(candidate)
        if candidate_type in sized_builtin_types:
            measured_bytes += sys.getsizeof(candidate)
        else:
            measured_bytes += object.__sizeof__(candidate)

        if candidate_type in atomic_types:
            continue
        if candidate_type is dict:
            for item_key, item_value in cast(dict[object, object], candidate).items():
                candidates.extend((item_key, item_value))
            continue
        if candidate_type in (tuple, list, set, frozenset):
            candidates.extend(cast(Iterable[object], candidate))
            continue

        try:
            instance_dict = vars(candidate)
        except TypeError:
            instance_dict = None
        if instance_dict is not None:
            candidates.append(instance_dict)
        for cls in candidate_type.__mro__:
            declared_slots = cls.__dict__.get("__slots__", ())
            if isinstance(declared_slots, str):
                slot_names = (declared_slots,)
            else:
                slot_names = tuple(declared_slots)
            for slot_name in slot_names:
                if slot_name in {"__dict__", "__weakref__"}:
                    continue
                try:
                    candidates.append(object.__getattribute__(candidate, slot_name))
                except AttributeError:
                    pass

    return max(256, measured_bytes)


def _representative_homogeneous_bytearray_lists() -> list[tuple[Hashable, object]]:
    return [(index, [bytearray(96)]) for index in range(512)]


def _representative_heterogeneous_lists() -> list[tuple[Hashable, object]]:
    entries = []
    for index in range(512):
        value: list[object] = [index] * 9
        value[0] = bytearray(32)
        value[4] = bytearray(160)
        value[8] = bytearray(288)
        entries.append((index, value))
    return entries


def _representative_builtin_mappings_and_sets() -> list[tuple[Hashable, object]]:
    mappings = [
        (
            ("mapping", index),
            {offset: bytearray(24 + offset) for offset in range(4)},
        )
        for index in range(256)
    ]
    sets = [
        (("set", index), {index * 4 + offset for offset in range(4)})
        for index in range(256)
    ]
    return [*mappings, *sets]


def _representative_orm_rows() -> list[tuple[Hashable, object]]:
    return [(index, RepresentativeRow(index)) for index in range(512)]


def _representative_slotted_managers() -> list[tuple[Hashable, object]]:
    return [(index, RepresentativeManager(index)) for index in range(512)]


def _representative_metric_series() -> list[tuple[Hashable, object]]:
    entries = []
    first_day = date(2024, 1, 1)
    for index in range(512):
        records = (
            RepresentativeMetric(index, "actual", index / 10),
            RepresentativeMetric(index + 1, "forecast", index / 8),
        )
        series = {
            first_day + timedelta(days=offset): records[offset % 2]
            for offset in range(120)
        }
        entries.append((index, series))
    return entries


def test_representative_metric_series_uses_120_distinct_dates() -> None:
    entries = _representative_metric_series()

    for _key, series in entries:
        metric_series = cast(dict[object, object], series)
        assert len(metric_series) == 120
        assert all(type(day) is date for day in metric_series)


def _representative_shared_aggregate() -> list[tuple[Hashable, object]]:
    aggregate = {index: [bytearray(24)] for index in range(120)}
    references = [(("value", index), value) for index, value in aggregate.items()]
    return [("aggregate", aggregate), *references]


def _representative_cycles() -> list[tuple[Hashable, object]]:
    entries: list[tuple[Hashable, object]] = []
    for index in range(256):
        direct: list[object] = []
        direct.append(direct)
        entries.append((("direct", index), direct))
    for index in range(256):
        first: list[object] = []
        second: list[object] = [first]
        first.append(second)
        entries.append((("mutual", index), first))
    return entries


REPRESENTATIVE_CORPUS_BUILDERS = (
    _representative_homogeneous_bytearray_lists,
    _representative_heterogeneous_lists,
    _representative_builtin_mappings_and_sets,
    _representative_orm_rows,
    _representative_slotted_managers,
    _representative_metric_series,
    _representative_shared_aggregate,
    _representative_cycles,
)


@pytest.mark.parametrize(
    "build_entries",
    REPRESENTATIVE_CORPUS_BUILDERS,
    ids=lambda builder: builder.__name__.removeprefix("_representative_"),
)
def test_representative_aggregate_is_within_five_percent(
    build_entries: object,
) -> None:
    entries = cast(Callable[[], list[tuple[Hashable, object]]], build_entries)()
    exact_total = sum(
        _exhaustive_representative_entry_size(key, value) for key, value in entries
    )
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 1_000_000_000)

    groups: dict[object, list[tuple[Hashable, object]]] = {}
    for key, value in entries:
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)
        stratum = budget._entries[(id(owner), "values", key)].stratum
        if stratum is not None:
            groups.setdefault(stratum, []).append((key, value))

    for stratum, grouped_entries in groups.items():
        next_index = 0
        while len(budget._strata[cast(tuple, stratum)].samples) < 8:
            key, value = grouped_entries[next_index % len(grouped_entries)]
            owner.store("values", key, value)
            budget.track(owner, "values", key, value)
            next_index += 1

    assert math.ceil(exact_total * 0.95) <= budget.estimated_bytes
    assert budget.estimated_bytes <= math.ceil(exact_total * 1.05)


def test_representative_pressure_retains_between_ninety_and_one_hundred_percent() -> (
    None
):
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    configured_bytes = 2_560
    budget.register(owner, configured_bytes)
    first_value = [bytearray(96)]
    owner.store("values", 0, first_value)
    budget.track(owner, "values", 0, first_value)
    stratum = budget._entries[(id(owner), "values", 0)].stratum
    assert stratum is not None

    while len(budget._strata[stratum].samples) < 8:
        owner.store("values", 0, first_value)
        budget.track(owner, "values", 0, first_value)

    for index in range(1, 10):
        value = [bytearray(96)]
        owner.store("values", index, value)
        budget.track(owner, "values", index, value)

    retained_bytes = sum(
        _exhaustive_representative_entry_size(key, value)
        for (_namespace, key), value in owner.entries.items()
    )
    assert len(owner.entries) == 9
    assert retained_bytes <= configured_bytes
    assert retained_bytes >= 2_304


@pytest.mark.parametrize(
    ("configured_bytes", "expected_target"),
    [(1, 0), (19, 18), (20, 19), (101, 95), (1_000, 950)],
)
def test_eviction_target_uses_floor_of_ninety_five_percent(
    configured_bytes: int,
    expected_target: int,
) -> None:
    assert run_context_lru._eviction_target(configured_bytes) == expected_target


@pytest.mark.parametrize(
    ("first_charge", "expected_keys"),
    [(694, {"first", "second"}), (695, {"second"})],
)
def test_reserve_eviction_keeps_equality_and_evicts_one_byte_above_target(
    first_charge: int,
    expected_keys: set[str],
) -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 1_000)
    owner_reference = ref(owner)
    entries = (("first", first_charge), ("second", 256))

    with budget._lock:
        for key, exact_bytes in entries:
            owner.store("values", key, key)
            budget._add_entry_accounting_locked(
                (id(owner), "values", key),
                _TrackedEntry(
                    owner=owner_reference,
                    namespace="values",
                    key=key,
                    exact_bytes=exact_bytes,
                    stratum=None,
                    shallow_bytes=0,
                ),
            )
            budget._mru_key = (id(owner), "values", key)
        budget._evict_excess_locked()

    assert {key for _namespace, key in owner.entries} == expected_keys
    assert budget.estimated_bytes == sum(
        exact_bytes for key, exact_bytes in entries if key in expected_keys
    )


@pytest.mark.parametrize(
    ("estimated_bytes", "expected_reason"),
    [
        (960, "run cache entry evicted by process-wide LRU budget"),
        (1_001, "run cache entry skipped because it exceeds the process budget"),
    ],
)
def test_complete_cap_oversized_rejection_is_distinct_from_reserve_eviction(
    estimated_bytes: int,
    expected_reason: str,
) -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 1_000)
    value = [bytearray(64)]
    owner.store("values", "key", value)

    with (
        mock.patch.object(
            run_context_lru,
            "estimate_cache_entry_size",
            return_value=estimated_bytes,
        ),
        mock.patch.object(run_context_lru.logger, "debug") as debug,
    ):
        budget.track(owner, "values", "key", value)

    assert not owner.entries
    assert not budget._entries
    assert budget.estimated_bytes == 0
    assert debug.call_args.args == (expected_reason,)


def test_stratum_models_all_entries_from_rolling_samples() -> None:
    state = run_context_lru._StratumState(
        entry_count=4,
        shallow_total=400,
        samples=deque([(100, 1_000)], maxlen=8),
    )

    assert state.modeled_bytes() == 4_000


def test_stratum_keeps_only_eight_recent_samples() -> None:
    state = run_context_lru._StratumState()
    for deep in range(1_000, 2_000, 100):
        state.samples.append((100, deep))

    assert list(state.samples) == [
        (100, 1_200),
        (100, 1_300),
        (100, 1_400),
        (100, 1_500),
        (100, 1_600),
        (100, 1_700),
        (100, 1_800),
        (100, 1_900),
    ]


def test_stratum_calibrates_first_and_each_256th_admission() -> None:
    state = run_context_lru._StratumState()

    decisions = []
    for _ in range(512):
        decisions.append(state.should_calibrate_next())
        state.admission_count += 1

    assert [index + 1 for index, decision in enumerate(decisions) if decision] == [
        1,
        256,
        512,
    ]


def test_track_calibrates_only_first_and_each_256th_stratum_admission() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000_000)
    deep_calls = 0

    def estimate(key: object, value: object, *, stop_after: int | None) -> int:
        nonlocal deep_calls
        deep_calls += 1
        return 1_024

    with mock.patch.object(run_context_lru, "estimate_cache_entry_size", estimate):
        for index in range(512):
            value = [index]
            owner.store("values", index, value)
            budget.track(owner, "values", index, value)

    assert deep_calls == 3
    assert len(owner.entries) == 512
    assert budget.estimated_bytes > 0


@pytest.mark.parametrize("seed_count", [0, 255])
def test_concurrent_same_stratum_admissions_publish_one_due_calibration(
    seed_count: int,
) -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000_000)
    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        return_value=1_024,
    ):
        for index in range(seed_count):
            value = [index]
            owner.store("values", index, value)
            budget.track(owner, "values", index, value)

    first_key = seed_count
    second_key = seed_count + 1
    values = {first_key: [first_key], second_key: [second_key]}
    estimator_started = {first_key: Event(), second_key: Event()}
    allow_estimators = Event()
    worker_errors: list[BaseException] = []

    def blocking_estimator(
        key: object, value: object, *, stop_after: int | None
    ) -> int:
        typed_key = cast(int, key)
        estimator_started[typed_key].set()
        assert allow_estimators.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return 1_024

    def track_entry(key: int) -> None:
        try:
            budget.track(owner, "values", key, values[key])
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    for key, value in values.items():
        owner.store("values", key, value)
    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        blocking_estimator,
    ):
        workers = [Thread(target=track_entry, args=(key,)) for key in values]
        for worker in workers:
            worker.start()
        try:
            assert all(
                started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
                for started in estimator_started.values()
            )
        finally:
            allow_estimators.set()
            for worker in workers:
                worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert all(not worker.is_alive() for worker in workers)
    if worker_errors:
        raise worker_errors[0]
    state = next(iter(budget._strata.values()))
    expected_sample_count = 1 if seed_count == 0 else 2
    assert len(state.samples) == expected_sample_count
    assert state.admission_count == seed_count + 2
    assert len(owner.entries) == seed_count + 2


def test_atomic_admission_never_calibrates() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "key", "value")

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=AssertionError("atomic admission calibrated"),
    ):
        budget.track(owner, "values", "key", "value")

    assert owner.entries == {("values", "key"): "value"}
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES


def test_ordinary_calibrated_admission_publishes_without_attempt_token() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000_000)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        return_value=1_024,
    ) as estimator:
        for index in range(255):
            value = [index]
            owner.store("values", index, value)
            budget.track(owner, "values", index, value)

    assert estimator.call_count == 1
    assert len(owner.entries) == 255
    assert not budget._entry_attempt_generations


def test_calibration_updates_aggregate_without_iterating_entries() -> None:
    class DirectOnlyEntries(OrderedDict[object, object]):
        def __iter__(self) -> Iterator[object]:
            raise AssertionError("aggregate calibration iterated entries")  # noqa: TRY003

        def values(self) -> object:
            raise AssertionError("aggregate calibration read entry values")  # noqa: TRY003

    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000_000)
    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        return_value=1_024,
    ):
        for index in range(2):
            value = [index]
            owner.store("values", index, value)
            budget.track(owner, "values", index, value)

    stratum = run_context_lru._admission_signal("values", 0, [0]).stratum
    assert stratum is not None
    before = budget.estimated_bytes
    original_entries = budget._entries
    budget._entries = cast(OrderedDict, DirectOnlyEntries(budget._entries))

    try:
        with budget._lock:
            budget._publish_calibration_locked(stratum, 100, 2_000)
    finally:
        budget._entries = original_entries

    assert budget.estimated_bytes > before


def test_replacement_changes_calibrated_stratum_accounting() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000_000)
    small = [1]
    large = [1, 2]
    owner.store("values", "key", small)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=[1_024, 2_048],
    ):
        budget.track(owner, "values", "key", small)
        owner.store("values", "key", large)
        budget.track(owner, "values", "key", large)

    entry = budget._entries[(id(owner), "values", "key")]
    assert (
        entry.stratum
        == run_context_lru._admission_signal("values", "key", large).stratum
    )
    assert budget.estimated_bytes == 2_048


def test_refresh_updates_shallow_signal_without_extra_calibration() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000_000)
    initial = [1, 2, 3, 4, 5]
    refreshed = [1, 2, 3, 4, 5, 6, 7, 8]
    owner.store("values", "key", initial)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        return_value=1_024,
    ) as estimator:
        budget.track(owner, "values", "key", initial)
        initial_shallow = budget._entries[(id(owner), "values", "key")].shallow_bytes
        owner.store("values", "key", refreshed)
        budget.refresh(owner, "values", "key", refreshed)

    refreshed_entry = budget._entries[(id(owner), "values", "key")]
    assert estimator.call_count == 1
    assert refreshed_entry.shallow_bytes > initial_shallow
    assert budget._strata[cast(tuple, refreshed_entry.stratum)].admission_count == 2


def test_first_blocked_estimate_is_not_published_early() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    value = [1]
    owner.store("values", "key", value)
    estimator_started = Event()
    allow_estimator = Event()

    def blocking_estimator(
        key: object, value: object, *, stop_after: int | None
    ) -> int:
        estimator_started.set()
        assert allow_estimator.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return 1_024

    with mock.patch.object(
        run_context_lru, "estimate_cache_entry_size", blocking_estimator
    ):
        worker = Thread(target=budget.track, args=(owner, "values", "key", value))
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            assert (id(owner), "values", "key") not in budget._entries
            assert budget.estimated_bytes == 0
        finally:
            allow_estimator.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()


def test_zero_cap_rejects_without_blocked_estimate() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 0)
    value = [1]
    owner.store("values", "key", value)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=AssertionError("zero cap calibrated"),
    ):
        budget.track(owner, "values", "key", value)

    assert not owner.entries
    assert budget.estimated_bytes == 0


def test_calibrated_oversized_entry_is_not_retained() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 1_024)
    value = [bytearray(2_048)]
    owner.store("values", "key", value)

    with mock.patch.object(
        run_context_lru, "estimate_cache_entry_size", return_value=2_048
    ):
        budget.track(owner, "values", "key", value)

    assert not owner.entries
    assert not budget._entries
    assert budget.estimated_bytes == 0


def test_calibrated_estimator_exception_cleans_value_token_and_accounting() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    value = [1]
    owner.store("values", "key", value)

    with (
        mock.patch.object(
            run_context_lru,
            "estimate_cache_entry_size",
            side_effect=RuntimeError("estimation failed"),
        ),
        pytest.raises(RuntimeError, match="estimation failed"),
    ):
        budget.track(owner, "values", "key", value)

    assert not owner.entries
    assert not budget._entries
    assert not budget._entry_attempt_generations
    assert budget.estimated_bytes == 0


def test_due_estimator_failure_preserves_seeded_stratum_and_peer() -> None:
    budget = ProcessRunContextCacheBudget()
    peer_owner = CacheOwner()
    pending_owner = CacheOwner()
    state, peer_key = _seed_due_stratum_with_peer(budget, peer_owner, pending_owner)
    lifecycle_generation = budget._owner_lifecycle_generations.get(id(pending_owner))
    configuration_generation = budget._configuration_generation
    value = ["pending"]
    pending_owner.store("values", "pending", value)
    active_lifecycle_generations: list[int] = []

    def failing_estimator(key: object, value: object, *, stop_after: int | None) -> int:
        active_lifecycle_generations.append(
            budget._owner_lifecycle_generations[id(pending_owner)]
        )
        raise RuntimeError("estimation failed")  # noqa: TRY003

    with (
        mock.patch.object(
            run_context_lru,
            "estimate_cache_entry_size",
            side_effect=failing_estimator,
        ),
        pytest.raises(RuntimeError, match="estimation failed"),
    ):
        budget.track(pending_owner, "values", "pending", value)

    _assert_seeded_due_stratum_unchanged(budget, peer_owner, state, peer_key)
    assert ("values", "pending") not in pending_owner.entries
    assert not budget._entry_attempt_generations
    assert budget._configuration_generation == configuration_generation
    assert active_lifecycle_generations == [
        budget._owner_lifecycle_generations[id(pending_owner)]
    ]
    assert active_lifecycle_generations[0] != (lifecycle_generation or 0)


def test_due_sample_configuration_retry_failure_preserves_seeded_stratum() -> None:
    budget = ProcessRunContextCacheBudget()
    peer_owner = CacheOwner()
    pending_owner = CacheOwner()
    state, peer_key = _seed_due_stratum_with_peer(budget, peer_owner, pending_owner)
    first_generation = budget._configuration_generation
    value = ["pending"]
    pending_owner.store("values", "pending", value)
    estimator_started = Event()
    allow_first_estimator = Event()
    stop_after_values: list[int | None] = []
    lifecycle_generations: list[int] = []
    worker_errors: list[BaseException] = []

    def changing_configuration_estimator(
        key: object, value: object, *, stop_after: int | None
    ) -> int:
        stop_after_values.append(stop_after)
        lifecycle_generations.append(
            budget._owner_lifecycle_generations[id(pending_owner)]
        )
        if len(stop_after_values) == 1:
            estimator_started.set()
            assert allow_first_estimator.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            return 9_000
        raise RuntimeError("new-generation estimation failed")  # noqa: TRY003

    def track_entry() -> None:
        try:
            budget.track(pending_owner, "values", "pending", value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        changing_configuration_estimator,
    ):
        worker = Thread(target=track_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            with budget._lock:
                budget._max_bytes = 200_000
                budget._configuration_generation += 1
        finally:
            allow_first_estimator.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    assert len(worker_errors) == 1
    assert isinstance(worker_errors[0], RuntimeError)
    _assert_seeded_due_stratum_unchanged(budget, peer_owner, state, peer_key)
    assert stop_after_values == [100_000, 200_000]
    assert lifecycle_generations == [lifecycle_generations[0]] * 2
    assert budget._configuration_generation == first_generation + 1
    assert not budget._entry_attempt_generations


@pytest.mark.parametrize("invalidation", ["weak_finalization", "owner_reuse"])
def test_due_lifecycle_invalidation_preserves_seeded_stratum_and_peer(
    invalidation: str,
) -> None:
    budget = ProcessRunContextCacheBudget()
    peer_owner = CacheOwner()
    pending_owner = CacheOwner()
    state, peer_key = _seed_due_stratum_with_peer(budget, peer_owner, pending_owner)
    configuration_generation = budget._configuration_generation
    value = ["pending"]
    pending_owner.store("values", "pending", value)
    estimator_started = Event()
    allow_estimator = Event()
    worker_errors: list[BaseException] = []

    def blocking_estimator(
        key: object, value: object, *, stop_after: int | None
    ) -> int:
        estimator_started.set()
        assert allow_estimator.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return 2_000

    def track_entry() -> None:
        try:
            budget.track(pending_owner, "values", "pending", value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru, "estimate_cache_entry_size", blocking_estimator
    ):
        worker = Thread(target=track_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            pending_owner_id = id(pending_owner)
            lifecycle_generation = budget._owner_lifecycle_generations[pending_owner_id]
            with budget._lock:
                if invalidation == "weak_finalization":
                    owner_reference = budget._owner_references[pending_owner_id]
                    budget._owner_finalizer(pending_owner_id)(owner_reference)
                else:
                    replacement = CacheOwner()
                    budget._owner_references[pending_owner_id] = ref(replacement)
                    budget._owner_reference_locked(pending_owner)
        finally:
            allow_estimator.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    _assert_seeded_due_stratum_unchanged(budget, peer_owner, state, peer_key)
    assert not budget._entry_attempt_generations
    assert budget._configuration_generation == configuration_generation
    assert lifecycle_generation > 0
    assert id(pending_owner) not in budget._owner_lifecycle_generations


def test_concurrent_due_samples_preserve_seeded_window_and_count_both_entries() -> None:
    budget = ProcessRunContextCacheBudget()
    peer_owner = CacheOwner()
    first_owner = CacheOwner()
    state, peer_key = _seed_due_stratum_with_peer(budget, peer_owner, first_owner)
    second_owner = CacheOwner()
    budget.register(second_owner, 100_000)
    configuration_generation = budget._configuration_generation
    owners = {1: first_owner, 2: second_owner}
    values = {key: [key] for key in owners}
    estimator_started = {key: Event() for key in owners}
    allow_estimators = Event()
    worker_errors: list[BaseException] = []

    def blocking_estimator(
        key: object, value: object, *, stop_after: int | None
    ) -> int:
        typed_key = cast(int, key)
        estimator_started[typed_key].set()
        assert allow_estimators.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return 2_000

    def track_entry(key: int) -> None:
        try:
            budget.track(owners[key], "values", key, values[key])
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    for key, owner in owners.items():
        owner.store("values", key, values[key])
    with mock.patch.object(
        run_context_lru, "estimate_cache_entry_size", blocking_estimator
    ):
        workers = [Thread(target=track_entry, args=(key,)) for key in owners]
        for worker in workers:
            worker.start()
        try:
            assert all(
                started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
                for started in estimator_started.values()
            )
        finally:
            allow_estimators.set()
            for worker in workers:
                worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert all(not worker.is_alive() for worker in workers)
    if worker_errors:
        raise worker_errors[0]
    assert budget._strata == {SEEDED_DUE_STRATUM: state}
    assert list(state.samples) == [*SEEDED_CALIBRATION_WINDOW[1:], (92, 2_000)]
    assert state.admission_count == 257
    assert state.entry_count == 3
    assert state.shallow_total == 284
    assert set(budget._entries) == {
        peer_key,
        (id(first_owner), "values", 1),
        (id(second_owner), "values", 2),
    }
    assert not budget._entry_attempt_generations
    assert budget._configuration_generation == configuration_generation
    assert budget.estimated_bytes == 3_261


def test_stale_blocked_estimate_cannot_replace_newer_same_key_sample() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    old = ["old"]
    new = ["new"]
    owner.store("values", "key", old)
    estimator_started = Event()
    allow_old = Event()

    def delayed_estimator(key: object, value: object, *, stop_after: int | None) -> int:
        if value is old:
            estimator_started.set()
            assert allow_old.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            return 2_048
        assert value is new
        return 1_024

    with mock.patch.object(
        run_context_lru, "estimate_cache_entry_size", delayed_estimator
    ):
        worker = Thread(target=budget.track, args=(owner, "values", "key", old))
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            owner.store("values", "key", new)
            budget.track(owner, "values", "key", new)
        finally:
            allow_old.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    assert owner.entries[("values", "key")] is new
    entry = budget._entries[(id(owner), "values", "key")]
    assert (
        entry.shallow_bytes
        == run_context_lru._admission_signal("values", "key", new).shallow_bytes
    )
    assert list(budget._strata[cast(tuple, entry.stratum)].samples) == [
        (entry.shallow_bytes, 1_024)
    ]


@pytest.mark.parametrize("invalidation", ["weak_finalization", "owner_reuse"])
def test_blocked_estimate_lifecycle_invalidation_prevents_calibration(
    invalidation: str,
) -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    value = ["value"]
    owner.store("values", "key", value)
    estimator_started = Event()
    allow_estimator = Event()
    worker_errors: list[BaseException] = []

    def blocking_estimator(
        key: object, value: object, *, stop_after: int | None
    ) -> int:
        estimator_started.set()
        assert allow_estimator.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return 1_024

    def track_entry() -> None:
        try:
            budget.track(owner, "values", "key", value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru, "estimate_cache_entry_size", blocking_estimator
    ):
        worker = Thread(target=track_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            owner_id = id(owner)
            with budget._lock:
                if invalidation == "weak_finalization":
                    owner_reference = budget._owner_references[owner_id]
                    budget._owner_finalizer(owner_id)(owner_reference)
                else:
                    replacement = CacheOwner()
                    budget._owner_references[owner_id] = ref(replacement)
                    budget._owner_reference_locked(owner)
        finally:
            allow_estimator.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    assert not budget._strata
    assert not budget._entries
    assert not budget._entry_attempt_generations


def test_stale_configuration_sample_never_enters_new_generation_window() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    first_limit = 10_000
    second_limit = 20_000
    budget.register(owner, first_limit)
    value = ["value"]
    owner.store("values", "key", value)
    estimator_started = Event()
    allow_first_estimate = Event()
    deep_results = iter((9_000, 1_024))

    def changing_generation_estimator(
        key: object, value: object, *, stop_after: int | None
    ) -> int:
        result = next(deep_results)
        if result == 9_000:
            assert stop_after == first_limit
            estimator_started.set()
            assert allow_first_estimate.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        else:
            assert stop_after == second_limit
        return result

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        changing_generation_estimator,
    ):
        worker = Thread(target=budget.track, args=(owner, "values", "key", value))
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            with budget._lock:
                budget._max_bytes = second_limit
                budget._configuration_generation += 1
        finally:
            allow_first_estimate.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    entry = budget._entries[(id(owner), "values", "key")]
    assert list(budget._strata[cast(tuple, entry.stratum)].samples) == [
        (entry.shallow_bytes, 1_024)
    ]


def test_setting_change_to_zero_invalidates_blocked_estimate_without_recalibration() -> (
    None
):
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    value = ["value"]
    owner.store("values", "key", value)
    estimator_started = Event()
    allow_estimator = Event()
    estimator_calls = 0
    worker_errors: list[BaseException] = []

    def blocking_estimator(
        key: object, value: object, *, stop_after: int | None
    ) -> int:
        nonlocal estimator_calls
        estimator_calls += 1
        if estimator_calls > 1:
            raise AssertionError("zero-cap transition recalibrated")  # noqa: TRY003
        estimator_started.set()
        assert allow_estimator.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return 1_024

    def track_entry() -> None:
        try:
            budget.track(owner, "values", "key", value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru, "estimate_cache_entry_size", blocking_estimator
    ):
        worker = Thread(target=track_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            budget.register(owner, 0)
        finally:
            allow_estimator.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    assert estimator_calls == 1
    assert not owner.entries
    assert not budget._entry_attempt_generations
    assert not budget._strata


@pytest.mark.parametrize("configured", [None, 0, 1, 1024])
def test_resolve_run_context_cache_max_bytes_accepts_supported_values(
    configured: int | None,
) -> None:
    with override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": configured}):
        assert resolve_run_context_cache_max_bytes() == configured


@override_settings(GENERAL_MANAGER={})
def test_resolve_run_context_cache_max_bytes_defaults_to_unlimited() -> None:
    assert resolve_run_context_cache_max_bytes() is None


@pytest.mark.parametrize("configured", [-1, True, False, 1.5, "1024"])
def test_resolve_run_context_cache_max_bytes_rejects_invalid_values(
    configured: object,
) -> None:
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": configured}),
        pytest.raises(ImproperlyConfigured, match="RUN_CONTEXT_CACHE_MAX_BYTES"),
    ):
        resolve_run_context_cache_max_bytes()


def test_estimate_cache_entry_size_handles_cycles() -> None:
    value: list[object] = []
    value.append(value)

    size = estimate_cache_entry_size("cycle", value, stop_after=None)

    assert size >= MIN_TRACKED_ENTRY_BYTES


@pytest.mark.parametrize("stop_after", [None, 10**400])
def test_estimate_cache_entry_size_handles_sampled_sequence_cycles(
    stop_after: int | None,
) -> None:
    value: list[object] = [None] * 2_000
    value[-1] = value

    size = estimate_cache_entry_size("cycle", value, stop_after=stop_after)

    assert MIN_TRACKED_ENTRY_BYTES <= size
    if stop_after is not None:
        assert size <= stop_after


@pytest.mark.parametrize("stop_after", [None, 10**400])
def test_estimate_cache_entry_size_handles_mutually_cyclic_sampled_sequences(
    stop_after: int | None,
) -> None:
    first: list[object] = [None] * 200
    second: list[object] = [None] * 200
    first[-1] = second
    second[-1] = first
    visited: list[object] = []

    with mock.patch.object(
        run_context_lru,
        "_calibration_visit_observer",
        visited.append,
    ):
        size = estimate_cache_entry_size("cycle", first, stop_after=stop_after)

    assert MIN_TRACKED_ENTRY_BYTES <= size
    assert len(visited) < run_context_lru.RUN_CONTEXT_CALIBRATION_CANDIDATE_LIMIT
    if stop_after is not None:
        assert size <= stop_after


def test_estimate_cache_entry_size_handles_deep_sampled_identity_sharing() -> None:
    value: object = None
    for _ in range(1_200):
        value = [value] * 129
    stop_after = 10**400

    size = estimate_cache_entry_size("deep", value, stop_after=stop_after)

    assert MIN_TRACKED_ENTRY_BYTES <= size <= stop_after


@pytest.mark.parametrize("stop_after", [None, 10**400])
def test_estimate_cache_entry_size_handles_sampled_mapping_cycles(
    stop_after: int | None,
) -> None:
    value: dict[int, object] = {index: None for index in range(2_000)}
    value[max(value)] = value

    size = estimate_cache_entry_size("cycle", value, stop_after=stop_after)

    assert MIN_TRACKED_ENTRY_BYTES <= size
    if stop_after is not None:
        assert size <= stop_after


@pytest.mark.parametrize(
    "value",
    [None, False, 1, 1.5, 2j, "value", b"value", bytearray(b"value"), range(3)],
)
def test_estimate_cache_entry_size_does_not_inspect_atomic_leaf_metadata(
    value: object,
) -> None:
    with mock.patch(
        "general_manager.cache.run_context_lru._get_static_type_mro",
        side_effect=AssertionError("atomic leaves must not inspect type metadata"),
    ):
        size = estimate_cache_entry_size("key", value, stop_after=None)

    assert size >= MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_counts_same_atomic_object_once() -> None:
    value = b"x" * 1_024

    size = estimate_cache_entry_size(value, value, stop_after=None)

    assert size == sys.getsizeof(value)


def test_estimate_cache_entry_size_stops_after_atomic_value_exceeds_budget() -> None:
    value = b"x" * 1_024
    with mock.patch(
        "general_manager.cache.run_context_lru.sys.getsizeof",
        wraps=sys.getsizeof,
    ) as getsizeof:
        size = estimate_cache_entry_size("key", value, stop_after=1)

    assert size == 2
    getsizeof.assert_called_once_with(value)


# Mutation caught: calling application sizing or length hooks during admission.
def test_admission_signal_does_not_execute_application_hooks() -> None:
    class Value:
        __slots__ = ("payload",)

        def __init__(self) -> None:
            self.payload = bytearray(8_192)

        def __sizeof__(self) -> int:
            raise AssertionError("application __sizeof__ must not execute")  # noqa: TRY003

        def __len__(self) -> int:
            raise AssertionError("application __len__ must not execute")  # noqa: TRY003

    signal = run_context_lru._admission_signal("values", "key", Value())

    assert signal.exact_bytes is None
    assert signal.stratum == ("values", "slots", 1)
    assert 1 <= signal.shallow_bytes < 8_192


# Mutation caught: bucketing distinct native container sizes together.
def test_admission_signal_uses_exact_builtin_length_bucket() -> None:
    short = run_context_lru._admission_signal("values", "key", [None] * 65)
    long = run_context_lru._admission_signal("values", "key", [None] * 129)

    assert short.stratum == ("values", "list", 128)
    assert long.stratum == ("values", "list", 256)


# Mutation caught: routing atomic entries through calibrated storage strata.
def test_admission_signal_keeps_atomic_entry_exact() -> None:
    signal = run_context_lru._admission_signal("values", "key", b"payload")

    assert signal.stratum is None
    assert signal.exact_bytes == max(
        MIN_TRACKED_ENTRY_BYTES,
        sys.getsizeof("key") + sys.getsizeof(b"payload"),
    )


# Mutation caught: treating hostile builtin subclasses as their native bases.
@pytest.mark.parametrize("container_type", [dict, list, str])
def test_admission_signal_does_not_execute_hostile_builtin_subclass_hooks(
    container_type: type[object],
) -> None:
    class HostileContainer(container_type):  # type: ignore[misc, valid-type]
        def __len__(self) -> int:
            raise AssertionError("application __len__ must not execute")  # noqa: TRY003

        def __sizeof__(self) -> int:
            raise AssertionError("application __sizeof__ must not execute")  # noqa: TRY003

        def __eq__(self, other: object) -> bool:
            raise AssertionError("application equality must not execute")  # noqa: TRY003

        @property
        def __class__(self) -> type[object]:
            raise AssertionError("application metadata must not execute")  # noqa: TRY003

    if container_type is dict:
        value = HostileContainer({"payload": bytearray(64)})
    elif container_type is list:
        value = HostileContainer([bytearray(64)])
    else:
        value = HostileContainer("payload")

    signal = run_context_lru._admission_signal("values", "key", value)

    assert signal.exact_bytes is None
    assert signal.stratum is not None
    assert signal.stratum[1] not in {"dict", "list", "shallow_leaf"}
    assert signal.shallow_bytes >= 1


# Mutation caught: reading instance dictionary values instead of native storage metadata.
def test_admission_signal_buckets_genuine_instance_dict_without_reading_values() -> (
    None
):
    class Value:
        pass

    one_attribute = Value()
    one_attribute.first = object()
    three_attributes = Value()
    three_attributes.first = object()
    three_attributes.second = object()
    three_attributes.third = object()

    one_signal = run_context_lru._admission_signal("values", "key", one_attribute)
    three_signal = run_context_lru._admission_signal("values", "key", three_attributes)

    assert one_signal.stratum == ("values", "instance_dict", 1)
    assert three_signal.stratum == ("values", "instance_dict", 4)


# Mutation caught: invoking an object's overridable __sizeof__ during estimation.
def test_estimator_does_not_execute_application_sizing_hook() -> None:
    class Value:
        __slots__ = ("payload",)

        def __init__(self) -> None:
            self.payload = bytearray(1_024)

        def __sizeof__(self) -> int:
            return 1

    value = Value()
    size = estimate_cache_entry_size("key", value, stop_after=None)

    assert size == (
        object.__sizeof__(value) + sys.getsizeof("key") + sys.getsizeof(value.payload)
    )


def test_estimate_cache_entry_size_counts_shared_object_once_per_entry() -> None:
    shared = [bytearray(1024)]

    shared_size = estimate_cache_entry_size("key", [shared, shared], stop_after=None)
    copied_size = estimate_cache_entry_size(
        "key", [[bytearray(1024)], [bytearray(1024)]], stop_after=None
    )

    assert shared_size < copied_size


def test_estimate_cache_entry_size_reuses_storage_plan_for_same_type() -> None:
    class Value:
        __slots__ = ("payload",)

        def __init__(self, payload: object) -> None:
            self.payload = payload

    values = [Value(bytearray(64)) for _ in range(20)]
    original = run_context_lru._get_static_class_metadata
    with mock.patch(
        "general_manager.cache.run_context_lru._get_static_class_metadata",
        wraps=original,
    ) as get_metadata:
        size = estimate_cache_entry_size("key", values, stop_after=None)

    assert size > MIN_TRACKED_ENTRY_BYTES
    assert get_metadata.call_count == 1


@pytest.mark.parametrize("container_type", [list, tuple, set, frozenset])
def test_estimate_cache_entry_size_samples_large_uniform_container_with_margin(
    container_type: object,
) -> None:
    key = "key"
    if container_type in (list, tuple):
        items: list[object] = [bytearray(64) for _ in range(2_000)]
    else:
        items = [f"{index:06}".encode().ljust(64, b"x") for index in range(2_000)]
    value = cast(type[object], container_type)(items)
    exact = (
        sys.getsizeof(key)
        + sys.getsizeof(value)
        + sum(sys.getsizeof(item) for item in cast(Iterable[object], value))
    )

    estimated = estimate_cache_entry_size(key, value, stop_after=None)

    assert exact <= estimated <= math.ceil(exact * 1.06)


def test_estimate_cache_entry_size_samples_large_uniform_mapping_with_margin() -> None:
    key = "key"
    value = {f"key-{index:04}": bytearray(64) for index in range(2_000)}
    exact = (
        sys.getsizeof(key)
        + sys.getsizeof(value)
        + sum(
            sys.getsizeof(item_key) + sys.getsizeof(item_value)
            for item_key, item_value in value.items()
        )
    )

    estimated = estimate_cache_entry_size(key, value, stop_after=None)

    assert exact <= estimated <= math.ceil(exact * 1.06)


# Mutation caught: allowing nested sampled candidates to grow without a global bound.
def test_estimator_bounds_nested_container_candidate_visits() -> None:
    value: object = bytearray(64)
    for _ in range(40):
        value = [value for _ in range(129)]
    visited: list[object] = []

    with mock.patch.object(
        run_context_lru,
        "_calibration_visit_observer",
        visited.append,
    ):
        size = estimate_cache_entry_size("key", value, stop_after=10**400)

    assert MIN_TRACKED_ENTRY_BYTES <= size <= 10**400
    assert len(visited) <= run_context_lru.RUN_CONTEXT_CALIBRATION_CANDIDATE_LIMIT


# Mutation caught: using floor projection that underestimates uniform samples.
def test_estimator_fixed_point_projection_is_within_uniform_margin() -> None:
    value = [bytearray(64) for _ in range(2_000)]
    exact = (
        sys.getsizeof("key")
        + sys.getsizeof(value)
        + sum(sys.getsizeof(item) for item in value)
    )

    estimated = estimate_cache_entry_size("key", value, stop_after=None)

    assert exact <= estimated <= math.ceil(exact * 1.06)


def test_estimate_cache_entry_size_samples_mapping_without_rehashing_keys() -> None:
    class Key:
        def __hash__(self) -> int:
            return id(self)

    value = {Key(): bytearray(64) for _ in range(129)}

    def raise_if_rehashed(self: Key) -> int:
        raise AssertionError("sampled keys must not be rehashed")  # noqa: TRY003

    Key.__hash__ = raise_if_rehashed

    size = estimate_cache_entry_size("key", value, stop_after=None)

    assert size > MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_samples_large_sequence_with_bounded_work() -> None:
    value = list(range(10_000))
    with mock.patch(
        "general_manager.cache.run_context_lru.sys.getsizeof",
        wraps=sys.getsizeof,
    ) as getsizeof:
        estimate_cache_entry_size("key", value, stop_after=None)

    assert getsizeof.call_count <= 64 + 2


def test_estimate_cache_entry_size_traverses_sequence_at_exact_sample_threshold() -> (
    None
):
    value = [bytearray(1) for _ in range(128)]
    with mock.patch(
        "general_manager.cache.run_context_lru.sys.getsizeof",
        wraps=sys.getsizeof,
    ) as getsizeof:
        estimate_cache_entry_size("key", value, stop_after=None)

    assert getsizeof.call_count == 128 + 2


def test_estimate_cache_entry_size_samples_sequence_above_threshold() -> None:
    value = [bytearray(1) for _ in range(129)]
    with mock.patch(
        "general_manager.cache.run_context_lru.sys.getsizeof",
        wraps=sys.getsizeof,
    ) as getsizeof:
        estimate_cache_entry_size("key", value, stop_after=None)

    assert getsizeof.call_count == 64 + 2


def test_estimate_cache_entry_size_traverses_mapping_at_exact_sample_threshold() -> (
    None
):
    value = {f"item-{index}".encode(): bytearray(1) for index in range(128)}
    with mock.patch(
        "general_manager.cache.run_context_lru.sys.getsizeof",
        wraps=sys.getsizeof,
    ) as getsizeof:
        estimate_cache_entry_size("key", value, stop_after=None)

    assert getsizeof.call_count == (128 * 2) + 2


def test_estimate_cache_entry_size_samples_mapping_above_threshold() -> None:
    value = {f"item-{index}".encode(): bytearray(1) for index in range(129)}
    with mock.patch(
        "general_manager.cache.run_context_lru.sys.getsizeof",
        wraps=sys.getsizeof,
    ) as getsizeof:
        estimate_cache_entry_size("key", value, stop_after=None)

    assert getsizeof.call_count == (64 * 2) + 2


def test_estimate_cache_entry_size_traverses_set_at_exact_sample_threshold() -> None:
    value = {f"item-{index}".encode() for index in range(128)}
    with mock.patch(
        "general_manager.cache.run_context_lru.sys.getsizeof",
        wraps=sys.getsizeof,
    ) as getsizeof:
        estimate_cache_entry_size("key", value, stop_after=None)

    assert getsizeof.call_count == 128 + 2


def test_estimate_cache_entry_size_samples_set_above_threshold() -> None:
    value = {f"item-{index}".encode() for index in range(129)}
    with mock.patch(
        "general_manager.cache.run_context_lru.sys.getsizeof",
        wraps=sys.getsizeof,
    ) as getsizeof:
        estimate_cache_entry_size("key", value, stop_after=None)

    assert getsizeof.call_count == 64 + 2


def test_estimate_cache_entry_size_stratifies_large_sequence_samples() -> None:
    small = [bytearray(1) for _ in range(2_001)]
    large = [bytearray(1) for _ in range(2_001)]
    for index in (0, len(large) // 2, len(large) - 1):
        large[index] = bytearray(4_096)

    small_estimate = estimate_cache_entry_size("key", small, stop_after=None)
    large_estimate = estimate_cache_entry_size("key", large, stop_after=None)

    assert large_estimate > small_estimate


def test_stratified_indexes_returns_one_representative() -> None:
    assert _stratified_indexes(129, 1) == (0,)


def test_estimate_cache_entry_size_stops_after_budget() -> None:
    size = estimate_cache_entry_size(
        "key",
        [b"x" * 1024 for _ in range(100)],
        stop_after=512,
    )

    assert size == 513


def test_estimate_cache_entry_size_traverses_genuine_instance_dictionaries() -> None:
    class Value:
        def __init__(self, payload: object) -> None:
            self.payload = payload

    empty_size = estimate_cache_entry_size("key", Value(None), stop_after=None)
    payload_size = estimate_cache_entry_size(
        "key", Value(bytearray(1024)), stop_after=None
    )

    assert empty_size < payload_size


def test_estimate_cache_entry_size_traverses_list_declared_slots() -> None:
    class SlotValue:
        __slots__ = ["payload"]

        def __init__(self, payload: object) -> None:
            self.payload = payload

    empty_size = estimate_cache_entry_size("key", SlotValue(None), stop_after=None)
    payload_size = estimate_cache_entry_size(
        "key", SlotValue(bytearray(1024)), stop_after=None
    )

    assert empty_size < payload_size


def test_estimate_cache_entry_size_traverses_private_slots() -> None:
    class PrivateSlotValue:
        __slots__ = ("__payload",)

        def __init__(self, payload: object) -> None:
            self.__payload = payload

    empty_size = estimate_cache_entry_size(
        "key", PrivateSlotValue(None), stop_after=None
    )
    payload_size = estimate_cache_entry_size(
        "key", PrivateSlotValue(bytearray(1024)), stop_after=None
    )

    assert empty_size < payload_size


def test_estimate_cache_entry_size_counts_remaining_native_alias_once() -> None:
    class Value:
        __slots__ = ("_Renamed__payload", "__payload")

        def __init__(self, alias_payload: object) -> None:
            self.__payload = None
            self._Renamed__payload = alias_payload

    alias_payload = bytearray(1024)
    value = Value(alias_payload)
    Value._Value__payload = None
    Value.__slots__ = ()
    Value.__name__ = "Renamed"

    size = estimate_cache_entry_size("key", value, stop_after=None)

    assert size == (
        object.__sizeof__(value) + sys.getsizeof("key") + sys.getsizeof(alias_payload)
    )


def test_estimate_cache_entry_size_ignores_class_name_and_qualname_for_slots() -> None:
    class Value:
        __qualname__ = "Renamed"
        __slots__ = ("_Renamed__payload", "__payload")

        def __init__(self) -> None:
            self.__payload = bytearray(1024)
            self._Renamed__payload = None

    value = Value()
    Value.__slots__ = ()
    Value.__name__ = "Renamed"

    size = estimate_cache_entry_size("key", value, stop_after=None)

    assert size > MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_traverses_all_same_suffix_private_slots() -> None:
    class Value:
        __slots__ = ("_Other__payload", "__payload")

        def __init__(self, private_payload: object, other_payload: object) -> None:
            self.__payload = private_payload
            self._Other__payload = other_payload

    empty_size = estimate_cache_entry_size("key", Value(None, None), stop_after=None)
    private_size = estimate_cache_entry_size(
        "key",
        Value(bytearray(1024), None),
        stop_after=None,
    )
    other_size = estimate_cache_entry_size(
        "key",
        Value(None, bytearray(1024)),
        stop_after=None,
    )
    both_size = estimate_cache_entry_size(
        "key",
        Value(bytearray(1024), bytearray(1024)),
        stop_after=None,
    )

    assert empty_size < private_size
    assert empty_size < other_size
    assert private_size < both_size
    assert other_size < both_size


def test_estimate_cache_entry_size_traverses_dotted_class_private_slots() -> None:
    value_type = type("A.B", (), {"__slots__": ("__payload",)})
    empty = value_type()
    payload = value_type()
    setattr(empty, "_A.B__payload", None)
    setattr(payload, "_A.B__payload", bytearray(1024))

    empty_size = estimate_cache_entry_size("key", empty, stop_after=None)
    payload_size = estimate_cache_entry_size("key", payload, stop_after=None)

    assert empty_size < payload_size


def test_estimate_cache_entry_size_avoids_metaclass_name_descriptor() -> None:
    metadata_accesses: list[str] = []

    class HostileNameDescriptor:
        def __get__(self, instance: object, owner: object = None) -> object:
            metadata_accesses.append("__name__")
            raise AssertionError(  # noqa: TRY003 - test double reports the hook.
                "unexpected metaclass descriptor lookup: __name__"
            )

        def __set__(self, instance: object, value: object) -> None:
            raise AssertionError(  # noqa: TRY003 - test double reports the hook.
                "unexpected metaclass descriptor assignment: __name__"
            )

    class HostileMeta(type):
        __name__ = HostileNameDescriptor()

    class Value(metaclass=HostileMeta):
        __slots__ = ("__payload",)

        def __init__(self) -> None:
            self.__payload = bytearray(1024)

    size = estimate_cache_entry_size("key", Value(), stop_after=None)

    assert size > MIN_TRACKED_ENTRY_BYTES
    assert metadata_accesses == []


def test_estimate_cache_entry_size_treats_module_subclasses_as_shallow_leaves() -> None:
    class CustomModule(ModuleType):
        pass

    module = CustomModule("custom")
    module.payload = bytearray(1024)

    size = estimate_cache_entry_size("key", module, stop_after=None)

    assert size == MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_avoids_custom_metaclass_metadata_hooks() -> None:
    metadata_accesses: list[str] = []

    class HostileMeta(type):
        def __getattribute__(cls, name: str) -> object:
            if name in {"__mro__", "__dict__"}:
                metadata_accesses.append(name)
                raise AssertionError(  # noqa: TRY003 - test double reports the hook.
                    f"unexpected metaclass lookup: {name}"
                )
            return super().__getattribute__(name)

    class Value(metaclass=HostileMeta):
        __slots__ = ("payload",)

        def __init__(self) -> None:
            self.payload = bytearray(1024)

    size = estimate_cache_entry_size("key", Value(), stop_after=None)

    assert size > MIN_TRACKED_ENTRY_BYTES
    assert metadata_accesses == []


def test_estimate_cache_entry_size_avoids_custom_metaclass_hash_hook() -> None:
    class HostileMeta(type):
        def __hash__(cls) -> int:
            raise AssertionError("unexpected metaclass hash")  # noqa: TRY003

    class Value(metaclass=HostileMeta):
        __slots__ = ("payload",)

        def __init__(self) -> None:
            self.payload = bytearray(1024)

    size = estimate_cache_entry_size("key", Value(), stop_after=None)

    assert size > MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_avoids_custom_metaclass_equality_hook() -> None:
    class HostileMeta(type):
        def __eq__(cls, other: object) -> bool:
            raise AssertionError("unexpected metaclass equality")  # noqa: TRY003

    class Value(metaclass=HostileMeta):
        __slots__ = ("payload",)

        def __init__(self) -> None:
            self.payload = bytearray(1024)

    size = estimate_cache_entry_size("key", Value(), stop_after=None)

    assert size > MIN_TRACKED_ENTRY_BYTES


@pytest.mark.parametrize("metadata_name", ["__mro__", "__dict__"])
def test_estimate_cache_entry_size_avoids_metaclass_metadata_data_descriptors(
    metadata_name: str,
) -> None:
    metadata_accesses: list[str] = []

    class HostileMetadataDescriptor:
        def __get__(self, instance: object, owner: object = None) -> object:
            metadata_accesses.append(metadata_name)
            raise AssertionError(  # noqa: TRY003 - test double reports the hook.
                f"unexpected metaclass descriptor lookup: {metadata_name}"
            )

        def __set__(self, instance: object, value: object) -> None:
            raise AssertionError(  # noqa: TRY003 - test double reports the hook.
                f"unexpected metaclass descriptor assignment: {metadata_name}"
            )

    hostile_meta = type(
        "HostileMeta",
        (type,),
        {metadata_name: HostileMetadataDescriptor()},
    )

    class Value(metaclass=hostile_meta):
        __slots__ = ()

    size = estimate_cache_entry_size("key", Value(), stop_after=None)

    assert size == MIN_TRACKED_ENTRY_BYTES
    assert metadata_accesses == []


def test_estimate_cache_entry_size_ignores_unrelated_native_dict_descriptor() -> None:
    class Value:
        __dict__ = object.__dict__["__class__"]

    size = estimate_cache_entry_size("key", Value(), stop_after=None)

    assert size == MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_ignores_member_descriptor_from_base_slot() -> None:
    class Base:
        __slots__ = ("base_payload",)

    class Value(Base):
        __slots__ = ("payload",)

    value = Value()
    value.base_payload = bytearray(1024)
    value.payload = None
    base_payload_descriptor = Base.__dict__["base_payload"]
    Value.payload = base_payload_descriptor
    Base.base_payload = None

    size = estimate_cache_entry_size("key", value, stop_after=None)

    assert size == MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_ignores_hostile_mutated_slot_metadata() -> None:
    class HostileSlot:
        def __hash__(self) -> int:
            raise AssertionError("unexpected slot hash")  # noqa: TRY003

        def startswith(self, prefix: str) -> bool:
            raise AssertionError("unexpected slot string lookup")  # noqa: TRY003

        def endswith(self, suffix: str) -> bool:
            raise AssertionError("unexpected slot string lookup")  # noqa: TRY003

    class Value:
        __slots__ = ["payload"]

        def __init__(self) -> None:
            self.payload = bytearray(1024)

    Value.__slots__.append(HostileSlot())

    size = estimate_cache_entry_size("key", Value(), stop_after=None)

    assert size > MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_ignores_hostile_string_slot_subclasses() -> None:
    class HostileSlotName(str):
        def __hash__(self) -> int:
            raise AssertionError("unexpected slot hash")  # noqa: TRY003

        def startswith(self, prefix: str) -> bool:
            raise AssertionError("unexpected slot string lookup")  # noqa: TRY003

        def endswith(self, suffix: str) -> bool:
            raise AssertionError("unexpected slot string lookup")  # noqa: TRY003

    class Value:
        __slots__ = ["payload"]

        def __init__(self) -> None:
            self.payload = bytearray(1024)

    Value.__slots__.append(HostileSlotName("hostile"))

    size = estimate_cache_entry_size("key", Value(), stop_after=None)

    assert size > MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_ignores_hostile_slot_list_subclasses() -> None:
    class HostileSlots(list[str]):
        def __iter__(self) -> object:
            raise AssertionError("unexpected slot iteration")  # noqa: TRY003

    class Value:
        __slots__ = ["payload"]

        def __init__(self) -> None:
            self.payload = bytearray(1024)

    Value.__slots__ = HostileSlots(["payload"])

    size = estimate_cache_entry_size("key", Value(), stop_after=None)

    assert size > MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_traverses_many_slots_without_slot_metadata() -> None:
    slot_names = tuple(f"payload_{index}" for index in range(64))
    value_type = type("ManySlots", (), {"__slots__": slot_names})
    value = value_type()
    payloads = [bytearray(32) for _ in slot_names]
    for slot_name, payload in zip(slot_names, payloads, strict=True):
        setattr(value, slot_name, payload)
    value_type.__slots__ = None

    size = estimate_cache_entry_size("key", value, stop_after=None)

    assert size > sum(sys.getsizeof(payload) for payload in payloads)


def test_unlimited_budget_operations_do_not_enter_lock_or_hash_key() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    key = CountingHashKey()
    budget._lock = RaisingLock()

    budget.touch(owner, "values", key)
    budget.track(owner, "values", key, "value")
    budget.remove(owner, "values", key)
    budget.refresh(owner, "values", key, "value")

    assert key.hash_calls == 0


def test_estimation_does_not_hold_coordinator_lock() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "initial", "value")
    budget.track(owner, "values", "initial", "value")

    estimator_started = Event()
    allow_estimator_to_finish = Event()
    worker_errors: list[BaseException] = []
    reader_errors: list[BaseException] = []

    blocked_value = ["value"]

    def blocking_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        assert key == "blocked"
        assert value is blocked_value
        assert stop_after == 10_000
        estimator_started.set()
        assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return MIN_TRACKED_ENTRY_BYTES

    def track_entry() -> None:
        try:
            budget.track(owner, "values", "blocked", blocked_value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    read_completed = Event()

    def read_budget() -> None:
        try:
            _ = budget.estimated_bytes
        except BaseException as error:  # noqa: BLE001
            reader_errors.append(error)
        finally:
            read_completed.set()

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=blocking_estimator,
    ):
        worker = Thread(target=track_entry)
        reader = Thread(target=read_budget)
        reader_started = False
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            reader.start()
            reader_started = True
            assert read_completed.wait(timeout=HANDOFF_TIMEOUT_SECONDS), (
                "estimation held the coordinator lock"
            )
        finally:
            allow_estimator_to_finish.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)
            if reader_started:
                reader.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if reader_started:
        assert not reader.is_alive()
    if worker_errors:
        raise worker_errors[0]
    if reader_errors:
        raise reader_errors[0]


def test_track_retries_estimation_after_limit_change() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    old_limit = 10_000
    new_limit = MIN_TRACKED_ENTRY_BYTES * 2
    budget.register(owner, old_limit)
    value = ["value"]
    owner.store("values", "key", value)

    estimator_started = Event()
    allow_estimator_to_finish = Event()
    stop_after_values: list[int | None] = []
    worker_errors: list[BaseException] = []

    def blocking_first_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        assert key == "key"
        assert value == ["value"]
        stop_after_values.append(stop_after)
        if len(stop_after_values) == 1:
            estimator_started.set()
            assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return MIN_TRACKED_ENTRY_BYTES

    def track_entry() -> None:
        try:
            budget.track(owner, "values", "key", value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=blocking_first_estimator,
    ):
        worker = Thread(target=track_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            with budget._lock:
                budget._max_bytes = new_limit
                budget._configuration_generation += 1
            allow_estimator_to_finish.set()
        finally:
            allow_estimator_to_finish.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    assert stop_after_values == [old_limit, new_limit]
    assert owner.entries[("values", "key")] == ["value"]
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES


def test_track_abandons_admission_during_continuous_limit_changes() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    value = ["value"]
    owner.store("values", "key", value)
    estimator_calls = 0

    def changing_configuration_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        nonlocal estimator_calls
        assert key == "key"
        assert value == ["value"]
        assert stop_after is not None
        estimator_calls += 1
        if estimator_calls <= 100:
            with budget._lock:
                assert budget._max_bytes is not None
                budget._max_bytes += 1
                budget._configuration_generation += 1
        return MIN_TRACKED_ENTRY_BYTES

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=changing_configuration_estimator,
    ):
        budget.track(owner, "values", "key", value)

    assert estimator_calls == 4
    assert ("values", "key") not in owner.entries
    assert (id(owner), "values", "key") not in budget._entry_attempt_generations
    assert budget.estimated_bytes == 0


def test_track_discards_current_entry_after_estimator_exception() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "key", "old")
    budget.track(owner, "values", "key", "old")
    new_value = ["new"]
    owner.store("values", "key", new_value)
    tracked_key = (id(owner), "values", "key")

    with (
        mock.patch.object(
            run_context_lru,
            "estimate_cache_entry_size",
            side_effect=RuntimeError("estimation failed"),
        ),
        pytest.raises(RuntimeError, match="estimation failed"),
    ):
        budget.track(owner, "values", "key", new_value)

    assert ("values", "key") not in owner.entries
    assert tracked_key not in budget._entries
    assert tracked_key not in budget._entry_attempt_generations
    assert budget.estimated_bytes == 0


def test_failed_track_does_not_evict_newer_same_key_replacement() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    old_value = ["old"]
    owner.store("values", "key", old_value)

    estimator_started = Event()
    allow_estimator_to_fail = Event()
    worker_errors: list[BaseException] = []

    def delayed_old_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        assert key == "key"
        assert stop_after == 10_000
        if value is old_value:
            estimator_started.set()
            assert allow_estimator_to_fail.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            raise RuntimeError
        raise AssertionError("new atomic replacement was estimated")  # noqa: TRY003

    def track_old_entry() -> None:
        try:
            budget.track(owner, "values", "key", old_value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=delayed_old_estimator,
    ):
        worker = Thread(target=track_old_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            owner.store("values", "key", "new")
            budget.track(owner, "values", "key", "new")
        finally:
            allow_estimator_to_fail.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    assert len(worker_errors) == 1
    assert isinstance(worker_errors[0], RuntimeError)
    assert owner.entries[("values", "key")] == "new"
    assert budget._entries[(id(owner), "values", "key")].exact_bytes == (
        MIN_TRACKED_ENTRY_BYTES
    )
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES


@pytest.mark.parametrize("mutation", ["remove", "clear_context"])
def test_track_does_not_reintroduce_accounting_after_owner_mutation(
    mutation: str,
) -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    value = ["value"]
    owner.store("values", "key", value)

    estimator_started = Event()
    allow_estimator_to_finish = Event()
    worker_errors: list[BaseException] = []

    def blocking_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        assert key == "key"
        assert value == ["value"]
        assert stop_after == 10_000
        estimator_started.set()
        assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return MIN_TRACKED_ENTRY_BYTES

    def track_entry() -> None:
        try:
            budget.track(owner, "values", "key", value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=blocking_estimator,
    ):
        worker = Thread(target=track_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            if mutation == "remove":
                owner._evict_run_cache_entry("values", "key")
                budget.remove(owner, "values", "key")
            else:
                budget.clear_context(owner)
            allow_estimator_to_finish.set()
        finally:
            allow_estimator_to_finish.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    assert budget.estimated_bytes == 0


@pytest.mark.parametrize("mutation", ["remove", "clear_context"])
def test_due_sample_mutation_preserves_seeded_stratum_and_peer(
    mutation: str,
) -> None:
    budget = ProcessRunContextCacheBudget()
    peer_owner = CacheOwner()
    pending_owner = CacheOwner()
    state, peer_key = _seed_due_stratum_with_peer(budget, peer_owner, pending_owner)
    configuration_generation = budget._configuration_generation
    value = ["pending"]
    pending_owner.store("values", "pending", value)
    estimator_started = Event()
    allow_estimator = Event()
    worker_errors: list[BaseException] = []

    def blocking_estimator(
        key: object, value: object, *, stop_after: int | None
    ) -> int:
        estimator_started.set()
        assert allow_estimator.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return 2_000

    def track_entry() -> None:
        try:
            budget.track(pending_owner, "values", "pending", value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru, "estimate_cache_entry_size", blocking_estimator
    ):
        worker = Thread(target=track_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            lifecycle_generation = budget._owner_lifecycle_generations[
                id(pending_owner)
            ]
            if mutation == "remove":
                pending_owner._evict_run_cache_entry("values", "pending")
                budget.remove(pending_owner, "values", "pending")
            else:
                budget.clear_context(pending_owner)
        finally:
            allow_estimator.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    _assert_seeded_due_stratum_unchanged(budget, peer_owner, state, peer_key)
    assert not budget._entry_attempt_generations
    assert budget._configuration_generation == configuration_generation
    if mutation == "remove":
        assert budget._owner_lifecycle_generations[id(pending_owner)] == (
            lifecycle_generation
        )
    else:
        assert id(pending_owner) not in budget._owner_lifecycle_generations


def test_track_keeps_latest_same_key_replacement_accounting() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    old_value = ["old"]
    owner.store("values", "key", old_value)

    estimator_started = Event()
    allow_estimator_to_finish = Event()
    worker_errors: list[BaseException] = []

    def delayed_old_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        assert key == "key"
        assert stop_after == 10_000
        if value is old_value:
            estimator_started.set()
            assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            return MIN_TRACKED_ENTRY_BYTES * 2
        raise AssertionError("new atomic replacement was estimated")  # noqa: TRY003

    def track_old_entry() -> None:
        try:
            budget.track(owner, "values", "key", old_value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=delayed_old_estimator,
    ):
        worker = Thread(target=track_old_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            owner.store("values", "key", "new")
            budget.track(owner, "values", "key", "new")
            allow_estimator_to_finish.set()
        finally:
            allow_estimator_to_finish.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    assert owner.entries[("values", "key")] == "new"
    assert (
        budget._entries[(id(owner), "values", "key")].exact_bytes
        == MIN_TRACKED_ENTRY_BYTES
    )
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES


def test_track_admits_distinct_entry_after_other_entry_is_tracked() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    a_value = ["A"]
    owner.store("values", "a", a_value)

    estimator_started = Event()
    allow_estimator_to_finish = Event()
    worker_errors: list[BaseException] = []

    def blocking_a_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        assert stop_after == 10_000
        if key == "a":
            assert value is a_value
            estimator_started.set()
            assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        else:
            raise AssertionError("atomic entry was estimated")  # noqa: TRY003
        return MIN_TRACKED_ENTRY_BYTES

    def track_a() -> None:
        try:
            budget.track(owner, "values", "a", a_value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=blocking_a_estimator,
    ):
        worker = Thread(target=track_a)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            owner.store("values", "b", "B")
            budget.track(owner, "values", "b", "B")
            allow_estimator_to_finish.set()
        finally:
            allow_estimator_to_finish.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    assert owner.entries == {("values", "a"): a_value, ("values", "b"): "B"}
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES * 2


def test_track_rejects_pre_clear_attempt_after_owner_lifecycle_restarts() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    old_value = ["old"]
    owner.store("values", "key", old_value)

    estimator_started = Event()
    allow_estimator_to_finish = Event()
    worker_errors: list[BaseException] = []

    def delayed_old_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        assert key == "key"
        assert stop_after == 10_000
        if value is old_value:
            estimator_started.set()
            assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            return MIN_TRACKED_ENTRY_BYTES * 2
        raise AssertionError("new atomic entry was estimated")  # noqa: TRY003

    def track_old_entry() -> None:
        try:
            budget.track(owner, "values", "key", old_value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=delayed_old_estimator,
    ):
        worker = Thread(target=track_old_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            budget.clear_context(owner)
            owner._evict_run_cache_entry("values", "key")
            budget.register(owner, 10_000)
            owner.store("values", "key", "new")
            budget.track(owner, "values", "key", "new")
            allow_estimator_to_finish.set()
        finally:
            allow_estimator_to_finish.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    assert owner.entries[("values", "key")] == "new"
    assert (
        budget._entries[(id(owner), "values", "key")].exact_bytes
        == MIN_TRACKED_ENTRY_BYTES
    )
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES


def test_track_does_not_admit_entry_evicted_while_estimation_is_blocked() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    initial_limit = 1_000_000
    budget.register(owner, initial_limit)
    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        return_value=MIN_TRACKED_ENTRY_BYTES,
    ):
        for index in range(255):
            value = [index]
            owner.store("values", index, value)
            budget.track(owner, "values", index, value)

    estimator_started = Event()
    allow_estimator_to_finish = Event()
    worker_errors: list[BaseException] = []
    estimator_calls = 0

    def blocking_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        nonlocal estimator_calls
        assert key == 0
        assert value == [0]
        assert stop_after == initial_limit
        estimator_calls += 1
        if estimator_calls == 1:
            estimator_started.set()
            assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return MIN_TRACKED_ENTRY_BYTES

    def refresh_entry() -> None:
        try:
            budget.refresh(owner, "values", 0, [0])
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=blocking_estimator,
    ):
        worker = Thread(target=refresh_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            budget.register(owner, MIN_TRACKED_ENTRY_BYTES * 2)
            allow_estimator_to_finish.set()
        finally:
            allow_estimator_to_finish.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    assert ("values", 0) not in owner.entries
    assert (id(owner), "values", 0) not in budget._entries
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES


def test_register_only_advances_configuration_generation_for_limit_changes() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()

    assert budget._configuration_generation == 0
    budget.register(first, 10_000)
    assert budget._configuration_generation == 1
    budget.register(first, 10_000)
    budget.register(second, 10_000)
    assert budget._configuration_generation == 1
    budget.register(first, 20_000)
    assert budget._configuration_generation == 2


def test_successful_admissions_do_not_leave_attempt_tokens() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()

    for key in ("first", "second", "third"):
        budget.register(owner, 10_000)
        owner.store("values", key, "value")
        budget.track(owner, "values", key, "value")
        tracked_key = (id(owner), "values", key)
        assert tracked_key not in budget._entry_attempt_generations

        budget.register(owner, None)
        owner._evict_run_cache_entry("values", key)
        budget.remove(owner, "values", key)

        assert tracked_key not in budget._entry_attempt_generations
        assert not budget._entry_attempt_generations


def test_disabling_budget_rejects_pre_disable_estimate_after_reenable() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    value = ["value"]
    owner.store("values", "key", value)

    estimator_started = Event()
    allow_estimator_to_finish = Event()
    worker_errors: list[BaseException] = []
    estimator_calls = 0

    def blocking_first_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        nonlocal estimator_calls
        assert key == "key"
        assert value == ["value"]
        assert stop_after == 10_000
        estimator_calls += 1
        invocation = estimator_calls
        if invocation == 1:
            estimator_started.set()
            assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            return MIN_TRACKED_ENTRY_BYTES * 2
        return MIN_TRACKED_ENTRY_BYTES

    def track_entry() -> None:
        try:
            budget.track(owner, "values", "key", value)
        except BaseException as error:  # noqa: BLE001
            worker_errors.append(error)

    with mock.patch.object(
        run_context_lru,
        "estimate_cache_entry_size",
        side_effect=blocking_first_estimator,
    ):
        worker = Thread(target=track_entry)
        worker.start()
        try:
            assert estimator_started.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            budget.register(owner, None)
            assert not budget._entry_attempt_generations
            budget.register(owner, 10_000)
            allow_estimator_to_finish.set()
        finally:
            allow_estimator_to_finish.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    assert estimator_calls == 2
    entry = budget._entries[(id(owner), "values", "key")]
    assert entry.stratum is not None
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES


def test_budget_evicts_oldest_entry_across_owners() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(first, entry_size * 3)
    budget.register(second, entry_size * 3)

    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    second.store("values", "b", "B")
    budget.track(second, "values", "b", "B")
    second.store("values", "c", "C")
    budget.track(second, "values", "c", "C")

    assert ("values", "a") not in first.entries
    assert ("values", "b") in second.entries
    assert ("values", "c") in second.entries


def test_budget_touch_refreshes_recency() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size * 3)
    for key, value in (("a", "A"), ("b", "B")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)

    budget.touch(owner, "values", "a")
    owner.store("values", "c", "C")
    budget.track(owner, "values", "c", "C")

    assert ("values", "a") in owner.entries
    assert ("values", "b") not in owner.entries


def test_budget_touch_many_preserves_latest_access_order() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size * 4)
    for key, value in (("a", "A"), ("b", "B"), ("c", "C")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)

    budget.touch_many(
        owner,
        (("values", "a"), ("values", "b"), ("values", "a")),
    )
    owner.store("values", "d", "D")
    budget.track(owner, "values", "d", "D")

    assert ("values", "a") in owner.entries
    assert ("values", "b") in owner.entries
    assert ("values", "c") not in owner.entries
    assert ("values", "d") in owner.entries


def test_budget_touch_skips_lock_for_current_mru_entry() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "key", "value")
    budget.track(owner, "values", "key", "value")
    original_lock = budget._lock
    budget._lock = RaisingLock()

    try:
        budget.touch(owner, "values", "key")
    finally:
        budget._lock = original_lock


def test_budget_touch_skips_lock_for_mru_after_current_entry_is_removed() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    for key in ("first", "second"):
        owner.store("values", key, key)
        budget.track(owner, "values", key, key)
    owner.entries.pop(("values", "second"))
    budget.remove(owner, "values", "second")
    original_lock = budget._lock
    budget._lock = RaisingLock()

    try:
        budget.touch(owner, "values", "first")
    finally:
        budget._lock = original_lock


def test_budget_touch_skips_lock_for_mru_after_limit_setting_transition() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    for key in ("first", "second"):
        owner.store("values", key, key)
        budget.track(owner, "values", key, key)
    budget.register(owner, None)
    budget.register(owner, 10_000)
    original_lock = budget._lock
    budget._lock = RaisingLock()

    try:
        budget.touch(owner, "values", "second")
    finally:
        budget._lock = original_lock


def test_budget_notifies_live_owners_when_enabled_mode_changes() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    budget.register(first, None)
    budget.register(second, 10_000)

    assert first.budget_enabled
    assert second.budget_enabled

    budget.register(first, None)

    assert not first.budget_enabled
    assert not second.budget_enabled


def test_recency_enables_at_80_percent_and_disables_below_70_percent() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = ModeRecordingCacheOwner()
    budget.register(owner, 10_000)

    _track_exact_bytes(budget, owner, "below-enable", 7_999)
    assert budget.is_recency_enabled is False
    _track_exact_bytes(budget, owner, "at-enable", 1)
    assert budget.is_recency_enabled is True

    budget.remove(owner, "values", "at-enable")
    assert budget.is_recency_enabled is True
    budget.remove(owner, "values", "below-enable")
    assert budget.is_recency_enabled is False
    assert [update[:2] for update in owner.mode_updates] == [
        (True, False),
        (True, True),
        (True, False),
    ]


def test_recency_mode_handles_disabled_zero_and_cap_transitions() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = ModeRecordingCacheOwner()

    budget.register(owner, None)
    assert owner.mode_updates[-1][:2] == (False, False)
    budget.register(owner, 0)
    assert owner.mode_updates[-1][:2] == (True, False)
    budget.register(owner, 10_000)
    _track_exact_bytes(budget, owner, "entry", 4_200)

    budget.register(owner, 5_250)
    assert budget.is_recency_enabled is True
    budget.register(owner, 6_000)
    assert budget.is_recency_enabled is True
    budget.register(owner, 6_001)
    assert budget.is_recency_enabled is False


def test_recency_mode_does_not_publish_duplicate_mode_tuple() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = ModeRecordingCacheOwner()
    budget.register(owner, 10_000)
    first_update = owner.mode_updates[-1]

    _track_exact_bytes(budget, owner, "entry", 1_000)
    budget.register(owner, 10_001)

    assert owner.mode_updates == [first_update]


def test_recency_mode_disables_after_weak_owner_removal_lowers_usage() -> None:
    import gc

    budget = ProcessRunContextCacheBudget()
    survivor = ModeRecordingCacheOwner()
    removed = ModeRecordingCacheOwner()
    budget.register(survivor, 10_000)
    budget.register(removed, 10_000)
    _track_exact_bytes(budget, removed, "large", 8_000)
    assert budget.is_recency_enabled is True

    removed_reference = ref(removed)
    del removed
    gc.collect()

    assert removed_reference() is None
    assert budget.is_recency_enabled is False
    assert survivor.mode_updates[-1][:2] == (True, False)


def test_recency_reentrant_callback_keeps_newest_generation() -> None:
    budget = ProcessRunContextCacheBudget()

    class ReentrantOwner(ModeRecordingCacheOwner):
        def _set_run_cache_modes(
            self,
            budget_enabled: bool,
            recency_enabled: bool,
            generation: int,
        ) -> None:
            if recency_enabled:
                budget.register(self, 20_000)
            super()._set_run_cache_modes(
                budget_enabled,
                recency_enabled,
                generation,
            )

    owner = ReentrantOwner()
    budget.register(owner, 10_000)
    _track_exact_bytes(budget, owner, "entry", 8_000)

    assert budget.is_recency_enabled is False
    assert owner.mode_updates[-1][0:2] == (True, False)
    assert owner.mode_updates[-1][2] > owner.mode_updates[-2][2]


def test_recency_callback_is_deferred_past_reentrant_outer_lock() -> None:
    budget = ProcessRunContextCacheBudget()

    class TrackingLock:
        def __init__(self, wrapped: object) -> None:
            self.wrapped = wrapped
            self.depth = 0

        def __enter__(self) -> None:
            self.wrapped.__enter__()  # type: ignore[attr-defined]
            self.depth += 1

        def __exit__(self, *args: object) -> None:
            self.depth -= 1
            self.wrapped.__exit__(*args)  # type: ignore[attr-defined]

        def _is_owned(self) -> bool:
            return self.depth > 0

    tracking_lock = TrackingLock(budget._lock)
    budget._lock = tracking_lock  # type: ignore[assignment]

    class LockCheckingOwner(LimitRaisingCacheOwner):
        def _set_run_cache_modes(
            self,
            budget_enabled: bool,
            recency_enabled: bool,
            generation: int,
        ) -> None:
            assert tracking_lock.depth == 0
            super()._set_run_cache_modes(
                budget_enabled,
                recency_enabled,
                generation,
            )

    owner = LockCheckingOwner(budget, 2_000)
    budget.register(owner, 640)
    _track_exact_bytes(budget, owner, "first", 256)
    _track_exact_bytes(budget, owner, "second", 256)
    assert budget.is_recency_enabled is True

    _track_exact_bytes(budget, owner, "third", 256)

    assert budget.is_recency_enabled is False


def test_unchanged_mode_admission_enters_coordinator_lock_once() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = ModeRecordingCacheOwner()
    budget.register(owner, 10_000)

    class CountingLock:
        def __init__(self, wrapped: object) -> None:
            self.wrapped = wrapped
            self.enter_count = 0

        def __enter__(self) -> None:
            self.enter_count += 1
            self.wrapped.__enter__()  # type: ignore[attr-defined]

        def __exit__(self, *args: object) -> None:
            self.wrapped.__exit__(*args)  # type: ignore[attr-defined]

        def _is_owned(self) -> bool:
            return bool(self.wrapped._is_owned())  # type: ignore[attr-defined]

    counting_lock = CountingLock(budget._lock)
    budget._lock = counting_lock  # type: ignore[assignment]

    _track_exact_bytes(budget, owner, "entry", 1_000)

    assert counting_lock.enter_count == 1
    assert owner.mode_updates == [(True, False, 1)]


def test_track_eviction_exception_reconciles_recency_below_70_percent() -> None:
    budget = ProcessRunContextCacheBudget()

    class RaisingEvictionOwner(ModeRecordingCacheOwner):
        def _evict_run_cache_entry(
            self,
            namespace: RunCacheNamespace,
            key: Hashable,
        ) -> None:
            super()._evict_run_cache_entry(namespace, key)
            raise RuntimeError("eviction failed")  # noqa: TRY003

    raising = RaisingEvictionOwner()
    survivor = ModeRecordingCacheOwner()
    budget.register(raising, 1_000)
    budget.register(survivor, 1_000)
    _track_exact_bytes(budget, raising, "large", 500)
    _track_exact_bytes(budget, survivor, "warm", 300)
    assert budget.is_recency_enabled is True

    with pytest.raises(RuntimeError, match="eviction failed"):
        _track_exact_bytes(budget, survivor, "new", 300)

    assert budget.estimated_bytes == 600
    assert budget.is_recency_enabled is False
    assert survivor.mode_updates[-1][:2] == (True, False)


def test_eviction_exception_precedes_mode_callback_exception() -> None:
    budget = ProcessRunContextCacheBudget()

    class DoublyRaisingOwner(ModeRecordingCacheOwner):
        raise_on_disabled_mode = False

        def _evict_run_cache_entry(
            self,
            namespace: RunCacheNamespace,
            key: Hashable,
        ) -> None:
            super()._evict_run_cache_entry(namespace, key)
            raise RuntimeError("primary eviction failed")  # noqa: TRY003

        def _set_run_cache_modes(
            self,
            budget_enabled: bool,
            recency_enabled: bool,
            generation: int,
        ) -> None:
            super()._set_run_cache_modes(
                budget_enabled,
                recency_enabled,
                generation,
            )
            if self.raise_on_disabled_mode and not recency_enabled:
                raise RuntimeError("secondary publication failed")  # noqa: TRY003

    raising = DoublyRaisingOwner()
    survivor = ModeRecordingCacheOwner()
    budget.register(raising, 1_000)
    budget.register(survivor, 1_000)
    _track_exact_bytes(budget, raising, "large", 500)
    _track_exact_bytes(budget, survivor, "warm", 300)
    raising.raise_on_disabled_mode = True

    with pytest.raises(RuntimeError, match="primary eviction failed") as captured:
        _track_exact_bytes(budget, survivor, "new", 300)

    assert any(
        "secondary publication failed" in note
        for note in getattr(captured.value, "__notes__", ())
    )
    assert survivor.mode_updates[-1][:2] == (True, False)


def test_register_eviction_exception_reconciles_recency_below_70_percent() -> None:
    budget = ProcessRunContextCacheBudget()

    class RaisingEvictionOwner(ModeRecordingCacheOwner):
        def _evict_run_cache_entry(
            self,
            namespace: RunCacheNamespace,
            key: Hashable,
        ) -> None:
            super()._evict_run_cache_entry(namespace, key)
            raise RuntimeError("cap eviction failed")  # noqa: TRY003

    raising = RaisingEvictionOwner()
    survivor = ModeRecordingCacheOwner()
    budget.register(raising, 1_000)
    budget.register(survivor, 1_000)
    _track_exact_bytes(budget, raising, "large", 500)
    _track_exact_bytes(budget, survivor, "warm", 300)
    assert budget.is_recency_enabled is True

    with pytest.raises(RuntimeError, match="cap eviction failed"):
        budget.register(survivor, 700)

    assert budget.estimated_bytes == 300
    assert budget.is_recency_enabled is False
    assert survivor.mode_updates[-1][:2] == (True, False)


def test_rebuild_exception_publishes_enabled_mode_after_partial_eviction() -> None:
    budget = ProcessRunContextCacheBudget()

    class RaisingEvictionOwner(ModeRecordingCacheOwner):
        def _evict_run_cache_entry(
            self,
            namespace: RunCacheNamespace,
            key: Hashable,
        ) -> None:
            super()._evict_run_cache_entry(namespace, key)
            raise RuntimeError("rebuild eviction failed")  # noqa: TRY003

    raising = RaisingEvictionOwner()
    survivor = ModeRecordingCacheOwner()
    late = ModeRecordingCacheOwner()
    budget.register(raising, None)
    budget.register(survivor, None)
    budget.register(late, None)
    raising.store("values", "large", "large")
    survivor.store("values", "warm", "warm")
    late.store("values", "new", "new")
    budget._owners = OrderedOwnerRegistry((raising, survivor, late))

    signals = iter((500, 300, 300))

    def admission_signal(*args: object) -> run_context_lru._AdmissionSignal:
        del args
        return run_context_lru._AdmissionSignal(None, 0, next(signals))

    with (
        mock.patch.object(run_context_lru, "_admission_signal", admission_signal),
        pytest.raises(RuntimeError, match="rebuild eviction failed"),
    ):
        budget.register(late, 1_000)

    assert budget.estimated_bytes == 600
    assert budget.is_recency_enabled is False
    assert survivor.mode_updates[-1][:2] == (True, False)


def test_budget_refreshes_mru_after_eviction() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size * 2)
    for key, value in (("a", "A"), ("b", "B")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)
    original_lock = budget._lock
    budget._lock = RaisingLock()

    try:
        budget.touch(owner, "values", "b")
        with pytest.raises(AssertionError, match="unexpected coordinator lock entry"):
            budget.touch(owner, "values", "a")
    finally:
        budget._lock = original_lock


def test_budget_refreshes_mru_after_clear_context() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    budget.register(first, 10_000)
    budget.register(second, 10_000)
    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    second.store("values", "b", "B")
    budget.track(second, "values", "b", "B")
    budget.clear_context(second)
    original_lock = budget._lock
    budget._lock = RaisingLock()

    try:
        budget.touch(first, "values", "a")
        with pytest.raises(AssertionError, match="unexpected coordinator lock entry"):
            budget.touch(second, "values", "b")
    finally:
        budget._lock = original_lock


def test_budget_refreshes_mru_after_weak_owner_finalization() -> None:
    import gc

    budget = ProcessRunContextCacheBudget()
    survivor = CacheOwner()
    dead_owner = CacheOwner()
    budget.register(survivor, 10_000)
    budget.register(dead_owner, 10_000)
    survivor.store("values", "a", "A")
    budget.track(survivor, "values", "a", "A")
    dead_owner.store("values", "b", "B")
    budget.track(dead_owner, "values", "b", "B")
    dead_reference = ref(dead_owner)

    del dead_owner
    gc.collect()

    assert dead_reference() is None
    original_lock = budget._lock
    budget._lock = RaisingLock()
    try:
        budget.touch(survivor, "values", "a")
    finally:
        budget._lock = original_lock


def test_budget_stale_owner_finalizer_preserves_replacement_mru() -> None:
    budget = ProcessRunContextCacheBudget()
    stale_owner = CacheOwner()
    stale_reference = ref(stale_owner)
    replacement_owner = CacheOwner()
    replacement_reference = ref(replacement_owner)
    owner_id = id(stale_owner)
    tracked_key = (owner_id, "values", "replacement")
    entry_size = estimate_cache_entry_size("replacement", "R", stop_after=None)
    replacement_owner.store("values", "replacement", "R")
    budget._owner_references = {owner_id: replacement_reference}
    budget._entries[tracked_key] = _TrackedEntry(
        owner=replacement_reference,
        namespace="values",
        key="replacement",
        exact_bytes=entry_size,
        stratum=None,
        shallow_bytes=0,
    )
    budget._mru_key = tracked_key
    budget._exact_total_bytes = entry_size
    budget._total_bytes = entry_size

    budget._owner_finalizer(owner_id)(stale_reference)
    original_lock = budget._lock
    budget._lock = RaisingLock()
    try:
        budget.touch(replacement_owner, "values", "replacement")
    finally:
        budget._lock = original_lock


def test_budget_refreshes_mru_after_oversized_rejection() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size * 2)
    owner.store("values", "a", "A")
    budget.track(owner, "values", "a", "A")
    owner.store("values", "oversized", b"x" * 1024)
    budget.track(owner, "values", "oversized", b"x" * 1024)
    original_lock = budget._lock
    budget._lock = RaisingLock()

    try:
        budget.touch(owner, "values", "a")
        with pytest.raises(AssertionError, match="unexpected coordinator lock entry"):
            budget.touch(owner, "values", "oversized")
    finally:
        budget._lock = original_lock


def test_budget_touch_locks_when_entry_is_not_current_mru() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    for key in ("first", "second"):
        owner.store("values", key, key)
        budget.track(owner, "values", key, key)
    original_lock = budget._lock
    budget._lock = RaisingLock()

    try:
        with pytest.raises(AssertionError, match="unexpected coordinator lock entry"):
            budget.touch(owner, "values", "first")
    finally:
        budget._lock = original_lock


def test_budget_does_not_admit_entry_larger_than_limit() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 256)
    owner.store("values", "large", b"x" * 1024)

    budget.track(owner, "values", "large", b"x" * 1024)

    assert ("values", "large") not in owner.entries


def test_budget_replacement_and_remove_update_accounting() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "key", "small")
    budget.track(owner, "values", "key", "small")
    small_size = budget.estimated_bytes

    owner.store("values", "key", b"x" * 1024)
    budget.track(owner, "values", "key", b"x" * 1024)

    assert budget.estimated_bytes > small_size
    budget.remove(owner, "values", "key")
    assert budget.estimated_bytes == 0


def test_budget_clear_context_preserves_other_owner_accounting() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    budget.register(first, 10_000)
    budget.register(second, 10_000)
    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    second.store("values", "b", "B")
    budget.track(second, "values", "b", "B")
    second_size = estimate_cache_entry_size("b", "B", stop_after=None)

    budget.clear_context(first)

    assert budget.estimated_bytes == second_size
    assert ("values", "b") in second.entries


def test_budget_releases_dead_owner_accounting() -> None:
    import gc

    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "a", "A")
    budget.track(owner, "values", "a", "A")
    owner_reference = ref(owner)

    del owner
    gc.collect()

    assert owner_reference() is None
    assert budget.estimated_bytes == 0


def test_budget_rebuilds_live_owners_when_limit_changes() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    budget.register(first, None)
    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)

    budget.register(second, entry_size * 2)
    second.store("values", "b", "B")
    budget.track(second, "values", "b", "B")

    assert ("values", "a") not in first.entries
    assert ("values", "b") in second.entries


def test_budget_rebuild_eviction_does_not_mutate_live_owner_iteration() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, None)
    for key, value in (("a", "A"), ("b", "B"), ("c", "C")):
        owner.store("values", key, value)
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)

    budget.register(owner, entry_size * 3)

    assert ("values", "a") not in owner.entries
    assert ("values", "b") in owner.entries
    assert ("values", "c") in owner.entries
    assert budget.estimated_bytes == entry_size * 2


def test_budget_lowering_finite_limit_preserves_single_owner_lru_order() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size * 4)
    for key, value in (("a", "A"), ("b", "B"), ("c", "C")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)

    budget.touch(owner, "values", "a")
    budget.register(owner, entry_size * 3)

    assert ("values", "a") in owner.entries
    assert ("values", "b") not in owner.entries
    assert ("values", "c") in owner.entries


def test_budget_lowering_finite_limit_preserves_cross_owner_lru_order() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(first, entry_size * 4)
    budget.register(second, entry_size * 4)
    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    for key, value in (("b", "B"), ("c", "C")):
        second.store("values", key, value)
        budget.track(second, "values", key, value)

    budget.touch(first, "values", "a")
    budget._owners = OrderedOwnerRegistry((first, second))
    budget.register(second, entry_size * 3)

    assert ("values", "a") in first.entries
    assert ("values", "b") not in second.entries
    assert ("values", "c") in second.entries


def test_budget_new_empty_owner_enforces_lower_finite_limit() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size * 4)
    for key, value in (("a", "A"), ("b", "B"), ("c", "C")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)

    budget.register(CacheOwner(), entry_size * 3)

    assert ("values", "a") not in owner.entries
    assert ("values", "b") in owner.entries
    assert ("values", "c") in owner.entries
    assert budget.estimated_bytes == entry_size * 2


def test_budget_stale_dead_owner_cleanup_preserves_replacement_accounting() -> None:
    budget = ProcessRunContextCacheBudget()
    stale_owner = CacheOwner()
    stale_reference = ref(stale_owner)
    replacement_owner = CacheOwner()
    replacement_reference = ref(replacement_owner)
    owner_id = id(stale_owner)
    entry_size = estimate_cache_entry_size("replacement", "R", stop_after=None)
    replacement_owner.store("values", "replacement", "R")
    budget._owner_references = {owner_id: replacement_reference}
    budget._entries[(owner_id, "values", "replacement")] = _TrackedEntry(
        owner=replacement_reference,
        namespace="values",
        key="replacement",
        exact_bytes=entry_size,
        stratum=None,
        shallow_bytes=0,
    )
    budget._exact_total_bytes = entry_size
    budget._total_bytes = entry_size

    budget._owner_finalizer(owner_id)(stale_reference)

    assert ("values", "replacement") in replacement_owner.entries
    assert budget.estimated_bytes == entry_size


def test_budget_reentrant_eviction_honors_raised_limit() -> None:
    budget = ProcessRunContextCacheBudget()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    owner = LimitRaisingCacheOwner(budget, entry_size * 3)
    budget.register(owner, entry_size * 4)
    for key, value in (("a", "A"), ("b", "B"), ("c", "C")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)

    budget.register(owner, entry_size * 2)

    assert ("values", "a") not in owner.entries
    assert ("values", "b") in owner.entries
    assert ("values", "c") in owner.entries
    assert budget.estimated_bytes == entry_size * 2


def test_budget_registers_prepopulated_owner_when_finite_limit_changes() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(first, entry_size * 4)
    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    for key, value in (("b", "B"), ("c", "C")):
        second.store("values", key, value)

    budget.register(second, entry_size * 3)

    assert ("values", "a") not in first.entries
    assert ("values", "b") in second.entries
    assert ("values", "c") in second.entries
    assert budget.estimated_bytes == entry_size * 2


def test_budget_registers_prepopulated_owner_when_finite_limit_is_unchanged() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(first, entry_size * 3)
    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    for key, value in (("b", "B"), ("c", "C")):
        second.store("values", key, value)

    budget.register(second, entry_size * 3)

    assert ("values", "a") not in first.entries
    assert ("values", "b") in second.entries
    assert ("values", "c") in second.entries
    assert budget.estimated_bytes == entry_size * 2
