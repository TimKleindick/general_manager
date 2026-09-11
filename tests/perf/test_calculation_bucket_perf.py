from __future__ import annotations

from collections.abc import Generator, Iterable
from dataclasses import dataclass
from typing import cast

import pytest
from django.core.cache import cache as django_cache
from django.test import override_settings

from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.cache import _dependency_graph as dependency_graph
from general_manager.cache import run_context_lru as run_context_lru_module
from general_manager.cache.cache_decorator import cached
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_cache import (
    _trusted_dependency_cache_hit,
    replay_dependency_cache_hit,
)
from general_manager.cache import run_context as run_context_module
from general_manager.cache.run_context import CalculationRunContext, _RunCacheEntry
from general_manager.cache.run_context_lru import ProcessRunContextCacheBudget
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
    first_bucket = CalculationBucket(manager).sort("a")
    second_bucket = CalculationBucket(manager).sort("a")

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


def _is_dependency_tuple(value: object) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 3
        and all(isinstance(item, str) for item in value)
    )


def _count_dependency_tuple_sets(monkeypatch: pytest.MonkeyPatch) -> Counter:
    counter = Counter()

    class DependencyTupleCountingSet(set[object]):
        def add(self, value: object) -> None:
            if _is_dependency_tuple(value):
                counter.increment()
            super().add(value)

        def update(self, values: Iterable[object]) -> None:
            for value in values:
                self.add(value)

    monkeypatch.setattr(
        dependency_graph,
        "set",
        DependencyTupleCountingSet,
        raising=False,
    )
    # Prove the hook observes real capture and materialization work.
    probe = dependency_graph.DependencyCapture()
    probe.add(("InstrumentationProbe", "all", ""))
    dependency_graph.materialize_dependencies(probe.freeze())
    assert counter.value == 2, (
        "Dependency set instrumentation is inactive or incomplete"
    )
    counter.reset()
    return counter


def _graph_shape(
    roots: Iterable[object],
) -> tuple[int, int, int]:
    """Return unique node, edge, and direct-membership counts by identity."""
    pending = [
        root for root in roots if isinstance(root, dependency_graph.DependencySnapshot)
    ]
    node_ids: set[int] = set()
    dependency_container_ids: set[int] = set()
    nodes = 0
    edges = 0
    memberships = 0
    while pending:
        node = pending.pop()
        node_id = id(node)
        if node_id in node_ids:
            continue
        node_ids.add(node_id)
        nodes += 1
        edges += len(node.children)
        if node.block is not None:
            dependencies = node.block.dependencies
            dependencies_id = id(dependencies)
            if dependencies_id not in dependency_container_ids:
                dependency_container_ids.add(dependencies_id)
                memberships += len(dependencies)
        pending.extend(node.children)
    return nodes, edges, memberships


@pytest.mark.parametrize(("breadth", "parents"), [(16, 12), (32, 12), (16, 24)])
def test_shared_run_dependency_graph_fanout(
    perf_budgets: PerfBudgets,
    monkeypatch: pytest.MonkeyPatch,
    breadth: int,
    parents: int,
) -> None:
    """Keep a broad source graph shared by cached parents with unique reads."""
    dependency_visits = _count_dependency_tuple_sets(monkeypatch)
    django_cache.clear()
    source_calls = Counter()
    parent_calls = Counter()
    published: list[set[tuple[str, str, str]]] = []

    @cached(cache="run")
    def source() -> int:
        source_calls.increment()
        for index in range(breadth):
            DependencyTracker.track("PerfSource", "identification", str(index))
        return breadth

    @cached(cache="run")
    def parent(index: int) -> int:
        parent_calls.increment()
        DependencyTracker.track("PerfParent", "identification", str(index))
        return source() + index

    @cached(
        cache="dependency",
        record_fn=lambda _key, dependencies: published.append(set(dependencies)),
    )
    def persistent_parent() -> int:
        return sum(parent(index) for index in range(parents))

    with CalculationRunContext() as context:
        cold = [parent(index) for index in range(parents)]
        direct_visits = dependency_visits.value
        run_entries = [
            value
            for value in context._values.values()
            if isinstance(value, _RunCacheEntry)
        ]
        nodes, edges, memberships = _graph_shape(
            entry.dependency_root for entry in run_entries
        )

        dependency_visits.reset()
        warm = [parent(index) for index in range(parents)]
        plain_run_hit_visits = dependency_visits.value

        dependency_visits.reset()
        persistent = persistent_parent()
        persistent_materialization_visits = dependency_visits.value

    assert cold == [breadth + index for index in range(parents)]
    assert warm == cold
    assert persistent == sum(cold)
    assert source_calls.value == 1
    assert parent_calls.value == parents
    assert (nodes, edges, memberships) == (parents + 1, parents, breadth + parents)
    assert direct_visits == breadth + parents
    assert plain_run_hit_visits == 0
    assert persistent_materialization_visits == breadth + parents
    expected_published = {
        ("PerfSource", "identification", str(index)) for index in range(breadth)
    } | {("PerfParent", "identification", str(index)) for index in range(parents)}
    assert published == [expected_published]

    if (breadth, parents) == (16, 24):
        for suffix, observed in (
            ("DIRECT_VISITS", direct_visits),
            ("PLAIN_HIT_VISITS", plain_run_hit_visits),
            ("MATERIALIZE_VISITS", persistent_materialization_visits),
            ("NODES", nodes),
            ("EDGES", edges),
            ("MEMBERSHIPS", memberships),
        ):
            perf_budgets.assert_observation(
                f"RUN_GRAPH_FANOUT_16X24_{suffix}", observed
            )


@pytest.mark.parametrize("depth", [6, 12])
def test_shared_run_dependency_graph_diamond_depth(
    perf_budgets: PerfBudgets,
    monkeypatch: pytest.MonkeyPatch,
    depth: int,
) -> None:
    """Retain a repeated diamond as nodes and edges instead of flattened leaves."""
    calls = Counter()
    dependency_visits = _count_dependency_tuple_sets(monkeypatch)
    django_cache.clear()

    @cached(cache="run")
    def leaf() -> int:
        calls.increment()
        DependencyTracker.track("PerfDiamond", "identification", "leaf")
        return 1

    left = leaf
    right = leaf
    for level in range(1, depth + 1):
        previous_left, previous_right = left, right

        def make_node(name: str, left_child=previous_left, right_child=previous_right):
            @cached(cache="run")
            def node(cache_key: str) -> int:
                del cache_key
                calls.increment()
                DependencyTracker.track("PerfDiamond", "identification", name)
                if left_child is right_child:
                    return left_child()
                return left_child() + right_child()

            return lambda: node(name)

        left = make_node(f"{level}-left")
        right = make_node(f"{level}-right")

    published: list[set[tuple[str, str, str]]] = []

    @cached(
        cache="dependency",
        record_fn=lambda _key, dependencies: published.append(set(dependencies)),
    )
    def persistent_parent() -> tuple[int, int]:
        return left(), right()

    with CalculationRunContext() as context:
        cold = (left(), right())
        cold_calls = calls.value
        entries = [
            value
            for value in context._values.values()
            if isinstance(value, _RunCacheEntry)
        ]
        nodes, edges, _memberships = _graph_shape(
            entry.dependency_root for entry in entries
        )
        calls.reset()
        warm = (left(), right())
        warm_calls = calls.value
        dependency_visits.reset()
        persistent = persistent_parent()
        materialization_visits = dependency_visits.value

    assert cold == warm
    assert cold == (2 ** (depth - 1),) * 2
    assert persistent == cold
    assert cold_calls == 2 * depth + 1
    assert warm_calls == 0
    assert (nodes, edges) == (2 * depth + 1, 4 * depth - 2)
    assert _memberships == 2 * depth + 1
    assert materialization_visits == 2 * depth + 1
    assert published == [
        {
            ("PerfDiamond", "identification", "leaf"),
            *{
                ("PerfDiamond", "identification", f"{level}-{side}")
                for level in range(1, depth + 1)
                for side in ("left", "right")
            },
        }
    ]
    if depth == 12:
        for suffix, observed in (
            ("COLD_CALLS", cold_calls),
            ("WARM_CALLS", warm_calls),
            ("NODES", nodes),
            ("EDGES", edges),
            ("MATERIALIZE_VISITS", materialization_visits),
        ):
            perf_budgets.assert_observation(f"RUN_GRAPH_DIAMOND_12_{suffix}", observed)


def test_shared_trusted_hit_materializes_once_for_persistent_parent(
    perf_budgets: PerfBudgets,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Materialize a shared trusted dependency container once at publication."""
    wrapper_count = 12
    shared_dependencies = frozenset(
        ("PerfShared", "identification", str(index)) for index in range(16)
    )
    dependency_visits = _count_dependency_tuple_sets(monkeypatch)
    django_cache.clear()
    published: list[set[tuple[str, str, str]]] = []
    wrappers = []
    for index in range(wrapper_count):
        hit = _trusted_dependency_cache_hit(index, shared_dependencies)

        def make_wrapper(index: int, hit=hit):
            @cached(cache="run")
            def wrapper(cache_key: int) -> int:
                del cache_key
                DependencyTracker.track("PerfWrapper", "identification", str(index))
                replay_dependency_cache_hit(hit)
                return index

            return lambda: wrapper(index)

        wrappers.append(make_wrapper(index))

    @cached(
        cache="dependency",
        record_fn=lambda _key, dependencies: published.append(set(dependencies)),
    )
    def persistent_parent() -> int:
        return sum(wrapper() for wrapper in wrappers)

    with CalculationRunContext():
        assert [wrapper() for wrapper in wrappers] == list(range(wrapper_count))
        dependency_visits.reset()
        assert persistent_parent() == sum(range(wrapper_count))
        materialization_visits = dependency_visits.value

    assert materialization_visits == len(shared_dependencies) + wrapper_count
    assert published == [
        set(shared_dependencies)
        | {
            ("PerfWrapper", "identification", str(index))
            for index in range(wrapper_count)
        }
    ]
    perf_budgets.assert_observation(
        "RUN_GRAPH_MIXED_12_MATERIALIZE_VISITS",
        materialization_visits,
    )


class _GetCountingDict(dict[int, int]):
    def __init__(self) -> None:
        super().__init__()
        self.get_calls = Counter()

    def get(self, key: int, default: int | None = None) -> int | None:
        self.get_calls.increment()
        return super().get(key, default)


def test_shared_graph_ledger_avoids_rewalking_retained_root(
    perf_budgets: PerfBudgets,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound finite-budget ledger work to the repeated root check."""
    shared = frozenset(
        ("PerfLedger", "identification", str(index)) for index in range(16)
    )
    source = dependency_graph.snapshot_from_dependencies(shared)
    aggregate = dependency_graph.DependencyCapture()
    for index in range(24):
        parent = dependency_graph.DependencyCapture()
        parent.add(("PerfLedgerParent", "identification", str(index)))
        parent.attach(source)
        aggregate.attach(parent.freeze())
    root = aggregate.freeze()
    coordinator = ProcessRunContextCacheBudget()
    monkeypatch.setattr(run_context_module, "run_context_cache_budget", coordinator)
    estimator_visits: list[object] = []
    monkeypatch.setattr(
        run_context_lru_module,
        "_calibration_visit_observer",
        estimator_visits.append,
    )

    with override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 1_000_000}):
        with CalculationRunContext() as context:
            ledger = coordinator._graph_ledger
            ledger.node_references = _GetCountingDict()
            ledger.dependency_references = _GetCountingDict()
            context.set("first", _RunCacheEntry(value=1, dependency_root=root))
            first_estimated_bytes = coordinator.estimated_bytes
            first_node_lookups = ledger.node_references.get_calls.value
            first_dependency_lookups = ledger.dependency_references.get_calls.value
            estimator_visits.clear()
            ledger.node_references.get_calls.reset()
            ledger.dependency_references.get_calls.reset()
            context.set("second", _RunCacheEntry(value=2, dependency_root=root))
            repeated_node_lookups = ledger.node_references.get_calls.value
            repeated_dependency_lookups = ledger.dependency_references.get_calls.value
            new_parent_lookups: list[tuple[int, int]] = []
            for index in range(3):
                parent = dependency_graph.DependencyCapture()
                parent.add(("PerfLedgerNew", "identification", str(index)))
                parent.attach(root)
                ledger.node_references.get_calls.reset()
                ledger.dependency_references.get_calls.reset()
                context.set(
                    f"new-{index}",
                    _RunCacheEntry(value=index, dependency_root=parent.freeze()),
                )
                new_parent_lookups.append(
                    (
                        ledger.node_references.get_calls.value,
                        ledger.dependency_references.get_calls.value,
                    )
                )
            retained_values = tuple(context._values)

    assert first_estimated_bytes > 0
    assert first_node_lookups > 24
    assert first_dependency_lookups > 24
    # Preparation and commit each consult the retained node once.  Neither
    # phase walks its children or estimates its dependency payload again.
    assert repeated_node_lookups == 2
    assert repeated_dependency_lookups == 0
    assert new_parent_lookups == [(4, 2)] * 3
    assert len(retained_values) == 5
    assert id(shared) not in {id(value) for value in estimator_visits}
    assert ledger.total_bytes == 0
    assert ledger.node_references == {}
    assert ledger.dependency_references == {}
    perf_budgets.assert_observation(
        "RUN_GRAPH_LEDGER_RETAIN_NODE_LOOKUPS",
        repeated_node_lookups,
    )
    perf_budgets.assert_observation(
        "RUN_GRAPH_LEDGER_RETAIN_DEPENDENCY_LOOKUPS",
        repeated_dependency_lookups,
    )
