"""Guard persistent key compatibility for the single-manager encoding path."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal, localcontext
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from general_manager.api import as_of
from general_manager.cache.cache_decorator import cached
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.run_context import CalculationRunContext
from general_manager.measurement import Measurement
from general_manager.utils._cache_key_encoder import (
    encode_cache_key_value,
    encode_frozen_manager_cache_key_value,
    freeze_encoded_cache_key_value,
)
from general_manager.utils._make_cache_key import make_cache_key
from tests.unit import test_make_cache_key as cache_key_tests


class IntSubclass(int):
    def __str__(self):
        return "custom-int"


class StringSubclass(str):
    pass


class DateSubclass(date):
    pass


class UnknownTimezone(tzinfo):
    pass


@pytest.fixture
def manager():
    return cache_key_tests.TestMakeCacheKey._manager_class()(1)


def getter(manager):
    return manager.identification


def assert_compatible(manager):
    assert encode_frozen_manager_cache_key_value(manager) == (
        freeze_encoded_cache_key_value(encode_cache_key_value(manager))
    )
    # Keyword binding deliberately exercises the independent generic call path.
    assert make_cache_key(getter, (manager,), {}) == make_cache_key(
        getter, (), {"manager": manager}
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        True,
        0,
        -1,
        2**128,
        "",
        "Müller\x00",
        b"\x00\xff",
        IntSubclass(1),
        StringSubclass("s"),
        0.0,
        -0.0,
        float("inf"),
        float("-inf"),
        float("nan"),
        UUID(int=1),
        *[
            Decimal(v)
            for v in (
                "0",
                "-0",
                "0.00",
                "1.0",
                "1.00",
                "1E+8",
                "NaN",
                "-NaN123",
                "sNaN",
                "Infinity",
            )
        ],
        date(2024, 2, 29),
        datetime(2024, 1, 1),
        datetime(2024, 11, 3, 1, 30, fold=1, tzinfo=ZoneInfo("America/New_York")),
        datetime(2024, 1, 1, tzinfo=timezone(timedelta(hours=1), "named")),
        time(1, 30, fold=1),
        time(1, tzinfo=ZoneInfo("UTC")),
        timedelta(days=-2),
        [],
        (),
        {1, "1"},
        frozenset({1, "1"}),
        {"z": [1, (Decimal("2.00"),)], "a": {False, "0"}},
        {"a": {False, "0"}, "z": [1, (Decimal("2.00"),)]},
    ],
)
def test_manager_values_preserve_frozen_representation_and_key(manager, value):
    manager.identification["id"] = value
    for precision in (2, 28, 60):
        with localcontext() as context:
            context.prec = precision
            assert_compatible(manager)


@pytest.mark.parametrize("unit", ["meter", "centimeter", "EUR", "dimensionless"])
def test_measurement_identification(manager, unit):
    manager.identification["id"] = Measurement(Decimal("1.2300"), unit)
    assert_compatible(manager)


@pytest.mark.parametrize(
    "identification",
    [
        {},
        {"id": 1, "tenant": "second"},
        {1: "value"},
        {StringSubclass("id"): 1},
        {"custom": True},
        {"id": {"list": [1, 2]}},
    ],
)
def test_mapping_shapes(manager, identification):
    manager.identification.clear()
    manager.identification.update(identification)
    assert_compatible(manager)


def test_mapping_subclass_keeps_custom_iteration(manager):
    class CustomDict(dict):
        def items(self):
            return [("custom", 42)]

    manager.__dict__["_GeneralManager__id"] = CustomDict(id=1)
    assert_compatible(manager)


@pytest.mark.parametrize(
    "value", [object(), DateSubclass(2024, 1, 1), time(tzinfo=UnknownTimezone())]
)
def test_error_contract(manager, value):
    manager.identification["id"] = value
    with pytest.raises(TypeError) as generic:
        make_cache_key(getter, (), {"manager": manager})
    with pytest.raises(type(generic.value)) as fast:
        make_cache_key(getter, (manager,), {})
    assert str(fast.value) == str(generic.value)


@pytest.mark.parametrize("kind", ["manager", "mapping", "list"])
def test_cycles_preserve_error(manager, kind):
    cycle = []
    cycle.append(cycle)
    manager.identification["id"] = {
        "manager": manager,
        "mapping": manager.identification,
        "list": cycle,
    }[kind]
    with pytest.raises(TypeError, match=r"^Unsupported cache-key cycle$"):
        make_cache_key(getter, (manager,), {})


def test_mutable_identity_is_read_again_and_not_retained(manager):
    keys = []
    for value in (1, True, "1", None, [1], [2], {"nested": [1]}):
        manager.identification["id"] = value
        assert_compatible(manager)
        keys.append(make_cache_key(getter, (manager,), {}))
    assert len(set(keys)) == len(keys)
    frozen = encode_frozen_manager_cache_key_value(manager)
    manager.identification["id"]["nested"].append(2)
    assert encode_frozen_manager_cache_key_value(manager) != frozen
    assert_compatible(manager)


def test_snapshot_and_legacy_snapshot(manager):
    for snapshot in (None, datetime(2024, 1, 1, tzinfo=timezone.utc)):
        manager.__dict__["_effective_search_date"] = snapshot
        for ambient in ("2022-01-01", "2022-01-02"):
            with as_of(ambient):
                assert_compatible(manager)
    del manager.__dict__["_effective_search_date"]
    manager._interface._search_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert_compatible(manager)


def test_class_module_mutation_preserves_generic_freezing(manager):
    manager_type = type(manager)
    original = manager_type.__module__
    try:
        manager_type.__module__ = ["unusual", "module"]
        assert_compatible(manager)
    finally:
        manager_type.__module__ = original


def test_context_isolation_across_threads_and_async_tasks(manager):
    def in_context(day):
        with as_of(day):
            assert_compatible(manager)
            return make_cache_key(getter, (manager,), {})

    days = ["2022-01-01", "2022-01-02"] * 10
    expected = [in_context(day) for day in days]
    assert expected[0] != expected[1]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(in_context, days)) == expected

    async def task(day):
        with as_of(day):
            await asyncio.sleep(0)
            assert_compatible(manager)
            return make_cache_key(getter, (manager,), {})

    async def run():
        return await asyncio.gather(*(task(day) for day in days))

    assert asyncio.run(run()) == expected


def test_cached_getter_mutation_and_dependency_replay(manager):
    calls = []

    @cached
    def value(manager):
        identifier = manager.identification["id"]
        calls.append(identifier)
        DependencyTracker.track("Example", "identification", str(identifier))
        return identifier

    with CalculationRunContext():
        assert value(manager) == 1
        with DependencyTracker() as tracker:
            assert value(manager) == 1
        assert ("Example", "identification", "1") in tracker
        manager.identification["id"] = 2
        assert value(manager) == 2
        manager.identification["id"] = 1
        assert value(manager) == 1
    assert calls == [1, 2]


@pytest.mark.parametrize("scalar", [int, str, float, bytes, Decimal, date])
def test_scalar_manager_dispatch_remains_scalar(scalar):
    from general_manager.manager.general_manager import GeneralManager

    class ScalarManager(scalar, GeneralManager):
        def __init__(self, *args):
            self.__dict__["_GeneralManager__id"] = {"id": 2}

    value = ScalarManager(2024, 1, 1) if scalar is date else ScalarManager()
    if scalar is date:
        with pytest.raises(TypeError) as generic:
            make_cache_key(getter, (), {"manager": value})
        with pytest.raises(type(generic.value)) as fast:
            make_cache_key(getter, (value,), {})
        assert str(fast.value) == str(generic.value)
    else:
        assert_compatible(value)


def test_identity_conversion_precedes_freezing_unusual_module(manager):
    module = ["module"]

    class MutatingInt(int):
        def __str__(self):
            module.append("changed")
            return "1"

    manager_type = type(manager)
    original = manager_type.__module__
    try:
        manager_type.__module__ = module
        manager.identification["id"] = MutatingInt(1)
        expected = freeze_encoded_cache_key_value(encode_cache_key_value(manager))
        module[:] = ["module"]
        assert encode_frozen_manager_cache_key_value(manager) == expected
        module[:] = [module]
        manager.identification["id"] = object()
        with pytest.raises(TypeError, match="Unsupported cache-key value"):
            encode_frozen_manager_cache_key_value(manager)
    finally:
        manager_type.__module__ = original


def test_database_alias_state_keeps_existing_call_key_semantics(manager):
    keys = []
    for alias in ("default", "secondary"):
        manager._interface._instance = SimpleNamespace(_state=SimpleNamespace(db=alias))
        assert_compatible(manager)
        keys.append(make_cache_key(getter, (manager,), {}))
    # The existing call schema encodes identification and snapshot, not ORM state.
    # This optimization must not silently introduce a new namespace component.
    assert keys[0] == keys[1]
    manager.identification["database_alias"] = "default"
    first = make_cache_key(getter, (manager,), {})
    manager.identification["database_alias"] = "secondary"
    assert_compatible(manager)
    assert make_cache_key(getter, (manager,), {}) != first


@pytest.mark.parametrize(
    "identifier,digest",
    [
        (1, "6ec8dc1922b1a895d87918e7e7e8b060aadb2219df5b4ee05d135ea38357d7b6"),
        (True, "5420548e581460a9dd7d5505d20eca4c40e01524340bbd61ab62750aa41352eb"),
        ("1", "4b149e260e930080a2aebb2371f0ca87fdf2383b41e6eba31573a5f61d427d25"),
        (None, "2d9f3631022487f13e5ff24e8a03f3b06cc7f05659e9a93dd0a8f7feed49366a"),
    ],
)
def test_persistent_keys_match_baseline_08873d4(manager, identifier, digest):
    manager.identification["id"] = identifier
    assert make_cache_key(getter, (manager,), {}) == f"gm:call:v2:{digest}"


def test_distinct_mutable_identities_preserve_keys_across_lru_eviction(manager):
    from general_manager.utils._make_cache_key import _single_manager_arg_cache_key

    cache = _single_manager_arg_cache_key
    cache.cache_clear()
    capacity = cache.cache_info().maxsize
    assert capacity == 65_536
    try:
        first = make_cache_key(getter, (manager,), {})
        # Reuse a mutable manager, crossing the capacity with distinct tagged
        # identities. Every key must still equal the independently bound path.
        for identifier in range(2, capacity + 3):
            manager.identification["id"] = identifier
            assert_compatible(manager)
        assert cache.cache_info().currsize == capacity
        misses = cache.cache_info().misses
        manager.identification["id"] = 1
        assert make_cache_key(getter, (manager,), {}) == first
        assert cache.cache_info().misses == misses + 1
        manager.identification["id"] = {"nested": [1]}
        complex_key = make_cache_key(getter, (manager,), {})
        manager.identification["id"]["nested"].append(2)
        assert make_cache_key(getter, (manager,), {}) != complex_key
        assert_compatible(manager)
    finally:
        cache.cache_clear()
