from collections.abc import Hashable, Iterable, Iterator
from unittest import mock
import math
import sys
from types import ModuleType
from typing import cast
from weakref import ref

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from general_manager.cache.run_context_lru import (
    MIN_TRACKED_ENTRY_BYTES,
    ProcessRunContextCacheBudget,
    RunCacheNamespace,
    _TrackedEntry,
    estimate_cache_entry_size,
    resolve_run_context_cache_max_bytes,
)
from general_manager.cache import run_context_lru


class CacheOwner:
    def __init__(self) -> None:
        self.entries: dict[tuple[RunCacheNamespace, Hashable], object] = {}

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


def test_estimate_cache_entry_size_stratifies_large_sequence_samples() -> None:
    small = [bytearray(1) for _ in range(2_001)]
    large = [bytearray(1) for _ in range(2_001)]
    for index in (0, len(large) // 2, len(large) - 1):
        large[index] = bytearray(4_096)

    small_estimate = estimate_cache_entry_size("key", small, stop_after=None)
    large_estimate = estimate_cache_entry_size("key", large, stop_after=None)

    assert large_estimate > small_estimate


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
