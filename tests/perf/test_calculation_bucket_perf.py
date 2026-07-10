from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import cast

import pytest

from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.cache.run_context import CalculationRunContext
from general_manager.interface.interfaces.calculation import CalculationInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from tests.perf.support import (
    Counter,
    CountingIterable,
    PerfBudgets,
    capture_diagnostics,
)

pytestmark = pytest.mark.perf


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
