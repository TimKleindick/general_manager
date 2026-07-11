from __future__ import annotations

import sys
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import cast
from unittest.mock import patch

import pytest
from django.test import override_settings

from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket import calculation_bucket as calculation_bucket_module
from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.cache.run_context import CalculationRunContext
from general_manager.interface.interfaces.calculation import CalculationInterface
from general_manager.api.property import graph_ql_property
from general_manager.interface import base_interface as base_interface_module
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from tests.perf.support import (
    Counter,
    CountingIterable,
    PerfBudgets,
    capture_diagnostics,
    count_profiled_calls,
)

pytestmark = pytest.mark.perf


@pytest.mark.parametrize("size", [400, 800])
@override_settings(AUTOCREATE_GRAPHQL=False)
def test_scalar_calculation_construction_skips_full_seed_audit(size: int) -> None:
    class ScalarCalculation(GeneralManager):
        class Interface(CalculationInterface):
            value = Input(int)

    GeneralManagerMeta.ensure_attributes_initialized(ScalarCalculation)
    original_audit = base_interface_module._canonical_manager_class_state
    original_seed = base_interface_module._seed_calculation_resolved_manager_values
    constructor_code = GeneralManager.__dict__["__init__"].__code__

    with (
        patch.object(
            base_interface_module,
            "_canonical_manager_class_state",
            wraps=original_audit,
        ) as full_audits,
        patch.object(
            base_interface_module,
            "_seed_calculation_resolved_manager_values",
            wraps=original_seed,
        ) as seed_calls,
        count_profiled_calls(
            constructor_code,
            lambda self: self.__class__ is ScalarCalculation,
        ) as outer_constructors,
    ):
        managers = [ScalarCalculation(value=index) for index in range(size)]

    assert len(managers) == size
    assert [manager.identification["value"] for manager in managers] == list(
        range(size)
    )
    assert outer_constructors.value == size
    assert seed_calls.call_count == 0
    assert full_audits.call_count == 0


@dataclass
class CombinationCounters:
    a_yields: Counter
    b_yields: Counter
    callbacks: Counter
    constructors: Counter

    @classmethod
    def create(cls) -> CombinationCounters:
        return cls(Counter(), Counter(), Counter(), Counter())

    def reset(self) -> None:
        self.a_yields.reset()
        self.b_yields.reset()
        self.callbacks.reset()
        self.constructors.reset()

    def snapshot(self) -> tuple[int, int, int, int]:
        return (
            self.a_yields.value,
            self.b_yields.value,
            self.callbacks.value,
            self.constructors.value,
        )


class CalculationManagerDouble:
    identification: dict[str, object]


def _make_calculation_manager(
    name: str,
    inputs: dict[str, object],
    constructor_counter: Counter,
) -> type[GeneralManager]:
    interface = cast(
        type[CalculationInterface],
        type(
            f"{name}Interface",
            (CalculationInterface,),
            {
                "__module__": __name__,
                "input_fields": inputs,
                **inputs,
            },
        ),
    )

    def __init__(
        self: CalculationManagerDouble,
        *args: object,
        **kwargs: object,
    ) -> None:
        constructor_counter.increment()
        parsed_interface = interface(*args, **kwargs)
        self.identification = parsed_interface.identification

    manager = cast(
        type[GeneralManager],
        type(
            name,
            (CalculationManagerDouble,),
            {
                "__module__": __name__,
                "Interface": interface,
                "__init__": __init__,
            },
        ),
    )
    interface._parent_class = manager
    return manager


def _make_default_calculation_manager(
    name: str,
    field_name: str,
    input_field: Input[type[object]],
) -> tuple[type[GeneralManager], Input[type[object]]]:
    """Build a real calculation manager without leaving metaclass registry state."""
    interface = cast(
        type[CalculationInterface],
        type(
            f"{name}Interface",
            (CalculationInterface,),
            {"__module__": __name__, field_name: input_field},
        ),
    )
    registries_before = (
        tuple(GeneralManagerMeta.all_classes),
        tuple(GeneralManagerMeta.read_only_classes),
        tuple(GeneralManagerMeta.pending_attribute_initialization),
        tuple(GeneralManagerMeta.pending_graphql_interfaces),
    )
    manager = cast(
        type[GeneralManager],
        type(
            name,
            (GeneralManager,),
            {"__module__": __name__, "Interface": interface},
        ),
    )
    for registry in (
        GeneralManagerMeta.all_classes,
        GeneralManagerMeta.read_only_classes,
        GeneralManagerMeta.pending_attribute_initialization,
        GeneralManagerMeta.pending_graphql_interfaces,
    ):
        while manager in registry:
            registry.remove(manager)
    manager.Interface._parent_class = manager
    assert manager.__init__ is GeneralManager.__init__
    assert (
        tuple(GeneralManagerMeta.all_classes),
        tuple(GeneralManagerMeta.read_only_classes),
        tuple(GeneralManagerMeta.pending_attribute_initialization),
        tuple(GeneralManagerMeta.pending_graphql_interfaces),
    ) == registries_before
    return manager, manager.Interface.input_fields[field_name]


@pytest.mark.parametrize("size", [400, 800])
def test_large_list_input_enumeration_work(
    size: int,
    monkeypatch: pytest.MonkeyPatch,
    perf_budgets: PerfBudgets,
) -> None:
    values = list(range(10_000_000, 10_000_000 + size))
    manager, tracked_input = _make_default_calculation_manager(
        f"LargeListEnumeration{size}Manager",
        "value",
        Input(int, possible_values=values),
    )
    resolution_calls = Counter()
    membership_scan_steps = Counter()
    original_resolve = cast(Callable[..., object], Input.resolve_possible_values)

    def counted_resolve(
        input_field: Input[type[object]],
        identification: dict[str, object] | None = None,
        *,
        cache_context: tuple[type[object], str] | None = None,
    ) -> object:
        resolved = original_resolve(
            input_field,
            identification,
            cache_context=cache_context,
        )
        if input_field is tracked_input:
            resolution_calls.increment()
            # The first call enumerates the source. Membership check i then scans
            # the i - 1 values that precede the matching object in this exact list.
            if resolution_calls.value > 1:
                membership_scan_steps.increment(resolution_calls.value - 2)
        return resolved

    monkeypatch.setattr(Input, "resolve_possible_values", counted_resolve)

    with override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True):
        managers = list(CalculationBucket(manager))

    resolved_values = [item.identification["value"] for item in managers]
    assert len(managers) == size
    assert resolved_values == values
    assert all(
        resolved is original
        for resolved, original in zip(resolved_values, values, strict=True)
    )
    prefix = f"CALC_ENUM_LIST_{size}"
    perf_budgets.assert_observation(
        f"{prefix}_MEMBERSHIP_SCAN_STEPS", membership_scan_steps.value
    )
    perf_budgets.assert_observation(f"{prefix}_RESOLUTIONS", resolution_calls.value)


def _assert_combination_observations(
    perf_budgets: PerfBudgets,
    prefix: str,
    observations: tuple[int, int, int, int],
) -> None:
    for suffix, observed in zip(
        ("A_YIELDS", "B_YIELDS", "CALLBACKS", "CONSTRUCTORS"),
        observations,
        strict=True,
    ):
        perf_budgets.assert_observation(f"{prefix}_{suffix}", observed)


def test_calculation_manager_fixture_does_not_grow_global_registries() -> None:
    registries_before = (
        tuple(GeneralManagerMeta.all_classes),
        tuple(GeneralManagerMeta.read_only_classes),
        tuple(GeneralManagerMeta.pending_attribute_initialization),
        tuple(GeneralManagerMeta.pending_graphql_interfaces),
    )
    constructor_counter = Counter()

    manager = _make_calculation_manager(
        "RegistryIsolatedCalculationManager",
        {"value": Input(int, possible_values=range(2))},
        constructor_counter,
    )
    CalculationBucket(manager).generate_combinations()

    assert (
        tuple(GeneralManagerMeta.all_classes),
        tuple(GeneralManagerMeta.read_only_classes),
        tuple(GeneralManagerMeta.pending_attribute_initialization),
        tuple(GeneralManagerMeta.pending_graphql_interfaces),
    ) == registries_before


class ValueInterface(CalculationInterface):
    id = Input(int, possible_values=range(50))


_REGISTRIES_BEFORE_VALUE_MANAGER = (
    tuple(GeneralManagerMeta.all_classes),
    tuple(GeneralManagerMeta.read_only_classes),
    tuple(GeneralManagerMeta.pending_attribute_initialization),
    tuple(GeneralManagerMeta.pending_graphql_interfaces),
)


class ValueManager(GeneralManager):
    Interface = ValueInterface


for manager_registry in (
    GeneralManagerMeta.all_classes,
    GeneralManagerMeta.read_only_classes,
    GeneralManagerMeta.pending_attribute_initialization,
    GeneralManagerMeta.pending_graphql_interfaces,
):
    while ValueManager in manager_registry:
        manager_registry.remove(ValueManager)
ValueInterface._parent_class = ValueManager
assert (
    tuple(GeneralManagerMeta.all_classes),
    tuple(GeneralManagerMeta.read_only_classes),
    tuple(GeneralManagerMeta.pending_attribute_initialization),
    tuple(GeneralManagerMeta.pending_graphql_interfaces),
) == _REGISTRIES_BEFORE_VALUE_MANAGER


def test_manager_valued_workload_is_registry_isolated() -> None:
    for registry in (
        GeneralManagerMeta.all_classes,
        GeneralManagerMeta.read_only_classes,
        GeneralManagerMeta.pending_attribute_initialization,
        GeneralManagerMeta.pending_graphql_interfaces,
    ):
        assert ValueManager not in registry
    assert ValueInterface._parent_class is ValueManager


def test_profiled_call_counter_preserves_constructor_descriptors_and_profiler() -> None:
    manager_constructor = GeneralManager.__dict__["__init__"]
    value_manager_constructor = ValueManager.__dict__.get("__init__")
    previous_profiler = sys.getprofile()
    observed_events: list[str] = []

    def sentinel_profiler(_frame: object, event: str, _arg: object) -> None:
        observed_events.append(event)

    try:
        sys.setprofile(sentinel_profiler)
        with count_profiled_calls(
            manager_constructor.__code__,
            lambda self: self.__class__ is ValueManager,
        ) as calls:
            ValueManager(id=1)

        assert calls.value == 1
        assert sys.getprofile() is sentinel_profiler
        assert observed_events
        assert GeneralManager.__dict__["__init__"] is manager_constructor
        assert ValueManager.__dict__.get("__init__") is value_manager_constructor
    finally:
        sys.setprofile(previous_profiler)


def test_profiled_call_counter_restores_profiler_after_an_error() -> None:
    class ProfiledFailure(RuntimeError):
        pass

    previous_profiler = sys.getprofile()

    def sentinel_profiler(_frame: object, _event: str, _arg: object) -> None:
        return None

    try:
        sys.setprofile(sentinel_profiler)
        with (
            pytest.raises(ProfiledFailure),
            count_profiled_calls(
                GeneralManager.__dict__["__init__"].__code__,
                lambda self: self.__class__ is ValueManager,
            ),
        ):
            raise ProfiledFailure

        assert sys.getprofile() is sentinel_profiler
    finally:
        sys.setprofile(previous_profiler)


class CountingManagerBucket(Bucket[ValueManager]):
    def __init__(self, values: list[ValueManager], yield_counter: Counter) -> None:
        super().__init__(ValueManager)
        self._values = values
        self._yield_counter = yield_counter

    def __or__(
        self,
        other: Bucket[ValueManager] | ValueManager,
    ) -> CountingManagerBucket:
        if isinstance(other, CountingManagerBucket):
            return CountingManagerBucket(
                [*self._values, *other._values], self._yield_counter
            )
        if isinstance(other, ValueManager):
            return CountingManagerBucket([*self._values, other], self._yield_counter)
        return CountingManagerBucket(list(self._values), self._yield_counter)

    def __iter__(self) -> Generator[ValueManager, None, None]:
        for value in self._values:
            self._yield_counter.increment()
            yield value

    def filter(self, **kwargs: object) -> CountingManagerBucket:
        assert not kwargs
        return self

    def exclude(self, **kwargs: object) -> CountingManagerBucket:
        assert not kwargs
        return self

    def first(self) -> ValueManager | None:
        return self._values[0] if self._values else None

    def last(self) -> ValueManager | None:
        return self._values[-1] if self._values else None

    def count(self) -> int:
        return len(self._values)

    def all(self) -> CountingManagerBucket:
        return self

    def get(self, **kwargs: object) -> ValueManager:
        assert not kwargs
        assert len(self._values) == 1
        return self._values[0]

    def __getitem__(self, item: int | slice) -> ValueManager | Bucket[ValueManager]:
        if isinstance(item, slice):
            return CountingManagerBucket(self._values[item], self._yield_counter)
        return self._values[item]

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, item: ValueManager) -> bool:
        return item in self._values

    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> CountingManagerBucket:
        keys = (key,) if isinstance(key, str) else key
        return CountingManagerBucket(
            sorted(
                self._values,
                key=lambda value: tuple(getattr(value, name) for name in keys),
                reverse=reverse,
            ),
            self._yield_counter,
        )


def test_static_5x10_cold_and_warm_generation(
    perf_budgets: PerfBudgets,
) -> None:
    counters = CombinationCounters.create()
    a_values = CountingIterable(range(5), counters.a_yields)
    b_values = CountingIterable(range(10), counters.b_yields)

    manager = _make_calculation_manager(
        "Static5x10Manager",
        {
            "b": Input(int, possible_values=b_values),
            "a": Input(int, possible_values=a_values),
        },
        counters.constructors,
    )
    bucket = CalculationBucket(manager)

    counters.reset()
    with CalculationRunContext():
        cold = bucket.generate_combinations()
        cold_observations = counters.snapshot()
        counters.reset()
        warm = bucket.generate_combinations()
        warm_observations = counters.snapshot()

    assert cold == [{"a": a, "b": b} for a in range(5) for b in range(10)]
    assert cold[0] == {"a": 0, "b": 0}
    assert cold[-1] == {"a": 4, "b": 9}
    assert warm is cold
    assert cold_observations[2] == 0
    assert warm_observations[2] == 0
    _assert_combination_observations(
        perf_budgets, "CALC_STATIC_5X10_COLD", cold_observations
    )
    _assert_combination_observations(
        perf_budgets, "CALC_STATIC_5X10_WARM", warm_observations
    )


def test_dependent_5x10_cold_and_warm_generation(
    perf_budgets: PerfBudgets,
    pytestconfig: pytest.Config,
) -> None:
    counters = CombinationCounters.create()
    a_values = CountingIterable(range(5), counters.a_yields)

    def possible_b_values(a: int) -> CountingIterable[int]:
        counters.callbacks.increment()
        return CountingIterable(range(a * 10, a * 10 + 10), counters.b_yields)

    manager = _make_calculation_manager(
        "Dependent5x10Manager",
        {
            "a": Input(int, possible_values=a_values),
            "b": Input(
                int,
                possible_values=possible_b_values,
                depends_on=["a"],
            ),
        },
        counters.constructors,
    )
    bucket = CalculationBucket(manager)

    counters.reset()
    with CalculationRunContext():
        diagnostic = capture_diagnostics(bucket.generate_combinations)
        cold = diagnostic.result
        cold_observations = counters.snapshot()
        counters.reset()
        warm = bucket.generate_combinations()
        warm_observations = counters.snapshot()

    if pytestconfig.getoption("verbose") >= 2:
        print(
            "CALC_DEPENDENT_5X10_DIAGNOSTIC "
            f"elapsed={diagnostic.elapsed_seconds:.6f}s "
            f"peak={diagnostic.peak_bytes}B"
        )
    assert cold == [
        {"a": a, "b": a * 10 + offset} for a in range(5) for offset in range(10)
    ]
    assert len({(combo["a"], combo["b"]) for combo in cold}) == 50
    assert cold[0] == {"a": 0, "b": 0}
    assert cold[-1] == {"a": 4, "b": 49}
    assert warm is cold
    _assert_combination_observations(
        perf_budgets, "CALC_DEPENDENT_5X10_COLD", cold_observations
    )
    _assert_combination_observations(
        perf_budgets, "CALC_DEPENDENT_5X10_WARM", warm_observations
    )


def test_equivalent_5x10_plans_reuse_possible_values(
    perf_budgets: PerfBudgets,
) -> None:
    counters = CombinationCounters.create()
    a_values = CountingIterable(range(5), counters.a_yields)

    def possible_a_values() -> CountingIterable[int]:
        counters.callbacks.increment()
        return a_values

    def possible_b_values(a: int) -> CountingIterable[int]:
        counters.callbacks.increment()
        return CountingIterable(range(a * 10, a * 10 + 10), counters.b_yields)

    manager = _make_calculation_manager(
        "Equivalent5x10Manager",
        {
            "a": Input(int, possible_values=possible_a_values),
            "b": Input(
                int,
                possible_values=possible_b_values,
                depends_on=["a"],
            ),
        },
        counters.constructors,
    )
    first_bucket = CalculationBucket(manager, {}, {}, sort_key="a")
    second_bucket = CalculationBucket(manager, {}, {}, sort_key="a")

    counters.reset()
    with CalculationRunContext():
        first = first_bucket.generate_combinations()
        first_observations = counters.snapshot()
        counters.reset()
        second = second_bucket.generate_combinations()
        second_observations = counters.snapshot()

    assert first == second
    assert len(first) == 50
    assert first[0] == {"a": 0, "b": 0}
    assert first[-1] == {"a": 4, "b": 49}
    assert first is not second
    assert all(left is not right for left, right in zip(first, second, strict=True))
    first[0]["temporary"] = True
    try:
        assert "temporary" not in second[0]
    finally:
        del first[0]["temporary"]
    assert first == second
    _assert_combination_observations(
        perf_budgets, "CALC_EQUIVALENT_5X10_FIRST", first_observations
    )
    _assert_combination_observations(
        perf_budgets, "CALC_EQUIVALENT_5X10_SECOND", second_observations
    )


@pytest.mark.parametrize(
    ("shape", "identifications", "expected_distinct"),
    [
        ("UNIQUE", list(range(50)), 50),
        ("REPEATED", [index % 10 for index in range(50)], 10),
    ],
)
def test_manager_valued_input_cold_and_warm_generation(
    perf_budgets: PerfBudgets,
    shape: str,
    identifications: list[int],
    expected_distinct: int,
) -> None:
    yield_counter = Counter()
    constructor_counter = Counter()
    values = [ValueManager(id=identification) for identification in identifications]
    manager_bucket = CountingManagerBucket(values, yield_counter)
    manager = _make_calculation_manager(
        f"ManagerValues{shape.title()}Manager",
        {"value": Input(ValueManager, possible_values=manager_bucket)},
        constructor_counter,
    )
    bucket = CalculationBucket(manager)

    yield_counter.reset()
    constructor_counter.reset()
    with CalculationRunContext():
        cold = bucket.generate_combinations()
        cold_observations = (yield_counter.value, constructor_counter.value)
        yield_counter.reset()
        constructor_counter.reset()
        warm = bucket.generate_combinations()
        warm_observations = (yield_counter.value, constructor_counter.value)

    assert len(cold) == 50
    assert (
        len({cast(ValueManager, combo["value"]).identification["id"] for combo in cold})
        == expected_distinct
    )
    assert warm is cold
    prefix = f"CALC_MANAGER_VALUES_{shape}_50"
    for phase, observations in (
        ("COLD", cold_observations),
        ("WARM", warm_observations),
    ):
        for suffix, observed in zip(
            ("YIELDS", "CONSTRUCTORS"), observations, strict=True
        ):
            perf_budgets.assert_observation(f"{prefix}_{phase}_{suffix}", observed)


@pytest.mark.parametrize("size", [400, 800])
def test_manager_valued_input_preserves_wrappers_between_attribute_reads(
    perf_budgets: PerfBudgets,
    size: int,
) -> None:
    source_wrappers = [ValueManager(id=index % 50) for index in range(size)]
    manager, _input_field = _make_default_calculation_manager(
        f"HydratedManagerValues{size}Manager",
        "value",
        cast(
            Input[type[object]],
            Input(ValueManager, possible_values=source_wrappers),
        ),
    )
    constructor_code = GeneralManager.__dict__["__init__"].__code__

    with (
        CalculationRunContext(),
        count_profiled_calls(
            constructor_code,
            lambda self: self.__class__ is manager,
        ) as outer_constructor_calls,
    ):
        outer_managers = list(CalculationBucket(manager))

        with count_profiled_calls(
            constructor_code,
            lambda self: self.__class__ is ValueManager,
        ) as first_nested_constructor_calls:
            first_wrappers = [
                cast(ValueManager, outer_manager.value)
                for outer_manager in outer_managers
            ]

        with count_profiled_calls(
            constructor_code,
            lambda self: self.__class__ is ValueManager,
        ) as second_nested_constructor_calls:
            second_wrappers = [
                cast(ValueManager, outer_manager.value)
                for outer_manager in outer_managers
            ]

    source_ids = [wrapper.identification["id"] for wrapper in source_wrappers]
    first_ids = [wrapper.identification["id"] for wrapper in first_wrappers]
    second_ids = [wrapper.identification["id"] for wrapper in second_wrappers]
    assert len(outer_managers) == size
    assert first_ids == source_ids
    assert second_ids == source_ids
    assert all(
        hydrated is not source
        for hydrated, source in zip(first_wrappers, source_wrappers, strict=True)
    )
    assert all(
        second is first
        for second, first in zip(second_wrappers, first_wrappers, strict=True)
    )

    prefix = f"CALC_HYDRATED_LIST_{size}"
    perf_budgets.assert_observation(f"{prefix}_OUTER_RESULTS", len(outer_managers))
    perf_budgets.assert_observation(
        f"{prefix}_OUTER_CONSTRUCTORS", outer_constructor_calls.value
    )
    perf_budgets.assert_observation(
        f"{prefix}_FIRST_NESTED_CONSTRUCTORS",
        first_nested_constructor_calls.value,
    )
    perf_budgets.assert_observation(
        f"{prefix}_SECOND_NESTED_CONSTRUCTORS",
        second_nested_constructor_calls.value,
    )


@pytest.mark.parametrize("size", [400, 800])
def test_hydrated_manager_input_performance_gate(
    perf_budgets: PerfBudgets,
    size: int,
) -> None:
    """Gate the initialized production path at zero manager rehydrations."""
    source_wrappers = [ValueManager(id=index % 50) for index in range(size)]
    manager, _input_field = _make_default_calculation_manager(
        f"HydratedManagerGate{size}Manager",
        "value",
        cast(
            Input[type[object]],
            Input(ValueManager, possible_values=source_wrappers),
        ),
    )
    # Late-created test managers need the same descriptor initialization that
    # application startup performs before calculation combinations are built.
    assert GeneralManagerMeta.ensure_attributes_initialized(manager)
    constructor_code = GeneralManager.__dict__["__init__"].__code__

    with (
        CalculationRunContext(),
        count_profiled_calls(
            constructor_code,
            lambda self: self.__class__ is manager,
        ) as outer_constructor_calls,
    ):
        outer_managers = list(CalculationBucket(manager))

        with count_profiled_calls(
            constructor_code,
            lambda self: self.__class__ is ValueManager,
        ) as first_rehydration_calls:
            first_wrappers = [
                cast(ValueManager, outer_manager.value)
                for outer_manager in outer_managers
            ]

        with count_profiled_calls(
            constructor_code,
            lambda self: self.__class__ is ValueManager,
        ) as second_rehydration_calls:
            second_wrappers = [
                cast(ValueManager, outer_manager.value)
                for outer_manager in outer_managers
            ]

    assert len(outer_managers) == size
    assert outer_constructor_calls.value == size
    assert first_rehydration_calls.value == 0
    assert second_rehydration_calls.value == 0
    outer_value_identifications = [
        outer_manager.identification["value"] for outer_manager in outer_managers
    ]
    assert all(
        type(value_identification) is dict
        for value_identification in outer_value_identifications
    )
    assert outer_value_identifications == [
        {"id": source.identification["id"]} for source in source_wrappers
    ]
    assert all(
        hydrated is source
        for hydrated, source in zip(first_wrappers, source_wrappers, strict=True)
    )
    assert all(
        second is first
        for second, first in zip(second_wrappers, first_wrappers, strict=True)
    )
    assert [wrapper.identification["id"] for wrapper in first_wrappers] == [
        wrapper.identification["id"] for wrapper in source_wrappers
    ]

    prefix = f"CALC_HYDRATED_GATE_LIST_{size}"
    perf_budgets.assert_observation(f"{prefix}_OUTER_RESULTS", len(outer_managers))
    perf_budgets.assert_observation(
        f"{prefix}_OUTER_CONSTRUCTORS", outer_constructor_calls.value
    )
    perf_budgets.assert_observation(
        f"{prefix}_FIRST_REHYDRATION_CONSTRUCTORS",
        first_rehydration_calls.value,
    )
    perf_budgets.assert_observation(
        f"{prefix}_SECOND_REHYDRATION_CONSTRUCTORS",
        second_rehydration_calls.value,
    )


@pytest.mark.parametrize("size", [100, 1_000, 100_000])
def test_scalar_terminal_stream_scaling_and_same_test_fallback(
    perf_budgets: PerfBudgets,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
) -> None:
    """Bound admitted terminals and characterize the unchanged fallback path."""

    manager, _input_field = _make_default_calculation_manager(
        f"ScalarTerminalStream{size}Manager",
        "value",
        Input(int, possible_values=range(size)),
    )
    constructor_code = GeneralManager.__dict__["__init__"].__code__

    def run_streamed(
        terminal: Callable[[CalculationBucket], object],
        expected_source_yields: int,
        observation: str,
    ) -> None:
        bucket = CalculationBucket(manager)
        source_yields = Counter()
        original_iter = bucket._iter_terminal_combinations

        def counted_iter() -> Generator[dict[str, object], None, None]:
            for combination in original_iter():
                source_yields.increment()
                yield combination

        monkeypatch.setattr(bucket, "_iter_terminal_combinations", counted_iter)
        with count_profiled_calls(
            constructor_code,
            lambda self: self.__class__ is manager,
        ) as constructor_calls:
            terminal(bucket)
        perf_budgets.assert_observation(
            f"CALC_TERM_SCALAR_{size}_{observation}_SOURCE_YIELDS",
            source_yields.value,
        )
        perf_budgets.assert_observation(
            f"CALC_TERM_SCALAR_{size}_{observation}_CONSTRUCTORS",
            constructor_calls.value,
        )
        assert source_yields.value == expected_source_yields
        assert constructor_calls.value == expected_source_yields

    run_streamed(
        lambda bucket: bucket.first(),
        1,
        "FIRST",
    )

    def multiple_match(bucket: CalculationBucket) -> object:
        with pytest.raises(ValueError):
            return bucket.get()
        return None

    run_streamed(multiple_match, 2, "GET_MULTIPLE")

    candidate = manager(value=0)

    def contains_first(bucket: CalculationBucket) -> object:
        return candidate in bucket

    run_streamed(contains_first, 1, "CONTAINS")

    fallback_source = Counter()
    fallback_values = CountingIterable(range(size), fallback_source)
    fallback_manager, _input_field = _make_default_calculation_manager(
        f"ScalarTerminalFallback{size}Manager",
        "value",
        Input(int, possible_values=fallback_values),
    )
    fallback_bucket = CalculationBucket(fallback_manager)
    with count_profiled_calls(
        constructor_code,
        lambda self: self.__class__ is fallback_manager,
    ) as fallback_constructor_calls:
        fallback_bucket.first()
    perf_budgets.assert_observation(
        f"CALC_TERM_SCALAR_{size}_FALLBACK_SOURCE_YIELDS",
        fallback_source.value,
    )
    perf_budgets.assert_observation(
        f"CALC_TERM_SCALAR_{size}_FALLBACK_CONSTRUCTORS",
        fallback_constructor_calls.value,
    )
    assert fallback_source.value == size
    assert fallback_constructor_calls.value == 1

    last_source = Counter()
    last_values = CountingIterable(range(size), last_source)
    last_manager, _input_field = _make_default_calculation_manager(
        f"ScalarTerminalLast{size}Manager",
        "value",
        Input(int, possible_values=last_values),
    )
    last_bucket = CalculationBucket(last_manager)
    with count_profiled_calls(
        constructor_code,
        lambda self: self.__class__ is last_manager,
    ) as last_constructor_calls:
        last_bucket.last()
    perf_budgets.assert_observation(
        f"CALC_TERM_SCALAR_{size}_LAST_SOURCE_YIELDS",
        last_source.value,
    )
    perf_budgets.assert_observation(
        f"CALC_TERM_SCALAR_{size}_LAST_CONSTRUCTORS",
        last_constructor_calls.value,
    )
    assert last_source.value == size
    assert last_constructor_calls.value == size


@pytest.mark.parametrize("size", [100, 1_000, 100_000])
def test_scalar_terminal_tuple_admission_validation_is_measured(
    perf_budgets: PerfBudgets,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
) -> None:
    """Measure the immutable tuple proof that precedes the bounded stream."""
    manager, _input_field = _make_default_calculation_manager(
        f"ScalarTerminalTuple{size}Manager",
        "value",
        Input(int, possible_values=tuple(range(size))),
    )
    inspected_values = Counter()
    original_support_check = calculation_bucket_module._terminal_scalar_source_supported

    def counted_support_check(source: object) -> bool:
        if type(source) is tuple:
            inspected_values.increment(len(source))
        return original_support_check(source)

    monkeypatch.setattr(
        calculation_bucket_module,
        "_terminal_scalar_source_supported",
        counted_support_check,
    )

    bucket = CalculationBucket(manager)
    assert bucket.first() is not None
    perf_budgets.assert_observation(
        f"CALC_TERM_SCALAR_{size}_TUPLE_ADMISSION_VALUES",
        inspected_values.value,
    )
    assert inspected_values.value == size


@pytest.mark.parametrize("size", [100, 1_000, 10_000])
def test_calculation_result_cache_scaling_and_fallback(
    perf_budgets: PerfBudgets,
    size: int,
) -> None:
    """Measure admitted equivalent plans and unchanged custom-source fallback."""
    provider_calls = Counter()
    property_calls = Counter()

    def provider() -> tuple[int, ...]:
        provider_calls.increment()
        return tuple(range(size))

    class CachedCalculation(GeneralManager):
        class Interface(CalculationInterface):
            value = Input(int, possible_values=provider)

        @graph_ql_property(sortable=True)
        def score(self) -> int:
            property_calls.increment()
            return self.value

    GeneralManagerMeta.ensure_attributes_initialized(CachedCalculation)
    constructor_code = GeneralManager.__dict__["__init__"].__code__
    first_bucket = CalculationBucket(CachedCalculation, sort_key="score")
    second_bucket = CalculationBucket(CachedCalculation, sort_key="score")
    with (
        CalculationRunContext(),
        count_profiled_calls(
            constructor_code,
            lambda self: self.__class__ is CachedCalculation,
        ) as constructors,
    ):
        first = first_bucket.generate_combinations()
        first_observations = (
            provider_calls.value,
            constructors.value,
            property_calls.value,
        )
        provider_calls.reset()
        constructors.reset()
        property_calls.reset()
        second = second_bucket.generate_combinations()
        second_observations = (
            provider_calls.value,
            constructors.value,
            property_calls.value,
        )

    third_bucket = CalculationBucket(CachedCalculation, sort_key="score")
    provider_calls.reset()
    constructors.reset()
    property_calls.reset()
    with (
        CalculationRunContext(),
        count_profiled_calls(
            constructor_code,
            lambda self: self.__class__ is CachedCalculation,
        ) as second_run_constructors,
    ):
        third = third_bucket.generate_combinations()
    second_run_observations = (
        provider_calls.value,
        second_run_constructors.value,
        property_calls.value,
    )

    fallback_source = Counter()

    class CustomValues:
        def __iter__(self):
            for value in range(size):
                fallback_source.increment()
                yield value

    fallback_manager, _input_field = _make_default_calculation_manager(
        f"CalculationResultFallback{size}Manager",
        "value",
        Input(int, possible_values=CustomValues()),
    )
    fallback_first = CalculationBucket(fallback_manager, sort_key="value")
    fallback_second = CalculationBucket(fallback_manager, sort_key="value")
    with count_profiled_calls(
        constructor_code,
        lambda self: self.__class__ is fallback_manager,
    ) as fallback_constructors:
        fallback_first.generate_combinations()
        fallback_second.generate_combinations()

    assert first == second == third
    assert len(first) == size
    assert first_observations == (1, size, size)
    assert second_observations == (0, 0, 0)
    assert second_run_observations == (1, size, size)
    perf_budgets.assert_observation(
        f"CALC_RESULT_CACHE_{size}_FIRST_PROVIDER_CALLS", first_observations[0]
    )
    perf_budgets.assert_observation(
        f"CALC_RESULT_CACHE_{size}_FIRST_CONSTRUCTORS", first_observations[1]
    )
    perf_budgets.assert_observation(
        f"CALC_RESULT_CACHE_{size}_FIRST_PROPERTY_CALLS", first_observations[2]
    )
    perf_budgets.assert_observation(
        f"CALC_RESULT_CACHE_{size}_HIT_PROVIDER_CALLS", second_observations[0]
    )
    perf_budgets.assert_observation(
        f"CALC_RESULT_CACHE_{size}_HIT_CONSTRUCTORS", second_observations[1]
    )
    perf_budgets.assert_observation(
        f"CALC_RESULT_CACHE_{size}_HIT_PROPERTY_CALLS", second_observations[2]
    )
    perf_budgets.assert_observation(
        f"CALC_RESULT_CACHE_{size}_SECOND_RUN_PROVIDER_CALLS",
        second_run_observations[0],
    )
    perf_budgets.assert_observation(
        f"CALC_RESULT_CACHE_{size}_SECOND_RUN_CONSTRUCTORS", second_run_observations[1]
    )
    perf_budgets.assert_observation(
        f"CALC_RESULT_CACHE_{size}_SECOND_RUN_PROPERTY_CALLS",
        second_run_observations[2],
    )
    perf_budgets.assert_observation(
        f"CALC_RESULT_CACHE_{size}_FALLBACK_SOURCE_YIELDS",
        fallback_source.value,
    )
    perf_budgets.assert_observation(
        f"CALC_RESULT_CACHE_{size}_FALLBACK_CONSTRUCTORS",
        fallback_constructors.value,
    )
    assert fallback_source.value == size * 2
    assert fallback_constructors.value == 0
