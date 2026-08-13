from collections.abc import Hashable, Iterable, Iterator
from threading import Event, Thread
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
def test_estimate_cache_entry_size_handles_mutually_cyclic_sampled_sequences(
    stop_after: int | None,
) -> None:
    first: list[object] = [None] * 200
    second: list[object] = [None] * 200
    first[-1] = second
    second[-1] = first

    size = estimate_cache_entry_size("cycle", first, stop_after=stop_after)

    assert MIN_TRACKED_ENTRY_BYTES <= size
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

    def blocking_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        assert key == "blocked"
        assert value == "value"
        assert stop_after == 10_000
        estimator_started.set()
        assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return MIN_TRACKED_ENTRY_BYTES

    def track_entry() -> None:
        try:
            budget.track(owner, "values", "blocked", "value")
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
    new_limit = MIN_TRACKED_ENTRY_BYTES
    budget.register(owner, old_limit)
    owner.store("values", "key", "value")

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
        assert value == "value"
        stop_after_values.append(stop_after)
        if len(stop_after_values) == 1:
            estimator_started.set()
            assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return MIN_TRACKED_ENTRY_BYTES

    def track_entry() -> None:
        try:
            budget.track(owner, "values", "key", "value")
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
    assert owner.entries[("values", "key")] == "value"
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES


def test_track_abandons_admission_during_continuous_limit_changes() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "key", "value")
    estimator_calls = 0

    def changing_configuration_estimator(
        key: Hashable,
        value: object,
        *,
        stop_after: int | None,
    ) -> int:
        nonlocal estimator_calls
        assert key == "key"
        assert value == "value"
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
        budget.track(owner, "values", "key", "value")

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
    owner.store("values", "key", "new")
    tracked_key = (id(owner), "values", "key")

    with (
        mock.patch.object(
            run_context_lru,
            "estimate_cache_entry_size",
            side_effect=RuntimeError("estimation failed"),
        ),
        pytest.raises(RuntimeError, match="estimation failed"),
    ):
        budget.track(owner, "values", "key", "new")

    assert ("values", "key") not in owner.entries
    assert tracked_key not in budget._entries
    assert tracked_key not in budget._entry_attempt_generations
    assert budget.estimated_bytes == 0


def test_failed_track_does_not_evict_newer_same_key_replacement() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "key", "old")

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
        if value == "old":
            estimator_started.set()
            assert allow_estimator_to_fail.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            raise RuntimeError
        assert value == "new"
        return MIN_TRACKED_ENTRY_BYTES

    def track_old_entry() -> None:
        try:
            budget.track(owner, "values", "key", "old")
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
    assert budget._entries[(id(owner), "values", "key")].size == (
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
    owner.store("values", "key", "value")

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
        assert value == "value"
        assert stop_after == 10_000
        estimator_started.set()
        assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return MIN_TRACKED_ENTRY_BYTES

    def track_entry() -> None:
        try:
            budget.track(owner, "values", "key", "value")
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


def test_track_keeps_latest_same_key_replacement_accounting() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "key", "old")

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
        if value == "old":
            estimator_started.set()
            assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            return MIN_TRACKED_ENTRY_BYTES * 2
        assert value == "new"
        return MIN_TRACKED_ENTRY_BYTES

    def track_old_entry() -> None:
        try:
            budget.track(owner, "values", "key", "old")
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
    assert budget._entries[(id(owner), "values", "key")].size == MIN_TRACKED_ENTRY_BYTES
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES


def test_track_admits_distinct_entry_after_other_entry_is_tracked() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "a", "A")

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
            assert value == "A"
            estimator_started.set()
            assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        else:
            assert key == "b"
            assert value == "B"
        return MIN_TRACKED_ENTRY_BYTES

    def track_a() -> None:
        try:
            budget.track(owner, "values", "a", "A")
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
    assert owner.entries == {("values", "a"): "A", ("values", "b"): "B"}
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES * 2


def test_track_rejects_pre_clear_attempt_after_owner_lifecycle_restarts() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "key", "old")

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
        if value == "old":
            estimator_started.set()
            assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
            return MIN_TRACKED_ENTRY_BYTES * 2
        assert value == "new"
        return MIN_TRACKED_ENTRY_BYTES

    def track_old_entry() -> None:
        try:
            budget.track(owner, "values", "key", "old")
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
    assert budget._entries[(id(owner), "values", "key")].size == MIN_TRACKED_ENTRY_BYTES
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES


def test_track_does_not_admit_entry_evicted_while_estimation_is_blocked() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size * 2)
    for key, value in (("a", "A"), ("b", "B")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)

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
        assert key == "a"
        assert value == "A"
        assert stop_after in {entry_size, entry_size * 2}
        estimator_calls += 1
        if estimator_calls == 1:
            estimator_started.set()
            assert allow_estimator_to_finish.wait(timeout=HANDOFF_TIMEOUT_SECONDS)
        return entry_size

    def refresh_entry() -> None:
        try:
            budget.refresh(owner, "values", "a", "A")
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
            budget.register(owner, entry_size)
            allow_estimator_to_finish.set()
        finally:
            allow_estimator_to_finish.set()
            worker.join(timeout=HANDOFF_TIMEOUT_SECONDS)

    assert not worker.is_alive()
    if worker_errors:
        raise worker_errors[0]
    assert ("values", "a") not in owner.entries
    assert (id(owner), "values", "a") not in budget._entries
    assert budget.estimated_bytes == entry_size


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


def test_disabling_budget_releases_attempt_tokens_for_removed_entries() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()

    for key in ("first", "second", "third"):
        budget.register(owner, 10_000)
        owner.store("values", key, "value")
        budget.track(owner, "values", key, "value")
        tracked_key = (id(owner), "values", key)
        assert tracked_key in budget._entry_attempt_generations

        budget.register(owner, None)
        owner._evict_run_cache_entry("values", key)
        budget.remove(owner, "values", key)

        assert tracked_key not in budget._entry_attempt_generations
        assert not budget._entry_attempt_generations


def test_disabling_budget_rejects_pre_disable_estimate_after_reenable() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "key", "value")

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
        assert value == "value"
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
            budget.track(owner, "values", "key", "value")
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
    assert budget._entries[(id(owner), "values", "key")].size == MIN_TRACKED_ENTRY_BYTES
    assert budget.estimated_bytes == MIN_TRACKED_ENTRY_BYTES


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


def test_budget_touch_many_preserves_latest_access_order() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size * 3)
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


def test_budget_refreshes_mru_after_eviction() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size)
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
        size=entry_size,
    )
    budget._mru_key = tracked_key
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
    budget.register(owner, entry_size)
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
