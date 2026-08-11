from collections.abc import Hashable, Iterable
from types import ModuleType
from typing import Literal
from weakref import ref

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from general_manager.cache.run_context_lru import (
    MIN_TRACKED_ENTRY_BYTES,
    ProcessRunContextCacheBudget,
    _TrackedEntry,
    estimate_cache_entry_size,
    resolve_run_context_cache_max_bytes,
)

Namespace = Literal["values", "dependency_hits"]


class CacheOwner:
    def __init__(self) -> None:
        self.entries: dict[tuple[Namespace, Hashable], object] = {}

    def store(self, namespace: Namespace, key: Hashable, value: object) -> None:
        self.entries[(namespace, key)] = value

    def _iter_run_cache_entries(
        self,
    ) -> Iterable[tuple[Namespace, Hashable, object]]:
        for (namespace, key), value in self.entries.items():
            yield namespace, key, value

    def _evict_run_cache_entry(self, namespace: Namespace, key: Hashable) -> None:
        self.entries.pop((namespace, key), None)


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

    def _evict_run_cache_entry(self, namespace: Namespace, key: Hashable) -> None:
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

    def __iter__(self) -> Iterable[CacheOwner]:
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


class CountingHashKey:
    def __init__(self) -> None:
        self.hash_calls = 0

    def __hash__(self) -> int:
        self.hash_calls += 1
        return 1


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


def test_estimate_cache_entry_size_counts_shared_object_once_per_entry() -> None:
    shared = [bytearray(1024)]

    shared_size = estimate_cache_entry_size("key", [shared, shared], stop_after=None)
    copied_size = estimate_cache_entry_size(
        "key", [[bytearray(1024)], [bytearray(1024)]], stop_after=None
    )

    assert shared_size < copied_size


def test_estimate_cache_entry_size_stops_after_budget() -> None:
    size = estimate_cache_entry_size(
        "key",
        [b"x" * 1024 for _ in range(100)],
        stop_after=512,
    )

    assert size == 513


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

    assert size == MIN_TRACKED_ENTRY_BYTES


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


def test_budget_evicts_oldest_entry_across_owners() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(first, entry_size * 2)
    budget.register(second, entry_size * 2)

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
    budget.register(owner, entry_size * 2)
    for key, value in (("a", "A"), ("b", "B")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)

    budget.touch(owner, "values", "a")
    owner.store("values", "c", "C")
    budget.track(owner, "values", "c", "C")

    assert ("values", "a") in owner.entries
    assert ("values", "b") not in owner.entries


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

    budget.register(second, entry_size)
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

    budget.register(owner, entry_size * 2)

    assert ("values", "a") not in owner.entries
    assert ("values", "b") in owner.entries
    assert ("values", "c") in owner.entries
    assert budget.estimated_bytes == entry_size * 2


def test_budget_lowering_finite_limit_preserves_single_owner_lru_order() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size * 3)
    for key, value in (("a", "A"), ("b", "B"), ("c", "C")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)

    budget.touch(owner, "values", "a")
    budget.register(owner, entry_size * 2)

    assert ("values", "a") in owner.entries
    assert ("values", "b") not in owner.entries
    assert ("values", "c") in owner.entries


def test_budget_lowering_finite_limit_preserves_cross_owner_lru_order() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(first, entry_size * 3)
    budget.register(second, entry_size * 3)
    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    for key, value in (("b", "B"), ("c", "C")):
        second.store("values", key, value)
        budget.track(second, "values", key, value)

    budget.touch(first, "values", "a")
    budget._owners = OrderedOwnerRegistry((first, second))
    budget.register(second, entry_size * 2)

    assert ("values", "a") in first.entries
    assert ("values", "b") not in second.entries
    assert ("values", "c") in second.entries


def test_budget_new_empty_owner_enforces_lower_finite_limit() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size * 3)
    for key, value in (("a", "A"), ("b", "B"), ("c", "C")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)

    budget.register(CacheOwner(), entry_size * 2)

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
        size=entry_size,
    )
    budget._total_bytes = entry_size

    budget._owner_finalizer(owner_id)(stale_reference)

    assert ("values", "replacement") in replacement_owner.entries
    assert budget.estimated_bytes == entry_size


def test_budget_reentrant_eviction_honors_raised_limit() -> None:
    budget = ProcessRunContextCacheBudget()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    owner = LimitRaisingCacheOwner(budget, entry_size * 2)
    budget.register(owner, entry_size * 3)
    for key, value in (("a", "A"), ("b", "B"), ("c", "C")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)

    budget.register(owner, entry_size)

    assert ("values", "a") not in owner.entries
    assert ("values", "b") in owner.entries
    assert ("values", "c") in owner.entries
    assert budget.estimated_bytes == entry_size * 2


def test_budget_registers_prepopulated_owner_when_finite_limit_changes() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(first, entry_size * 3)
    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    for key, value in (("b", "B"), ("c", "C")):
        second.store("values", key, value)

    budget.register(second, entry_size * 2)

    assert ("values", "a") not in first.entries
    assert ("values", "b") in second.entries
    assert ("values", "c") in second.entries
    assert budget.estimated_bytes == entry_size * 2


def test_budget_registers_prepopulated_owner_when_finite_limit_is_unchanged() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(first, entry_size * 2)
    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    for key, value in (("b", "B"), ("c", "C")):
        second.store("values", key, value)

    budget.register(second, entry_size * 2)

    assert ("values", "a") not in first.entries
    assert ("values", "b") in second.entries
    assert ("values", "c") in second.entries
    assert budget.estimated_bytes == entry_size * 2
