# type: ignore
from copy import copy, deepcopy
from django.test import TestCase
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from general_manager.bucket.calculation_bucket import (
    CalculationBucket,
    MissingCalculationMatchError,
    MultipleCalculationMatchError,
    _CALCULATION_RESULT_UNSUPPORTED,
    _BuiltinRangeEnumerationWitness,
    _CalculationCacheIdentityToken,
    _calculation_cache_callable_token,
    _calculation_cache_clone,
    _calculation_cache_filter_token,
    _calculation_cache_freeze,
    _calculation_cache_identity_token,
    _terminal_scalar_source_supported,
    _trusted_enumeration_evidence,
)
from general_manager.bucket import calculation_bucket as calculation_bucket_module
from general_manager.cache.run_context import (
    CALCULATION_BUCKET_RESULT_MISSING,
    CalculationRunContext,
    current_calculation_run_context,
)
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.api.property import graph_ql_property
from general_manager.interface import CalculationInterface
from general_manager.manager.input import DateRangeDomain, NumericRangeDomain, Input
from general_manager.manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from tests.utils.simple_manager_interface import SimpleBucket
from typing import ClassVar


# Create a dummy CalculationInterface with no input fields for simplicity
class DummyCalculationInterface(CalculationInterface):
    input_fields: ClassVar[dict] = {}


# Dummy manager class that uses the dummy interface
class DummyGeneralManager:
    Interface = DummyCalculationInterface

    def __init__(self, **kwargs):
        # Initialize with any keyword arguments, simulating a manager

        """
        Initializes the dummy manager with provided keyword arguments.

        Stores all keyword arguments for later comparison and representation.
        """
        self.kwargs = kwargs
        self.identification = dict(kwargs)

    def __eq__(self, value: object) -> bool:
        """
        Checks equality with another DummyGeneralManager based on initialization arguments.

        Returns:
            bool: True if the other object is a DummyGeneralManager and has the same kwargs; otherwise, False.
        """
        if not isinstance(value, DummyGeneralManager):
            return False
        return self.kwargs == value.kwargs

    def __repr__(self):
        """
        Returns a string representation of the DummyGeneralManager instance with its initialization arguments.
        """
        return f"DummyGeneralManager({self.kwargs})"


# Link parent class for the interface
DummyCalculationInterface._parent_class = DummyGeneralManager


class CountingIterable:
    def __init__(self, values):
        self.values = values
        self.yield_count = 0

    def __iter__(self):
        for value in self.values:
            self.yield_count += 1
            yield value


@patch(
    "general_manager.bucket.calculation_bucket.parse_filters",
    return_value={"dummy": {"filter_kwargs": {}}},
)
class TestCalculationBucket(TestCase):
    def test_initialization_defaults(self, _mock_parse):
        # Test basic initialization without optional parameters

        """
        Tests that CalculationBucket initializes with default values when only the manager class is provided.

        Verifies that filters, excludes, sort key, and reverse flag are set to their defaults, and that input fields are sourced from the associated interface.
        """
        bucket = CalculationBucket(manager_class=DummyGeneralManager)
        self.assertIsInstance(bucket, CalculationBucket)
        self.assertEqual(bucket._manager_class, DummyGeneralManager)
        self.assertEqual(bucket.filters, {})
        self.assertEqual(bucket.excludes, {})
        self.assertIsNone(bucket.sort_key)
        self.assertFalse(bucket.reverse)
        # input_fields should come from the interface
        self.assertEqual(bucket.input_fields, DummyCalculationInterface.input_fields)

    def test_initialization_with_filters_and_excludes(self, _mock_parse):
        # Filters and excludes passed directly to constructor

        """
        Tests that CalculationBucket initializes with provided filter and exclude definitions, sort key, and reverse flag.

        Verifies that the constructor correctly assigns the given filters, excludes, sort key, and reverse attributes.
        """
        fdefs = {"f": {"filter_kwargs": {"f": 1}}}
        edefs = {"e": {"filter_kwargs": {"e": 2}}}
        bucket = CalculationBucket(
            manager_class=DummyGeneralManager,
            filter_definitions=fdefs,
            exclude_definitions=edefs,
            sort_key="key",
            reverse=True,
        )
        self.assertEqual(bucket.filter_definitions, fdefs)
        self.assertEqual(bucket.exclude_definitions, edefs)
        self.assertEqual(bucket.sort_key, "key")
        self.assertTrue(bucket.reverse)

    def test_reduce_and_setstate(self, _mock_parse):
        # Test pickling support

        """
        Tests that CalculationBucket supports pickling and unpickling via __reduce__ and __setstate__.

        Verifies that the reduced state includes current combinations and that state restoration
        correctly sets the internal combinations on a new instance.
        """
        bucket = CalculationBucket(DummyGeneralManager, {"a": 1}, {"b": 2}, "k", True)
        # Prepopulate state
        bucket._data = [{"x": 10}]
        cls, args, state = bucket.__reduce__()
        # Check reduce data
        self.assertEqual(cls, CalculationBucket)
        self.assertEqual(args, (DummyGeneralManager, {"a": 1}, {"b": 2}, "k", True))
        self.assertIn("data", state)
        # Restore state on new instance
        new_bucket = CalculationBucket(*args)
        new_bucket.__setstate__(state)
        self.assertEqual(new_bucket._data, [{"x": 10}])

    def test_or_with_same_bucket(self, _mock_parse):
        # Combining two buckets of same class should intersect filters/excludes

        """
        Tests that combining two CalculationBucket instances with the same manager class using the bitwise OR operator results in a new bucket containing only the filters and excludes that are identical in both buckets.
        """
        b1 = CalculationBucket(DummyGeneralManager, {"f1": 1}, {"e1": 2})
        b2 = CalculationBucket(
            DummyGeneralManager, {"f1": 1, "f2": 3}, {"e1": 2, "e2": 4}
        )
        combined = b1 | b2
        self.assertIsInstance(combined, CalculationBucket)
        # Only common identical definitions should remain
        self.assertEqual(combined.filter_definitions, {"f1": 1})
        self.assertEqual(combined.exclude_definitions, {"e1": 2})

    def test_or_with_invalid(self, _mock_parse):
        """
        Tests that combining a CalculationBucket with an incompatible type or a bucket of a different manager class raises a TypeError.
        """
        b1 = CalculationBucket(DummyGeneralManager)
        # Combining with different type should raise
        with self.assertRaises(TypeError):
            _ = b1 | 123

        # Combining with bucket of different manager class should raise
        class OtherManager:
            Interface = DummyCalculationInterface

        b2 = CalculationBucket(OtherManager)
        with self.assertRaises(TypeError):
            _ = b1 | b2

    def test_str_and_repr_formatting(self, _mock_parse):
        """
        Tests the string and repr formatting of CalculationBucket instances.

        Verifies that the string representation displays the total count and up to five combinations, using an ellipsis if more exist, and that the repr shows the constructor parameters.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        # Manually set combinations for string formatting tests
        combos = [{"x": i} for i in range(7)]
        bucket._data = combos
        s = str(bucket)
        # Should show total count and at most 5 entries
        self.assertTrue(s.startswith("CalculationBucket (7)["))
        self.assertIn("...", s)
        # Test below threshold (no ellipsis)
        bucket._data = combos[:3]
        s2 = str(bucket)
        self.assertFalse("..." in s2)

        s3 = repr(bucket)
        self.assertEqual(
            s3,
            f"CalculationBucket({DummyGeneralManager.__name__}, {{}}, {{}}, None, False)",
        )

    def test_all_iter_len_count(self, _mock_parse):
        """
        Tests that CalculationBucket's all(), iteration, count(), and length methods behave as expected.

        Verifies that all() returns the bucket itself, iteration yields one manager instance per combination, and both count() and len() return the correct number of combinations.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        # Set a single empty combination so manager(**{}) works
        bucket._data = [{}] * 4
        # all() returns self
        self.assertEqual(bucket.all(), bucket)
        # Iteration yields one manager per combo
        items = list(bucket)
        self.assertEqual(len(items), 4)
        # count() and len() reflect number of combos
        self.assertEqual(bucket.count(), 4)
        self.assertEqual(len(bucket), 4)

    def test_first_last_empty_and_nonempty(self, _mock_parse):
        """
        Tests the behavior of the `first()` and `last()` methods on a `CalculationBucket`.

        Verifies that `first()` and `last()` return `None` when the bucket has no combinations, and return the same manager instance when only one combination exists.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        # Empty combos
        bucket._data = []
        self.assertIsNone(bucket.first())
        self.assertIsNone(bucket.last())
        # Single combo
        bucket._data = [{"test": 1}]
        first = bucket.first()
        last = bucket.last()
        self.assertIsNotNone(first)
        self.assertEqual(first, last)

    def test_getitem_index_and_slice(self, _mock_parse):
        """
        Tests that indexing a CalculationBucket returns a manager instance and slicing returns a new CalculationBucket with the correct subset of combinations.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        # Create distinct combos for index and slice
        bucket._data = [{"i": 1}, {"i": 2}, {"i": 3}]
        # Index __getitem__
        mgr = bucket[1]
        self.assertIsInstance(mgr, DummyGeneralManager)
        # Slice __getitem__
        sliced = bucket[0:2]
        self.assertIsInstance(sliced, CalculationBucket)
        # Sliced bucket should have its own combinations
        self.assertEqual(sliced._data, [{"i": 1}, {"i": 2}])

    def test_sort_returns_new_bucket(self, _mock_parse):
        """
        Tests that the sort() method returns a new CalculationBucket with updated sort key and reverse flag, leaving the original bucket unchanged.
        """
        bucket = CalculationBucket(DummyGeneralManager, {"a": 1}, {"b": 2}, None, False)
        sorted_bucket = bucket.sort(key="a", reverse=True)
        self.assertIsInstance(sorted_bucket, CalculationBucket)
        # Original bucket unchanged
        self.assertIsNone(bucket.sort_key)
        # New bucket has updated sort settings
        self.assertEqual(sorted_bucket.sort_key, "a")
        self.assertTrue(sorted_bucket.reverse)


@patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
class TestGenerateCombinations(TestCase):
    def _make_bucket_with_fields(self, fields):
        # Dynamically create an interface and manager class with given input_fields

        """
        Create a CalculationBucket configured with a manager whose interface exposes the given input fields.

        Parameters:
            fields (list): Input field definitions to assign to the generated interface's `input_fields`.

        Returns:
            CalculationBucket: An instance whose manager class has `Interface.input_fields` set to `fields`.
        """

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = fields

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                self.identification = kwargs

        DynInterface._parent_class = DynManager
        return CalculationBucket(DynManager)

    def test_basic_cartesian_product(self, _mock_parse):
        # Two independent fields produce a Cartesian product

        """
        Tests that generate_combinations produces the Cartesian product of independent input fields.

        Verifies that two fields with independent possible values yield all possible combinations.
        """
        fields = {
            "num": Input(type=int, possible_values=[1, 2]),
            "char": Input(type=str, possible_values=["a", "b"]),
        }
        bucket = self._make_bucket_with_fields(fields)
        combos = bucket.generate_combinations()
        # Expect 4 combinations
        expected = [
            {"num": 1, "char": "a"},
            {"num": 1, "char": "b"},
            {"num": 2, "char": "a"},
            {"num": 2, "char": "b"},
        ]
        # Compare as multisets since insertion order of fields may vary
        self.assertCountEqual(combos, expected)

    def test_safe_static_domains_are_classified_once_per_generation_plan(
        self,
        _mock_parse,
    ):
        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "outer": Input(int, possible_values=range(3)),
                "inner": Input(int, possible_values=(10, 20)),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)

        with patch.object(
            calculation_bucket_module,
            "_static_domain_snapshot",
            wraps=calculation_bucket_module._static_domain_snapshot,
        ) as snapshots:
            combinations = bucket.generate_combinations()

        self.assertEqual(
            combinations,
            [
                {"outer": outer, "inner": inner}
                for inner in (10, 20)
                for outer in range(3)
            ],
        )
        self.assertEqual(snapshots.call_count, 2)

    def test_provider_returning_tuple_is_not_classified_as_static(self, _mock_parse):
        calls: list[int] = []

        def provider(outer):
            calls.append(outer)
            return (outer, outer + 10)

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "outer": Input(int, possible_values=(1, 2)),
                "inner": Input(
                    int,
                    possible_values=provider,
                    depends_on=["outer"],
                ),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        combinations = CalculationBucket(DynManager).generate_combinations()

        self.assertCountEqual(
            combinations,
            [
                {"outer": outer, "inner": inner}
                for outer in (1, 2)
                for inner in (outer, outer + 10)
            ],
        )
        self.assertEqual(calls, [1, 2])

    def test_range_snapshot_preserves_trusted_evidence_source(self, _mock_parse):
        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "value": Input(
                    int,
                    possible_values=NumericRangeDomain(1, 2),
                ),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)
        combinations = bucket._generate_input_combinations(
            ["value"],
            {},
            {},
            retain_evidence=True,
        )

        self.assertEqual(combinations, [{"value": 1}, {"value": 2}])
        self.assertTrue(
            all(
                bucket._lookup_combination_evidence(combination) is not None
                for combination in combinations
            )
        )

    def test_mutated_range_state_is_rejected_before_snapshot(self, _mock_parse):
        domain = NumericRangeDomain(1, 2)
        object.__setattr__(domain, "step", 0)

        self.assertIs(
            calculation_bucket_module._static_domain_snapshot(
                Input(int, possible_values=domain),
                domain,
            ),
            calculation_bucket_module._STATIC_DOMAIN_UNSUPPORTED,
        )

    def test_mutated_date_range_state_is_rejected_before_snapshot(self, _mock_parse):
        invalid_states = (
            {"step": 0},
            {"start": date(2025, 1, 3)},
            {"frequency": "invalid"},
        )
        for state in invalid_states:
            domain = DateRangeDomain(date(2025, 1, 1), date(2025, 1, 2))
            for name, value in state.items():
                object.__setattr__(domain, name, value)
            self.assertIs(
                calculation_bucket_module._static_domain_snapshot(
                    Input(date, possible_values=domain),
                    domain,
                ),
                calculation_bucket_module._STATIC_DOMAIN_UNSUPPORTED,
            )

    def test_range_snapshot_iteration_error_does_not_publish_partial_entry(
        self,
        _mock_parse,
    ):
        class RaisingValues:
            def __iter__(self):
                yield 1
                raise RuntimeError

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "value": Input(int, possible_values=RaisingValues()),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)
        snapshots: dict[str, object] = {}
        with self.assertRaises(RuntimeError):
            list(
                bucket._iter_input_combinations(
                    ["value"],
                    {},
                    {},
                    snapshot_iterables=True,
                    static_snapshots=snapshots,
                )
            )
        entry = snapshots["value"]
        self.assertIs(
            entry.snapshot,
            calculation_bucket_module._STATIC_DOMAIN_UNSUPPORTED,
        )

    def test_filter_callbacks_keep_static_source_materialization_timing(
        self,
        _mock_parse,
    ):
        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "outer": Input(int, possible_values=(1, 2, 3)),
                "inner": Input(int, possible_values=(10, 20)),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)
        events: list[int] = []

        def record_inner(value):
            events.append(value)
            return True

        snapshots: dict[str, object] = {}
        list(
            bucket._iter_input_combinations(
                ["outer", "inner"],
                {"inner": {"filter_funcs": [record_inner]}},
                {},
                snapshot_iterables=True,
                static_snapshots=snapshots,
            )
        )

        self.assertEqual(events, [10, 20, 10, 20, 10, 20])
        self.assertNotIn("inner", snapshots)

    def test_static_source_mutation_refreshes_snapshot_between_branches(
        self,
        _mock_parse,
    ):
        inner_field = Input(int, possible_values=(10, 20))

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "outer": Input(int, possible_values=(1, 2)),
                "inner": inner_field,
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)
        snapshots: dict[str, object] = {}

        def mutate_inner(value):
            if value == 2:
                inner_field.possible_values = (30, 40)
            return True

        combinations = list(
            bucket._iter_input_combinations(
                ["outer", "inner"],
                {"outer": {"filter_funcs": [mutate_inner]}},
                {},
                snapshot_iterables=False,
                static_snapshots=snapshots,
            )
        )

        self.assertEqual(
            combinations,
            [
                {"outer": 1, "inner": 10},
                {"outer": 1, "inner": 20},
                {"outer": 2, "inner": 30},
                {"outer": 2, "inner": 40},
            ],
        )

    def test_generate_combinations_does_not_instantiate_managers_without_property_work(
        self, _mock_parse
    ):
        calls = []

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "num": Input(type=int, possible_values=[1, 2, 3]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.num = kwargs["num"]

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager)

        combos = bucket.generate_combinations()

        self.assertEqual(combos, [{"num": 1}, {"num": 2}, {"num": 3}])
        self.assertEqual(calls, [])

    def test_iter_instantiates_managers_once_for_input_only_bucket(self, _mock_parse):
        calls = []

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "num": Input(type=int, possible_values=[1, 2, 3]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.num = kwargs["num"]

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager)

        items = list(bucket)

        self.assertEqual([item.identification for item in items], bucket._data)
        self.assertEqual(
            calls,
            [{"num": 1}, {"num": 2}, {"num": 3}],
        )

    def test_manager_input_lifecycle_across_bucket_transformations(self, _mock_parse):
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(str)

        class StaticManagerBucket(SimpleBucket):
            def filter(self, **kwargs):
                return self

            def exclude(self, **kwargs):
                return self

        related_values = [RelatedManager(str(value)) for value in (1, 2, 3)]
        static_source = StaticManagerBucket(RelatedManager, related_values)

        class StaticManagerCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager, possible_values=static_source)

            @property
            def numeric_id(self):
                return int(self.related.identification["id"])

        GeneralManagerMeta.ensure_attributes_initialized(StaticManagerCalculation)
        bucket = CalculationBucket(StaticManagerCalculation)
        bucket._filters = {}
        bucket._excludes = {}
        combinations = bucket.generate_combinations()

        self.assertEqual(
            [
                combination["related"].identification["id"]
                for combination in combinations
            ],
            ["1", "2", "3"],
        )
        self.assertEqual(
            [combination["related"] for combination in combinations],
            related_values,
        )

        first_iteration = list(bucket)
        second_iteration = list(bucket)
        for managers in (first_iteration, second_iteration):
            self.assertEqual(
                [manager.identification["related"]["id"] for manager in managers],
                ["1", "2", "3"],
            )
            for manager, expected_related in zip(
                managers,
                related_values,
                strict=True,
            ):
                parsed_wrapper = vars(manager._interface)[
                    "_gm_seeded_input_values_cache"
                ]["related"]
                self.assertIs(parsed_wrapper, expected_related)
                self.assertIs(manager.related, parsed_wrapper)
        self.assertTrue(
            all(
                first is not second
                for first, second in zip(
                    first_iteration,
                    second_iteration,
                    strict=True,
                )
            )
        )

        copied = copy(bucket)
        sliced = bucket[1:]
        united = bucket[:1] | bucket[1:]
        united._filters = {}
        united._excludes = {}

        class ListManagerCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager, possible_values=related_values)

        GeneralManagerMeta.ensure_attributes_initialized(ListManagerCalculation)
        list_bucket = CalculationBucket(ListManagerCalculation)
        list_bucket._filters = {}
        list_bucket._excludes = {}
        deep_copied = deepcopy(list_bucket)
        transformed = (copied, sliced, deep_copied, united)
        expected_ids = (
            ["1", "2", "3"],
            ["2", "3"],
            ["1", "2", "3"],
            ["1", "2", "3"],
        )
        for transformed_bucket, ids in zip(
            transformed,
            expected_ids,
            strict=True,
        ):
            transformed_managers = list(transformed_bucket)
            self.assertEqual(
                [
                    manager.identification["related"]["id"]
                    for manager in transformed_managers
                ],
                ids,
            )
            for manager in transformed_managers:
                emitted_wrapper = vars(manager._interface)[
                    "_gm_seeded_input_values_cache"
                ]["related"]
                first_access = manager.related
                self.assertIs(first_access, emitted_wrapper)
                self.assertIs(manager.related, first_access)

        def filtered_sorted_bucket():
            current_bucket = CalculationBucket(
                StaticManagerCalculation,
                sort_key="numeric_id",
            )
            current_bucket._excludes = {}
            current_bucket._filters = {
                "numeric_id": {"filter_funcs": [lambda value: value >= 2]}
            }
            return current_bucket

        filtered_combinations = filtered_sorted_bucket().generate_combinations()
        self.assertEqual(
            [combination["related"]["id"] for combination in filtered_combinations],
            ["2", "3"],
        )
        filtered_managers = list(filtered_sorted_bucket())
        self.assertEqual(
            [manager.identification["related"]["id"] for manager in filtered_managers],
            ["2", "3"],
        )
        for manager in filtered_managers:
            emitted_wrapper = vars(manager._interface)["_gm_seeded_input_values_cache"][
                "related"
            ]
            first_access = manager.related
            self.assertIs(first_access, emitted_wrapper)
            self.assertIs(manager.related, first_access)

        provider_calls = []

        def manager_provider():
            provider_calls.append(None)
            return static_source

        class CallableManagerCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(RelatedManager, possible_values=manager_provider)

        GeneralManagerMeta.ensure_attributes_initialized(CallableManagerCalculation)
        callable_bucket = CalculationBucket(CallableManagerCalculation)
        callable_bucket._filters = {}
        callable_bucket._excludes = {}
        callable_managers = list(callable_bucket)
        self.assertEqual(
            [manager.identification["related"]["id"] for manager in callable_managers],
            ["1", "2", "3"],
        )
        self.assertTrue(provider_calls)
        for manager, expected_related in zip(
            callable_managers,
            related_values,
            strict=True,
        ):
            emitted_wrapper = vars(manager._interface)["_gm_seeded_input_values_cache"][
                "related"
            ]
            self.assertIs(emitted_wrapper, expected_related)
            self.assertIs(manager.related, emitted_wrapper)

    def test_custom_manager_bucket_transforms_keep_compatibility_and_order(
        self, _mock_parse
    ):
        class RelatedManager:
            def __init__(self, identifier):
                self.identifier = identifier

        calls = []

        class CountingBucket(SimpleBucket):
            def filter(self, **kwargs):
                calls.append(("filter", dict(kwargs)))
                return self

            def exclude(self, **kwargs):
                calls.append(("exclude", dict(kwargs)))
                return self

        values = [RelatedManager(1), RelatedManager(2)]
        source = CountingBucket(RelatedManager, values)

        class CalculationManager:
            class Interface(CalculationInterface):
                input_fields: ClassVar[dict] = {
                    "related": Input(RelatedManager, possible_values=source),
                }

        CalculationManager.Interface._parent_class = CalculationManager
        bucket = CalculationBucket(CalculationManager)
        bucket._filters = {}
        bucket._excludes = {}

        combinations = bucket._generate_input_combinations(["related"], {}, {})

        self.assertEqual(combinations, [{"related": value} for value in values])
        self.assertEqual(calls, [("filter", {}), ("exclude", {})])

        calls.clear()
        filters = {"related": {"filter_kwargs": {"id__in": [1]}}}
        excludes = {"related": {"filter_kwargs": {"id__in": [2]}}}
        bucket._generate_input_combinations(["related"], filters, excludes)

        self.assertEqual(
            calls,
            [
                ("filter", {"id__in": [1]}),
                ("exclude", {"id__in": [2]}),
            ],
        )

        calls.clear()
        bucket._generate_input_combinations(["related"], filters, {})
        self.assertEqual(
            calls,
            [("filter", {"id__in": [1]}), ("exclude", {})],
        )

        calls.clear()
        bucket._generate_input_combinations(["related"], {}, excludes)
        self.assertEqual(
            calls,
            [("filter", {}), ("exclude", {"id__in": [2]})],
        )

    def test_standard_manager_bucket_transforms_skip_empty_criteria(self, _mock_parse):
        class RelatedManager:
            class Interface(CalculationInterface):
                input_fields: ClassVar[dict] = {
                    "id": Input(int, possible_values=[1, 2]),
                }

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        RelatedManager.Interface._parent_class = RelatedManager
        source = CalculationBucket(RelatedManager)

        class CalculationManager:
            class Interface(CalculationInterface):
                input_fields: ClassVar[dict] = {
                    "related": Input(RelatedManager, possible_values=source),
                }

        CalculationManager.Interface._parent_class = CalculationManager
        bucket = CalculationBucket(CalculationManager)
        bucket._filters = {}
        bucket._excludes = {}

        with (
            patch.object(CalculationBucket, "filter", side_effect=AssertionError),
            patch.object(CalculationBucket, "exclude", side_effect=AssertionError),
        ):
            combinations = bucket._generate_input_combinations(["related"], {}, {})

        self.assertEqual(
            [combination["related"].identification for combination in combinations],
            [{"id": 1}, {"id": 2}],
        )

    def test_standard_manager_bucket_exclude_transform_removes_matching_values(
        self, _mock_parse
    ):
        from general_manager.utils.filter_parser import (
            parse_filters as real_parse_filters,
        )

        _mock_parse.side_effect = real_parse_filters

        class RelatedManager:
            class Interface(CalculationInterface):
                input_fields: ClassVar[dict] = {
                    "id": Input(int, possible_values=[1, 2]),
                }

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        RelatedManager.Interface._parent_class = RelatedManager
        source = CalculationBucket(RelatedManager)

        class CalculationManager:
            class Interface(CalculationInterface):
                input_fields: ClassVar[dict] = {
                    "related": Input(RelatedManager, possible_values=source),
                }

        CalculationManager.Interface._parent_class = CalculationManager
        bucket = CalculationBucket(CalculationManager)

        combinations = bucket._generate_input_combinations(
            ["related"], {}, {"related": {"filter_kwargs": {"id__in": [2]}}}
        )

        self.assertEqual(
            [combination["related"].identification for combination in combinations],
            [{"id": 1}],
        )

    def test_property_filter_still_instantiates_managers_for_property_access(
        self, _mock_parse
    ):
        calls = []

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "num": Input(type=int, possible_values=[1, 2, 3]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.num = kwargs["num"]

            @property
            def doubled(self):
                return self.num * 2

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager)
        bucket._filters = {"doubled": {"filter_funcs": [lambda value: value >= 4]}}

        combos = bucket.generate_combinations()

        self.assertEqual(combos, [{"num": 2}, {"num": 3}])
        self.assertEqual(
            calls,
            [{"num": 1}, {"num": 2}, {"num": 3}],
        )

    def test_generate_combinations_uses_one_run_context_for_bulk_work(
        self, _mock_parse
    ):
        possible_value_contexts = []
        property_contexts = []

        def possible_values():
            possible_value_contexts.append(current_calculation_run_context())
            return [1, 2, 3]

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "num": Input(type=int, possible_values=possible_values),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)
                self.num = kwargs["num"]

            @property
            def doubled(self):
                property_contexts.append(current_calculation_run_context())
                return self.num * 2

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager)
        bucket._filters = {"doubled": {"filter_funcs": [lambda value: value >= 4]}}

        combos = bucket.generate_combinations()

        self.assertEqual(combos, [{"num": 2}, {"num": 3}])
        self.assertIsNone(current_calculation_run_context())
        self.assertEqual(len(possible_value_contexts), 1)
        self.assertEqual(len(property_contexts), 3)
        all_contexts = [*possible_value_contexts, *property_contexts]
        self.assertTrue(all_contexts)
        self.assertTrue(all(context is not None for context in all_contexts))
        self.assertEqual(
            {id(context) for context in all_contexts}, {id(all_contexts[0])}
        )

    def test_property_exclude_still_instantiates_managers_for_property_access(
        self, _mock_parse
    ):
        calls = []

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "num": Input(type=int, possible_values=[1, 2, 3]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.num = kwargs["num"]

            @property
            def doubled(self):
                return self.num * 2

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager)
        bucket._excludes = {"doubled": {"filter_funcs": [lambda value: value == 4]}}

        combos = bucket.generate_combinations()

        self.assertEqual(combos, [{"num": 1}, {"num": 3}])
        self.assertEqual(
            calls,
            [{"num": 1}, {"num": 2}, {"num": 3}],
        )

    def test_input_sort_key_does_not_instantiate_managers(self, _mock_parse):
        calls = []

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "num": Input(type=int, possible_values=[3, 1, 2]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.num = kwargs["num"]

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager, sort_key="num")

        combos = bucket.generate_combinations()

        self.assertEqual(combos, [{"num": 1}, {"num": 2}, {"num": 3}])
        self.assertEqual(calls, [])

    def test_input_sort_key_allows_missing_optional_input(self, _mock_parse):
        fields = {
            "a": Input(type=int, possible_values=[2, 1]),
            "b": Input(
                type=int,
                possible_values=lambda a: [10] if a == 2 else None,
                depends_on=["a"],
                required=False,
            ),
        }
        bucket = self._make_bucket_with_fields(fields)
        sorted_bucket = CalculationBucket(
            bucket._manager_class,
            bucket.filters,
            bucket.excludes,
            sort_key=("b", "a"),
        )

        combos = sorted_bucket.generate_combinations()

        self.assertEqual(combos, [{"a": 2, "b": 10}, {"a": 1}])

    def test_property_sort_key_still_instantiates_managers_for_property_access(
        self, _mock_parse
    ):
        calls = []

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "num": Input(type=int, possible_values=[1, 2, 3]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.num = kwargs["num"]

            @property
            def descending_value(self):
                return -self.num

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager, sort_key="descending_value")

        combos = bucket.generate_combinations()

        self.assertEqual(combos, [{"num": 3}, {"num": 2}, {"num": 1}])
        self.assertEqual(
            calls,
            [{"num": 1}, {"num": 2}, {"num": 3}],
        )

    def test_property_filter_and_sort_instantiates_managers_once(self, _mock_parse):
        calls = []

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "num": Input(type=int, possible_values=[1, 2, 3]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.num = kwargs["num"]

            @property
            def doubled(self):
                return self.num * 2

            @property
            def descending_value(self):
                return -self.num

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager, sort_key="descending_value")
        bucket._filters = {"doubled": {"filter_funcs": [lambda value: value >= 4]}}

        combos = bucket.generate_combinations()

        self.assertEqual(combos, [{"num": 3}, {"num": 2}])
        self.assertEqual(
            calls,
            [{"num": 1}, {"num": 2}, {"num": 3}],
        )

    def test_mixed_input_and_property_sort_key_uses_manager_sorting(self, _mock_parse):
        calls = []

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "group": Input(type=str, possible_values=["b", "a"]),
                "num": Input(type=int, possible_values=[2, 1]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.group = kwargs["group"]
                self.num = kwargs["num"]

            @property
            def descending_value(self):
                return -self.num

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager, sort_key=("group", "descending_value"))

        combos = bucket.generate_combinations()

        self.assertEqual(
            combos,
            [
                {"group": "a", "num": 2},
                {"group": "a", "num": 1},
                {"group": "b", "num": 2},
                {"group": "b", "num": 1},
            ],
        )
        self.assertEqual(len(calls), 4)

    def test_empty_possible_values(self, _mock_parse):
        # A field with no possible_values yields no combinations

        """
        Tests that a field with an empty list of possible values results in no generated combinations.
        """
        fields = {
            "x": Input(type=int, possible_values=[]),
        }
        bucket = self._make_bucket_with_fields(fields)
        combos = bucket.generate_combinations()
        self.assertEqual(
            combos, [], "Expected no combinations when possible_values is empty"
        )

    def test_dependent_field(self, _mock_parse):
        # Field2 depends on field1 and its possible_values is a callable

        """
        Tests that a dependent input field with callable possible values generates combinations reflecting the dependency.

        Verifies that when one field's possible values depend on another field's value, the generated combinations correctly incorporate this relationship.
        """

        def pv_func(a):
            """
            Multiply a value by 10 and return it in a single-element list.

            Parameters:
                a (int or float): Value to be multiplied by 10.

            Returns:
                list: A single-element list containing the product of `a` and 10.
            """
            return [a * 10]

        fields = {
            "a": Input(type=int, possible_values=[1, 2]),
            "b": Input(type=int, possible_values=pv_func, depends_on=["a"]),
        }
        bucket = self._make_bucket_with_fields(fields)
        combos = bucket.generate_combinations()
        expected = [
            {"a": 1, "b": 10},
            {"a": 2, "b": 20},
        ]
        self.assertCountEqual(combos, expected)

    def test_generate_combinations_caches_callable_possible_values_by_dependencies(
        self,
        _mock_parse,
    ):
        calls: list[str] = []

        def possible_cities(country):
            calls.append(country)
            return [f"{country}-city"]

        fields = {
            "country": Input(type=str, possible_values=["FR", "DE"]),
            "segment": Input(type=str, possible_values=["retail", "enterprise"]),
            "city": Input(
                type=str,
                possible_values=possible_cities,
                depends_on=["country"],
            ),
        }
        bucket = self._make_bucket_with_fields(fields)

        combos = bucket.generate_combinations()

        self.assertCountEqual(
            combos,
            [
                {"country": "FR", "segment": "retail", "city": "FR-city"},
                {"country": "FR", "segment": "enterprise", "city": "FR-city"},
                {"country": "DE", "segment": "retail", "city": "DE-city"},
                {"country": "DE", "segment": "enterprise", "city": "DE-city"},
            ],
        )
        self.assertEqual(calls, ["FR", "DE"])

    def test_optional_field_does_not_expand_none(self, _mock_parse):
        fields = {
            "a": Input(type=int, possible_values=[1, 2]),
            "b": Input(type=int, possible_values=[10], required=False),
        }
        bucket = self._make_bucket_with_fields(fields)
        combos = bucket.generate_combinations()
        self.assertCountEqual(
            combos,
            [
                {"a": 1, "b": 10},
                {"a": 2, "b": 10},
            ],
        )

    def test_optional_field_without_domain_uses_default_behavior(self, _mock_parse):
        fields = {
            "a": Input(type=int, possible_values=[1, 2]),
            "b": Input(type=int, required=False),
        }
        bucket = self._make_bucket_with_fields(fields)
        combos = bucket.generate_combinations()
        self.assertCountEqual(
            combos,
            [
                {"a": 1},
                {"a": 2},
            ],
        )

    def test_optional_field_without_domain_still_respects_filters(self, _mock_parse):
        fields = {
            "a": Input(type=int, possible_values=[1, 2]),
            "b": Input(type=int, required=False),
        }
        bucket = self._make_bucket_with_fields(fields)
        bucket._filters = {"b": {"filter_funcs": [lambda value: value is None]}}
        combos = bucket.generate_combinations()
        self.assertCountEqual(
            combos,
            [
                {"a": 1},
                {"a": 2},
            ],
        )

        bucket = self._make_bucket_with_fields(fields)
        bucket._filters = {"b": {"filter_funcs": [lambda value: value == 1]}}
        self.assertEqual(bucket.generate_combinations(), [])

    def test_domain_backed_possible_values_are_iterable(self, _mock_parse):
        fields = {
            "as_of": Input(
                type=date,
                possible_values=DateRangeDomain(
                    date(2024, 1, 1),
                    date(2024, 3, 31),
                    frequency="month_end",
                ),
            ),
        }
        bucket = self._make_bucket_with_fields(fields)
        combos = bucket.generate_combinations()
        self.assertEqual(
            combos,
            [
                {"as_of": date(2024, 1, 31)},
                {"as_of": date(2024, 2, 29)},
                {"as_of": date(2024, 3, 31)},
            ],
        )

    def test_filters_and_excludes(self, _mock_parse):
        # Apply filter_funcs to include only even numbers, and exclude a specific value

        """
        Tests that filter and exclude functions are correctly applied to input values.

        Verifies that only even numbers are included and a specific value is excluded from the generated combinations.
        """
        fields = {
            "n": Input(type=int, possible_values=[1, 2, 3, 4]),
        }
        bucket = self._make_bucket_with_fields(fields)
        # Manually set filter and exclude definitions
        bucket._filters = {"n": {"filter_funcs": [lambda x: x % 2 == 0]}}
        bucket._excludes = {"n": {"filter_funcs": [lambda x: x == 4]}}
        combos = bucket.generate_combinations()
        # Should include only 2, excluding 4
        self.assertEqual(combos, [{"n": 2}])

    def test_sort_and_reverse_and_caching(self, _mock_parse):
        # Three values, sorted and reversed

        """
        Tests that sorting and reversing combinations works as expected and that results are cached.

        Verifies that combinations are sorted in descending order by the specified key, and that repeated calls to `generate_combinations` return the cached result.
        """
        fields = {
            "v": Input(type=int, possible_values=[3, 1, 2]),
        }
        # Create unsorted bucket
        bucket = self._make_bucket_with_fields(fields)
        # New bucket with sort_key
        sorted_bucket = CalculationBucket(
            bucket._manager_class,
            bucket.filters,
            bucket.excludes,
            sort_key="v",
            reverse=True,
        )
        combos = sorted_bucket.generate_combinations()
        # Should be [3,2,1]
        self.assertEqual([d["v"] for d in combos], [3, 2, 1])
        # Test caching: calling again yields same object
        combos2 = sorted_bucket.generate_combinations()
        self.assertIs(combos, combos2)

    def test_invalid_possible_values_type(self, _mock_parse):
        # possible_values not iterable or callable should raise TypeError

        """
        Tests that a TypeError is raised when a field's possible_values is neither iterable nor callable.
        """
        fields = {
            "z": Input(type=int, possible_values=123),
        }
        bucket = self._make_bucket_with_fields(fields)
        with self.assertRaises(TypeError):
            bucket.generate_combinations()


class TestCalculationBucketAdditional(TestCase):
    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_iter_yields_instances_with_combination_kwargs(self, _mock_parse):
        """
        Ensure iteration yields manager instances populated with the exact combination kwargs.
        """

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "a": Input(type=int, possible_values=[1, 2]),
                "b": Input(type=str, possible_values=["x", "y"]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def __eq__(self, other):
                return isinstance(other, DynManager) and self.kwargs == other.kwargs

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)
        # Preload combinations to avoid relying on internal generation order
        bucket._data = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        items = list(bucket)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].kwargs, {"a": 1, "b": "x"})
        self.assertEqual(items[1].kwargs, {"a": 2, "b": "y"})

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_getitem_negative_index_and_extended_slice(self, _mock_parse):
        """
        Support negative indices and extended slices when accessing the bucket.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        bucket._data = [{"i": 1}, {"i": 2}, {"i": 3}, {"i": 4}]
        # Negative index
        last_mgr = bucket[-1]
        self.assertIsInstance(last_mgr, DummyGeneralManager)
        self.assertEqual(last_mgr.kwargs, {"i": 4})
        # Extended slice
        sliced = bucket[::2]
        self.assertIsInstance(sliced, CalculationBucket)
        self.assertEqual(sliced._data, [{"i": 1}, {"i": 3}])

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_len_and_count_on_empty(self, _mock_parse):
        """
        len() and count() should both be zero on an empty bucket.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        bucket._data = []
        self.assertEqual(len(bucket), 0)
        self.assertEqual(bucket.count(), 0)

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_str_formatting_exact_threshold(self, _mock_parse):
        """
        For exactly five combinations, string representation should not include ellipsis.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        bucket._data = [{"x": i} for i in range(5)]
        s = str(bucket)
        self.assertTrue(s.startswith("CalculationBucket (5)["))
        self.assertNotIn("...", s)

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_str_uses_cached_combinations_with_exact_count(self, _mock_parse):
        bucket = CalculationBucket(DummyGeneralManager)
        bucket._data = [{"x": i} for i in range(7)]

        with patch.object(
            bucket,
            "generate_combinations",
            side_effect=AssertionError("str should use cached combinations directly"),
        ):
            s = str(bucket)

        self.assertTrue(s.startswith("CalculationBucket (7)["))
        self.assertIn("DummyGeneralManager(**{'x': 0})", s)
        self.assertIn("...", s)

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_str_counts_uncached_small_preview_exactly_without_caching(
        self, _mock_parse
    ):
        values = CountingIterable(range(3))

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "n": Input(type=int, possible_values=values),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)

        s = str(bucket)

        self.assertTrue(s.startswith("CalculationBucket (3)["))
        self.assertIn("DynManager(**{'n': 0})", s)
        self.assertIn("DynManager(**{'n': 2})", s)
        self.assertNotIn("...", s)
        self.assertIsNone(bucket._data)
        self.assertEqual(values.yield_count, 3)

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_str_bounds_uncached_large_preview_without_caching(self, _mock_parse):
        values = CountingIterable(range(1000))

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "n": Input(type=int, possible_values=values),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)

        s = str(bucket)

        self.assertTrue(s.startswith("CalculationBucket (5+)["))
        self.assertIn("DynManager(**{'n': 0})", s)
        self.assertIn("DynManager(**{'n': 4})", s)
        self.assertNotIn("DynManager(**{'n': 5})", s)
        self.assertIn("...", s)
        self.assertIsNone(bucket._data)
        self.assertLessEqual(values.yield_count, 6)

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_str_preserves_static_iterator_possible_values(self, _mock_parse):
        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "n": Input(type=int, possible_values=iter(range(10))),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)

        s = str(bucket)
        combinations = bucket.generate_combinations()

        self.assertTrue(s.startswith("CalculationBucket (10)["))
        self.assertEqual(combinations, [{"n": value} for value in range(10)])

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_generate_combinations_snapshots_iterables_before_dependencies(
        self,
        _mock_parse,
    ):
        class StatefulValues:
            def __init__(self):
                self.remaining = [1, 2]

            def __iter__(self):
                while self.remaining:
                    yield self.remaining.pop(0)

        values = StatefulValues()

        def dependent_values(_a):
            values.remaining.clear()
            return [10]

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "a": Input(type=int, possible_values=values),
                "b": Input(
                    type=int,
                    possible_values=dependent_values,
                    depends_on=["a"],
                ),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)

        combinations = bucket.generate_combinations()

        self.assertEqual(
            combinations,
            [{"a": 1, "b": 10}, {"a": 2, "b": 10}],
        )

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_str_snapshots_iterables_before_dependencies(self, _mock_parse):
        class StatefulValues:
            def __init__(self):
                self.remaining = [1, 2]

            def __iter__(self):
                while self.remaining:
                    yield self.remaining.pop(0)

        values = StatefulValues()

        def dependent_values(_a):
            values.remaining.clear()
            return [10]

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "a": Input(type=int, possible_values=values),
                "b": Input(
                    type=int,
                    possible_values=dependent_values,
                    depends_on=["a"],
                ),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)

        s = str(bucket)

        self.assertTrue(s.startswith("CalculationBucket (2)["))
        self.assertIn("DynManager(**{'a': 1, 'b': 10})", s)
        self.assertIn("DynManager(**{'a': 2, 'b': 10})", s)
        self.assertNotIn("...", s)
        self.assertIsNone(bucket._data)

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_str_preserves_sorted_preview_order(self, _mock_parse):
        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "n": Input(type=int, possible_values=[3, 1, 2]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager, sort_key="n")

        s = str(bucket)

        first = s.index("DynManager(**{'n': 1})")
        second = s.index("DynManager(**{'n': 2})")
        third = s.index("DynManager(**{'n': 3})")
        self.assertLess(first, second)
        self.assertLess(second, third)

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_generate_combinations_callable_returning_empty(self, _mock_parse):
        """
        A callable possible_values that returns an empty list should result in zero combinations.
        """

        def pv_empty(_):
            return []

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "a": Input(type=int, possible_values=[1, 2]),
                "b": Input(type=int, possible_values=pv_empty, depends_on=["a"]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.kwargs = kwargs

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)
        self.assertEqual(bucket.generate_combinations(), [])

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_generate_combinations_callable_returning_empty_2(self, _mock_parse):
        """
        A callable possible_values that returns an empty list should result in zero combinations.
        """

        def pv_empty(a):
            return []

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "a": Input(type=int, possible_values=[1, 2]),
                "b": Input(type=int, possible_values=pv_empty),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.kwargs = kwargs

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)
        self.assertEqual(bucket.generate_combinations(), [])

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_generate_combinations_missing_dependency(self, _mock_parse):
        """
        If a field declares depends_on referencing a non-existent field, generation should raise a ValueError.
        """

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "b": Input(
                    type=int, possible_values=lambda x: [x], depends_on=["a"]
                ),  # 'a' missing
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.kwargs = kwargs

        DynInterface._parent_class = DynManager
        bucket = CalculationBucket(DynManager)
        with self.assertRaises((ValueError, KeyError, AttributeError)):
            bucket.generate_combinations()

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_multiple_filter_funcs_all_must_pass(self, _mock_parse):
        """
        When multiple filter functions are provided, they should be combined with logical AND semantics.
        """

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "n": Input(type=int, possible_values=[0, 1, 2, 3, 4, 5, 6])
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = kwargs

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager)
        # Two filters: even numbers AND greater than 2 -> {4,6}
        bucket._filters = {
            "n": {"filter_funcs": [lambda x: x % 2 == 0, lambda x: x > 2]}
        }
        bucket._excludes = {}
        combos = bucket.generate_combinations()
        self.assertCountEqual(combos, [{"n": 4}, {"n": 6}])

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_exclude_funcs_remove_matching_values(self, _mock_parse):
        """
        Exclude functions should remove any matching values from the candidate set.
        """

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "n": Input(type=int, possible_values=[1, 2, 3, 4, 5])
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = kwargs

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager)
        bucket._filters = {}
        bucket._excludes = {"n": {"filter_funcs": [lambda x: x in (2, 5)]}}
        combos = bucket.generate_combinations()
        self.assertCountEqual(combos, [{"n": 1}, {"n": 3}, {"n": 4}])

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_sort_with_missing_key_raises(self, _mock_parse):
        """
        Sorting by a key that does not exist in all combinations should raise an error.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        bucket._data = [{"a": 1}, {"b": 2}]
        sorted_bucket = bucket.sort(key="a", reverse=False)
        with self.assertRaises((KeyError, TypeError, AttributeError)):
            _ = (
                sorted_bucket.generate_combinations()
                if hasattr(sorted_bucket, "generate_combinations")
                and sorted_bucket._data is None
                else sorted_bucket._data.sort(key=lambda d: d["a"])
            )  # Fallback if implementation sorts on generation

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_or_operator_preserves_common_nested_structures(self, _mock_parse):
        """
        __or__ should preserve only filters/excludes with identical nested structures.
        """
        f1 = {
            "field": {"gte": 1, "lte": 5},
        }
        f2 = {
            "field": {"gte": 1, "lte": 5},
        }
        e1 = {"field": {"ne": 3}}
        e2 = {"field": {"ne": 3, "dummy": None}}  # not identical
        b1 = CalculationBucket(
            DummyGeneralManager, filter_definitions=f1, exclude_definitions=e1
        )
        b2 = CalculationBucket(
            DummyGeneralManager, filter_definitions=f2, exclude_definitions=e2
        )
        combined = b1 | b2
        self.assertEqual(combined.filter_definitions, f1)  # identical preserved
        self.assertEqual(combined.exclude_definitions, {})  # non-identical removed


class TestCalculationBucketExceptions(TestCase):
    """Test new custom exception classes in CalculationBucket."""

    def test_invalid_calculation_interface_error(self):
        """Test that InvalidCalculationInterfaceError is raised for non-CalculationInterface managers."""
        from general_manager.bucket.calculation_bucket import (
            InvalidCalculationInterfaceError,
        )
        from general_manager.interface.base_interface import InterfaceBase

        # Create a manager with non-CalculationInterface
        class NonCalcInterface(InterfaceBase):
            pass

        class NonCalcManager:
            Interface = NonCalcInterface

        with self.assertRaises(InvalidCalculationInterfaceError) as ctx:
            CalculationBucket(NonCalcManager)
        self.assertIn("CalculationInterface", str(ctx.exception))

    def test_incompatible_bucket_type_error(self):
        """Test that IncompatibleBucketTypeError is raised when combining different bucket types."""
        from general_manager.bucket.calculation_bucket import (
            IncompatibleBucketTypeError,
        )
        from general_manager.bucket.base_bucket import Bucket

        bucket1 = CalculationBucket(DummyGeneralManager)

        # Create a different bucket type
        class OtherBucket(Bucket):
            def __init__(self, manager_class):
                super().__init__(manager_class)

            def __or__(self, other):
                raise NotImplementedError

            def __iter__(self):
                return iter(())

            def filter(self, **kwargs):
                raise NotImplementedError

            def exclude(self, **kwargs):
                raise NotImplementedError

            def first(self):
                return None

            def last(self):
                return None

            def __contains__(self, item):
                return False

            def count(self):
                return 0

            def all(self):
                return self

            def sort(self, key, reverse=False):
                return self

            def get(self, **kwargs):
                raise NotImplementedError

            def __getitem__(self, item):
                raise NotImplementedError

            def __len__(self):
                return 0

        other_bucket = OtherBucket(DummyGeneralManager)

        with self.assertRaises(IncompatibleBucketTypeError) as ctx:
            bucket1 | other_bucket
        self.assertIn("Cannot combine", str(ctx.exception))

    def test_incompatible_bucket_manager_error(self):
        """Test that IncompatibleBucketManagerError is raised when combining buckets with different managers."""
        from general_manager.bucket.calculation_bucket import (
            IncompatibleBucketManagerError,
        )

        # Create another dummy manager
        class AnotherDummyInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {}

        class AnotherDummyManager:
            Interface = AnotherDummyInterface

        AnotherDummyInterface._parent_class = AnotherDummyManager

        bucket1 = CalculationBucket(DummyGeneralManager)
        bucket2 = CalculationBucket(AnotherDummyManager)

        with self.assertRaises(IncompatibleBucketManagerError) as ctx:
            bucket1 | bucket2
        self.assertIn("Cannot combine buckets for", str(ctx.exception))

    def test_cyclic_dependency_error(self):
        """Test that CyclicDependencyError is raised when cyclic dependencies detected."""
        from general_manager.bucket.calculation_bucket import CyclicDependencyError

        # Create input fields with circular dependencies
        class CircularInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "field_a": Input(str, depends_on=["field_b"]),
                "field_b": Input(str, depends_on=["field_a"]),
            }

        class CircularManager:
            Interface = CircularInterface

        CircularInterface._parent_class = CircularManager

        bucket = CalculationBucket(CircularManager)

        # Try to sort with circular dependencies
        with self.assertRaises(CyclicDependencyError) as ctx:
            bucket.topological_sort_inputs()
        self.assertIn("Cyclic dependency detected", str(ctx.exception))

    def test_invalid_possible_values_error(self):
        """Test that InvalidPossibleValuesError is raised for invalid possible_values configuration."""
        from general_manager.bucket.calculation_bucket import (
            InvalidPossibleValuesError,
        )

        # Create interface with invalid possible_values
        class InvalidPossibleValuesInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "test_field": Input(
                    str,
                    possible_values=123,  # Invalid type
                ),
            }

        class InvalidPossibleValuesManager:
            Interface = InvalidPossibleValuesInterface

        InvalidPossibleValuesInterface._parent_class = InvalidPossibleValuesManager

        bucket = CalculationBucket(InvalidPossibleValuesManager)

        with self.assertRaises(InvalidPossibleValuesError) as ctx:
            bucket.get_possible_values(
                "test_field", bucket.input_fields["test_field"], {}
            )
        self.assertIn("Invalid possible_values configuration", str(ctx.exception))

    def test_missing_calculation_match_error(self):
        """Test that MissingCalculationMatchError is raised when no calculation matches."""
        from general_manager.bucket.calculation_bucket import (
            MissingCalculationMatchError,
        )

        bucket = CalculationBucket(DummyGeneralManager)

        # Try to get a calculation that doesn't exist
        bucket._data = []
        with patch.object(bucket, "filter", return_value=bucket):
            with self.assertRaises(MissingCalculationMatchError) as ctx:
                bucket.get(value="missing")
        self.assertIn("No matching calculation found", str(ctx.exception))

    def test_multiple_calculation_match_error(self):
        """Test that MultipleCalculationMatchError is raised when multiple calculations match."""
        from general_manager.bucket.calculation_bucket import (
            MultipleCalculationMatchError,
        )

        # Create interface with overlapping calculations
        class OverlapInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "field": Input(str, possible_values=["a", "b"]),
            }

        class OverlapManager:
            Interface = OverlapInterface
            identification: ClassVar[dict[str, type]] = {"field": str}

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)
                self.kwargs = kwargs

        OverlapInterface._parent_class = OverlapManager

        bucket = CalculationBucket(OverlapManager)
        bucket._data = [{"field": "a"}, {"field": "a"}]
        with patch.object(bucket, "filter", return_value=bucket):
            with self.assertRaises(MultipleCalculationMatchError) as ctx:
                bucket.get(field="a")
            self.assertIn("Multiple matching calculations found", str(ctx.exception))

    def test_calculation_bucket_edge_cases(self):
        """Test edge cases in CalculationBucket functionality."""
        bucket = CalculationBucket(DummyGeneralManager)

        # Test empty bucket operations
        bucket._data = []
        empty_result = list(bucket)
        self.assertEqual(empty_result, [])

        # Test filter with empty definitions
        filtered = bucket.filter()
        self.assertIsInstance(filtered, CalculationBucket)

        # Test exclude with empty definitions
        excluded = bucket.exclude()
        self.assertIsInstance(excluded, CalculationBucket)

    def test_calculation_bucket_filter_combinations(self):
        """Test various filter and exclude combinations."""
        with patch(
            "general_manager.bucket.calculation_bucket.parse_filters",
            return_value={},
        ):
            bucket = CalculationBucket(
                DummyGeneralManager,
                filter_definitions={"field1": "value1"},
                exclude_definitions={"field2": "value2"},
            )

            # Add more filters
            filtered = bucket.filter(field3="value3")
            self.assertIn("field1", filtered.filter_definitions)
            self.assertIn("field3", filtered.filter_definitions)

            # Add more exclusions
            excluded = bucket.exclude(field4="value4")
            self.assertIn("field2", excluded.exclude_definitions)
            self.assertIn("field4", excluded.exclude_definitions)

    def test_calculation_bucket_or_with_manager_instance(self):
        """Test OR operation with a GeneralManager instance."""

        class InlineInterface(CalculationInterface):
            id = Input(int, possible_values=[123])

        class InlineManager(GeneralManager):
            Interface = InlineInterface

        bucket = CalculationBucket(InlineManager)
        manager_instance = InlineManager(id=123)

        with patch.object(bucket, "filter", return_value=bucket):
            combined = bucket | manager_instance
        self.assertIsInstance(combined, CalculationBucket)


class TestCalculationBucketCoverageEdges(TestCase):
    def test_equality_rejects_other_types(self):
        """CalculationBucket equality should reject non-bucket values."""
        bucket = CalculationBucket(DummyGeneralManager)

        self.assertNotEqual(bucket, object())

    def test_property_transform_resolves_union_collection_and_unknown_hints(self):
        """Property type transformation should normalize common annotation shapes."""
        properties = {
            "optional_number": SimpleNamespace(graphql_type_hint=int | None),
            "names": SimpleNamespace(graphql_type_hint=list[str]),
            "unknown": SimpleNamespace(graphql_type_hint="not-a-type"),
        }

        inputs = CalculationBucket.transform_properties_to_input_fields(
            properties,
            {},
        )

        self.assertEqual(inputs["optional_number"].type, int)
        self.assertEqual(inputs["names"].type, str)
        self.assertEqual(inputs["unknown"].type, object)

    def test_bucket_index_signature_includes_sort_and_filters(self):
        """Bucket index signatures should include plan-defining state."""

        class SignatureInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "x": Input(int, possible_values=[1]),
                "y": Input(int, possible_values=[2]),
            }

        class SignatureManager:
            Interface = SignatureInterface

        SignatureInterface._parent_class = SignatureManager
        bucket = CalculationBucket(
            SignatureManager,
            {"x": 1},
            {"y": 2},
            sort_key=("x",),
            reverse=True,
        )

        signature = bucket._bucket_index_source_signature()

        self.assertEqual(signature[0], "calculation")
        self.assertIs(signature[1], SignatureManager)
        self.assertEqual(signature[-2:], (("x",), True))

    def test_topological_sort_skips_already_visited_dependencies(self):
        """Shared dependency paths should not duplicate already visited inputs."""

        class SharedDependencyInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "root": Input(int, possible_values=[1]),
                "middle": Input(int, possible_values=[2], depends_on=["root"]),
                "leaf": Input(int, possible_values=[3], depends_on=["root", "middle"]),
            }

        class SharedDependencyManager:
            Interface = SharedDependencyInterface

        SharedDependencyInterface._parent_class = SharedDependencyManager
        bucket = CalculationBucket(SharedDependencyManager)

        self.assertEqual(
            bucket.topological_sort_inputs(),
            ["root", "middle", "leaf"],
        )

    def test_required_input_without_possible_values_raises(self):
        """Required inputs without possible values should be invalid."""
        bucket = CalculationBucket(DummyGeneralManager)

        with self.assertRaises(TypeError):
            bucket.get_possible_values("required", Input(int), {})

    def test_iter_input_combinations_covers_bucket_excludes_and_type_skips(self):
        """Input enumeration should handle bucket sources, excludes, and bad types."""
        typed_field = Input(int, possible_values=[1, "bad", 2, 3])
        typed_bucket = CalculationBucket(DummyGeneralManager)
        typed_bucket.input_fields = {"value": typed_field}

        combinations = typed_bucket._generate_input_combinations(
            ["value"],
            {},
            {
                "value": {
                    "filter_funcs": [
                        lambda value: value == 2,
                        lambda value: value == 3,
                    ]
                }
            },
        )

        self.assertEqual(combinations, [{"value": 1}])

        class BucketValueInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "manager": Input(
                    DummyGeneralManager,
                    possible_values=SimpleBucket(DummyGeneralManager, []),
                )
            }

        class BucketValueManager:
            Interface = BucketValueInterface

        BucketValueInterface._parent_class = BucketValueManager
        bucket_value_bucket = CalculationBucket(BucketValueManager)

        self.assertEqual(bucket_value_bucket.generate_combinations(), [])

    def test_property_preview_and_terminal_helpers(self):
        """Lazy property previews and terminal helpers should keep expected behavior."""

        class ScoreInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "score": Input(int, possible_values=[1, 2, 3]),
            }

        class ScoreManager:
            Interface = ScoreInterface

            def __init__(self, **kwargs):
                """Store the score-backed identification for helper assertions."""
                self.identification = dict(kwargs)
                self.score = kwargs["score"]

            def __eq__(self, other):
                """Compare helper managers by their identification payload."""
                return (
                    isinstance(other, ScoreManager)
                    and self.identification == other.identification
                )

        ScoreInterface._parent_class = ScoreManager
        bucket = CalculationBucket(ScoreManager)

        preview = list(
            bucket._iter_prop_filtered_identifications(
                [{"score": 1}, {"score": 2}, {"score": 3}],
                {"score": {"filter_funcs": [lambda value: value >= 2]}},
                {"score": {"filter_funcs": [lambda value: value == 3]}},
            )
        )
        self.assertEqual(preview, [{"score": 2}])

        bucket._data = [{"score": 2}]
        self.assertIn(ScoreManager(score=2), bucket)
        self.assertEqual(bucket.get(score=2).identification, {"score": 2})

        empty = bucket.none()
        self.assertEqual(empty.generate_combinations(), [])
        self.assertEqual(empty.filter_definitions, {})
        self.assertEqual(empty.exclude_definitions, {})


class TestCalculationTerminalStreams(TestCase):
    """Characterize the scalar-only terminal stream admission boundary."""

    @staticmethod
    def _make_scalar_bucket(values: tuple[int, ...] | range) -> CalculationBucket:
        class ScalarCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=values)

        GeneralManagerMeta.ensure_attributes_initialized(ScalarCalculation)
        return CalculationBucket(ScalarCalculation)

    def test_admitted_scalar_bucket_has_stream_plan_and_first_is_bounded(self) -> None:
        bucket = self._make_scalar_bucket(tuple(range(1000)))

        self.assertTrue(bucket._terminal_stream_supported())
        first = bucket.first()

        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.identification, {"value": 0})
        self.assertIsNone(bucket._data)
        self.assertEqual(bucket._combination_evidence, {})

    def test_admitted_scalar_get_and_membership_consume_only_deciding_prefix(
        self,
    ) -> None:
        bucket = self._make_scalar_bucket(range(1000))
        with self.assertRaises(MultipleCalculationMatchError):
            bucket.get()
        self.assertIsNone(bucket._data)
        self.assertEqual(bucket._combination_evidence, {})

        bucket = self._make_scalar_bucket(range(1000))
        expected = bucket._manager_class(value=1)
        self.assertTrue(expected in bucket)
        self.assertIsNone(bucket._data)
        self.assertEqual(bucket._combination_evidence, {})

    def test_admitted_scalar_empty_and_no_match_exhaust_and_cache(self) -> None:
        empty = self._make_scalar_bucket(tuple())
        self.assertIsNone(empty.first())
        self.assertEqual(empty._data, [])

        no_match = self._make_scalar_bucket(tuple())
        with self.assertRaises(MissingCalculationMatchError):
            no_match.get()
        self.assertEqual(no_match._data, [])
        self.assertEqual(no_match._combination_evidence, {})

    def test_exhausted_stream_preserves_topological_combination_shape(self) -> None:
        class PairCalculation(GeneralManager):
            class Interface(CalculationInterface):
                a = Input(int, possible_values=(1,))
                b = Input(str, possible_values=("x",))

        GeneralManagerMeta.ensure_attributes_initialized(PairCalculation)
        expected_bucket = CalculationBucket(PairCalculation)
        expected = expected_bucket.generate_combinations()

        bucket = CalculationBucket(PairCalculation)
        self.assertEqual(bucket.get().identification, {"a": 1, "b": "x"})
        self.assertEqual(bucket._data, expected)
        self.assertEqual(
            [list(combo) for combo in bucket._data],
            [list(combo) for combo in expected],
        )

    def test_scalar_stream_fallbacks_are_not_admitted(self) -> None:
        class ListCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=[0, 1])

        GeneralManagerMeta.ensure_attributes_initialized(ListCalculation)
        bucket = CalculationBucket(ListCalculation)

        self.assertFalse(bucket._terminal_stream_supported())

    def test_scalar_admission_rejects_filters_order_and_custom_sources(self) -> None:
        bucket = self._make_scalar_bucket(range(3))

        for mutate in (
            lambda current: setattr(current, "sort_key", "value"),
            lambda current: setattr(current, "reverse", True),
            lambda current: current._filters.update(
                {"value": {"filter_funcs": [lambda value: value == 0]}}
            ),
            lambda current: current._excludes.update(
                {"value": {"filter_funcs": [lambda value: value == 0]}}
            ),
        ):
            current = self._make_scalar_bucket(range(3))
            mutate(current)
            self.assertFalse(current._terminal_stream_supported())

        class CustomValues:
            def __iter__(self):
                yield from (0, 1, 2)

        class CustomCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=CustomValues())

        GeneralManagerMeta.ensure_attributes_initialized(CustomCalculation)
        self.assertFalse(
            CalculationBucket(CustomCalculation)._terminal_stream_supported()
        )

        bucket._data = [{"value": 0}]
        self.assertFalse(bucket._terminal_stream_supported())

    def test_scalar_admission_rejects_mutated_input_state(self) -> None:
        for mutate in (
            lambda field: setattr(field, "required", False),
            lambda field: setattr(field, "is_manager", True),
            lambda field: setattr(field, "depends_on", ["other"]),
            lambda field: setattr(field, "normalizer", lambda value: value),
        ):
            bucket = self._make_scalar_bucket(range(2))
            mutate(bucket.input_fields["value"])
            self.assertFalse(bucket._terminal_stream_supported())

    def test_scalar_admission_rejects_manager_construction_overrides(self) -> None:
        class NewOverrideCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=(0, 1))

            def __new__(cls, *args, **kwargs):
                return super().__new__(cls)

        class IdentificationOverrideCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=(0, 1))

            @property
            def identification(self):
                return super().identification

        class InterfaceNewOverrideCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=(0, 1))

                def __new__(cls, *args, **kwargs):
                    return super().__new__(cls)

        class InterfaceIdentificationOverrideCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=(0, 1))

                @property
                def identification(self):
                    return {"value": 0}

        class ManagerStateOverrideCalculation(GeneralManager):
            _interface = property(lambda _self: None)

            class Interface(CalculationInterface):
                value = Input(int, possible_values=(0, 1))

        for manager_class in (
            NewOverrideCalculation,
            IdentificationOverrideCalculation,
            InterfaceNewOverrideCalculation,
            InterfaceIdentificationOverrideCalculation,
            ManagerStateOverrideCalculation,
        ):
            GeneralManagerMeta.ensure_attributes_initialized(manager_class)
            self.assertFalse(
                CalculationBucket(manager_class)._terminal_stream_supported()
            )

        class CustomBucket(CalculationBucket):
            pass

        regular_bucket = self._make_scalar_bucket((0, 1))
        self.assertFalse(
            CustomBucket(regular_bucket._manager_class)._terminal_stream_supported()
        )

    def test_last_remains_full_path(self) -> None:
        bucket = self._make_scalar_bucket(range(3))

        last = bucket.last()

        self.assertIsNotNone(last)
        self.assertEqual(last.identification, {"value": 2})
        self.assertEqual(bucket._data, [{"value": 0}, {"value": 1}, {"value": 2}])

    def test_equivalent_buckets_reuse_run_result_with_fresh_data(self) -> None:
        first = self._make_scalar_bucket((0, 1, 2))
        second = CalculationBucket(first._manager_class)
        signature = first._calculation_result_cache_signature()
        self.assertIsNotNone(signature)
        with CalculationRunContext() as context:
            first_result = first.generate_combinations()
            self.assertIsNot(
                context.get_calculation_bucket_result(signature),
                CALCULATION_BUCKET_RESULT_MISSING,
            )
            second_result = second.generate_combinations()

        self.assertEqual(second_result, first_result)
        self.assertIsNot(second_result, first_result)
        second_result[0]["value"] = 99
        self.assertEqual(first_result[0]["value"], 0)

    def test_calculation_result_cache_is_scoped_to_one_run(self) -> None:
        first = self._make_scalar_bucket((0, 1))
        second = CalculationBucket(first._manager_class)
        original = CalculationBucket._generate_input_combinations
        calls = 0

        def wrapped(
            bucket: CalculationBucket,
            *args: object,
            **kwargs: object,
        ) -> list[dict[str, object]]:
            nonlocal calls
            calls += 1
            return original(bucket, *args, **kwargs)

        with patch.object(CalculationBucket, "_generate_input_combinations", wrapped):
            with CalculationRunContext():
                first.generate_combinations()
                second.generate_combinations()
            self.assertEqual(calls, 1)
            third = CalculationBucket(first._manager_class)
            with CalculationRunContext():
                third.generate_combinations()
            self.assertEqual(calls, 2)

    def test_unsafe_mutable_source_bypasses_result_cache(self) -> None:
        values = [0, 1]

        class MutableCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=values)

        GeneralManagerMeta.ensure_attributes_initialized(MutableCalculation)
        first = CalculationBucket(MutableCalculation)
        second = CalculationBucket(MutableCalculation)
        self.assertIsNone(first._calculation_result_cache_signature())
        with CalculationRunContext():
            first.generate_combinations()
            second.generate_combinations()
        self.assertEqual(first.generate_combinations(), second.generate_combinations())

    def test_cyclic_filter_metadata_bypasses_result_cache(self) -> None:
        bucket = self._make_scalar_bucket((0, 1))
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        bucket.filter_definitions["metadata"] = cyclic

        self.assertIsNone(bucket._calculation_result_cache_signature())
        self.assertIsNone(
            CalculationBucket._calculation_result_snapshots([{"value": cyclic}])
        )

    def test_hostile_parsed_filter_key_bypasses_result_cache(self) -> None:
        class HostileKey:
            def __hash__(self) -> int:
                return 1

            def __eq__(self, _other: object) -> bool:
                raise AssertionError

        bucket = self._make_scalar_bucket((0, 1))
        bucket._filters["value"] = {HostileKey(): []}

        self.assertIsNone(bucket._calculation_result_cache_signature())

    def test_cache_hits_clone_nested_result_containers(self) -> None:
        class NestedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(dict, possible_values=({"nested": [1]},))

        GeneralManagerMeta.ensure_attributes_initialized(NestedCalculation)
        first = CalculationBucket(NestedCalculation)
        second = CalculationBucket(NestedCalculation)
        with CalculationRunContext():
            first.generate_combinations()
            second.generate_combinations()

        second._data[0]["value"]["nested"].append(2)
        self.assertEqual(first._data, [{"value": {"nested": [1]}}])

    def test_validator_and_normalizer_inputs_bypass_result_cache(self) -> None:
        class ValidatedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(
                    int,
                    possible_values=(0, 1),
                    validator=lambda value: value >= 0,
                )

        class NormalizedCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(
                    int,
                    possible_values=(0, 1),
                    normalizer=lambda value: value,
                )

        GeneralManagerMeta.ensure_attributes_initialized(ValidatedCalculation)
        GeneralManagerMeta.ensure_attributes_initialized(NormalizedCalculation)
        self.assertIsNone(
            CalculationBucket(
                ValidatedCalculation
            )._calculation_result_cache_signature()
        )
        self.assertIsNone(
            CalculationBucket(
                NormalizedCalculation
            )._calculation_result_cache_signature()
        )

    def test_manager_valued_provider_bypasses_result_cache_admission(self) -> None:
        class RelatedManager(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(int, possible_values=(1,))

        class ManagerValueCalculation(GeneralManager):
            class Interface(CalculationInterface):
                related = Input(
                    RelatedManager,
                    possible_values=lambda: (RelatedManager(id=1),),
                )

        GeneralManagerMeta.ensure_attributes_initialized(RelatedManager)
        GeneralManagerMeta.ensure_attributes_initialized(ManagerValueCalculation)
        self.assertIsNone(
            CalculationBucket(
                ManagerValueCalculation
            )._calculation_result_cache_signature()
        )

    def test_custom_manager_and_uncached_property_bypass_result_admission(self) -> None:
        class CustomCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=(0, 1))

            def __new__(cls, *args: object, **kwargs: object):
                return super().__new__(cls)

            @graph_ql_property(filterable=True, cache="none")
            def selected(self) -> bool:
                return self.value == 1

        GeneralManagerMeta.ensure_attributes_initialized(CustomCalculation)
        self.assertIsNone(
            CalculationBucket(
                CustomCalculation, {"selected": True}
            )._calculation_result_cache_signature()
        )

        class DiscoveryCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=(0, 1))

        GeneralManagerMeta.ensure_attributes_initialized(DiscoveryCalculation)
        discovery_bucket = CalculationBucket(DiscoveryCalculation)

        def hostile_discovery(_cls: type[object]) -> dict[str, object]:
            raise AssertionError

        with patch.object(
            DiscoveryCalculation.Interface,
            "get_graph_ql_properties",
            classmethod(hostile_discovery),
        ):
            self.assertIsNotNone(discovery_bucket._calculation_result_cache_signature())

    def test_callable_provider_reuses_result_without_reinvoking_provider(self) -> None:
        calls = 0

        def provider() -> tuple[int, ...]:
            nonlocal calls
            calls += 1
            return (0, 1)

        class ProviderCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=provider)

        GeneralManagerMeta.ensure_attributes_initialized(ProviderCalculation)
        first = CalculationBucket(ProviderCalculation)
        second = CalculationBucket(ProviderCalculation)
        self.assertIsNotNone(first._calculation_result_cache_signature())
        with CalculationRunContext():
            first.generate_combinations()
            second.generate_combinations()
        self.assertEqual(calls, 1)

    def test_dependent_callable_provider_bypasses_result_cache(self) -> None:
        class DependentCalculation(GeneralManager):
            class Interface(CalculationInterface):
                base = Input(int, possible_values=(0, 1))
                value = Input(
                    int,
                    possible_values=lambda base: (base,),
                    depends_on=["base"],
                )

        GeneralManagerMeta.ensure_attributes_initialized(DependentCalculation)
        self.assertIsNone(
            CalculationBucket(
                DependentCalculation
            )._calculation_result_cache_signature()
        )

    def test_custom_input_metaclass_bypasses_result_cache(self) -> None:
        class HostileMeta(type):
            def __instancecheck__(cls, _instance: object) -> bool:
                raise AssertionError

        class CustomValue(metaclass=HostileMeta):
            pass

        class CustomTypeCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(CustomValue, possible_values=(CustomValue(),))

        GeneralManagerMeta.ensure_attributes_initialized(CustomTypeCalculation)
        self.assertIsNone(
            CalculationBucket(
                CustomTypeCalculation
            )._calculation_result_cache_signature()
        )

    def test_property_filtered_cache_hit_replays_dependencies(self) -> None:
        property_calls = 0

        class PropertyCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=(0, 1))

            @graph_ql_property(filterable=True)
            def selected(self) -> bool:
                nonlocal property_calls
                property_calls += 1
                return self.value == 1

        GeneralManagerMeta.ensure_attributes_initialized(PropertyCalculation)
        first = CalculationBucket(PropertyCalculation, {"selected": True})
        second = CalculationBucket(PropertyCalculation, {"selected": True})
        with CalculationRunContext():
            with DependencyTracker() as tracked:
                first.generate_combinations()
            first_property_calls = property_calls
            with DependencyTracker() as replayed:
                second.generate_combinations()

        self.assertEqual(first._data, [{"value": 1}])
        self.assertEqual(second._data, [{"value": 1}])
        self.assertEqual(property_calls, first_property_calls)
        self.assertEqual(replayed, tracked)

    def test_result_cache_freeze_clone_and_filter_tokens_cover_safe_builtins(
        self,
    ) -> None:
        def callback(value):
            return value

        nested = {
            "tuple": (1, 1.5, b"bytes"),
            "list": [True, None],
            "set": frozenset({1, 2}),
        }
        snapshot = _calculation_cache_freeze(nested)
        self.assertIsNot(snapshot, _CALCULATION_RESULT_UNSUPPORTED)
        cloned = _calculation_cache_clone(snapshot)
        self.assertEqual(cloned, nested)
        self.assertIsNot(cloned, nested)
        self.assertIsNot(cloned["list"], nested["list"])

        cycle = []
        cycle.append(cycle)
        self.assertIs(_calculation_cache_freeze(cycle), _CALCULATION_RESULT_UNSUPPORTED)
        self.assertIs(
            _calculation_cache_freeze([object()]),
            _CALCULATION_RESULT_UNSUPPORTED,
        )
        self.assertIs(
            _calculation_cache_freeze(frozenset({object()})),
            _CALCULATION_RESULT_UNSUPPORTED,
        )
        self.assertIs(
            _calculation_cache_freeze({object(): 1}),
            _CALCULATION_RESULT_UNSUPPORTED,
        )

        identity = _calculation_cache_identity_token(nested)
        self.assertIsInstance(identity, _CalculationCacheIdentityToken)
        self.assertEqual(identity, _calculation_cache_identity_token(nested))
        self.assertIsNot(identity, _calculation_cache_identity_token({}))
        self.assertIs(
            _calculation_cache_callable_token(object()),
            _CALCULATION_RESULT_UNSUPPORTED,
        )
        self.assertEqual(_calculation_cache_callable_token(callback)[0], "callable")

        valid_filter = _calculation_cache_filter_token(
            {"value": {"filter_kwargs": {"minimum": 1}}},
            {
                "value": {
                    "filter_kwargs": {"minimum": 1},
                    "filter_funcs": [callback],
                }
            },
        )
        self.assertIsNot(valid_filter, _CALCULATION_RESULT_UNSUPPORTED)
        unsupported_filters = (
            ({"value": object()}, {}),
            ({"value": {}}, {1: {}}),
            ({"value": {}}, {"value": {1: 1}}),
            ({"value": {}}, {"value": {"filter_funcs": callback}}),
            ({"value": {}}, {"value": {"unknown": 1}}),
            ({"value": {}}, {"value": {"filter_funcs": [object()]}}),
            ({"value": {}}, {"value": {"filter_kwargs": object()}}),
        )
        for raw, parsed in unsupported_filters:
            self.assertIs(
                _calculation_cache_filter_token(raw, parsed),
                _CALCULATION_RESULT_UNSUPPORTED,
            )

        for invalid_snapshot in (
            None,
            (),
            ("unknown", 1),
            ("tuple", None),
        ):
            with self.assertRaises(TypeError):
                _calculation_cache_clone(invalid_snapshot)

    def test_result_cache_helpers_cover_scalar_sources_and_callable_edges(self) -> None:
        identity = _CalculationCacheIdentityToken(object())
        self.assertFalse(identity == object())
        self.assertTrue(_terminal_scalar_source_supported((1, "value", 1.5)))
        self.assertTrue(_terminal_scalar_source_supported(range(2)))
        self.assertFalse(_terminal_scalar_source_supported((object(),)))

        for value in (None, True, 1, 1.5, "value", b"bytes"):
            self.assertIsNot(
                _calculation_cache_freeze(value),
                _CALCULATION_RESULT_UNSUPPORTED,
            )
        self.assertIsNot(
            _calculation_cache_freeze([1, {"nested": frozenset({2})}]),
            _CALCULATION_RESULT_UNSUPPORTED,
        )

        class CallableObject:
            def __call__(self):
                return None

        self.assertIs(
            _calculation_cache_callable_token(CallableObject()),
            _CALCULATION_RESULT_UNSUPPORTED,
        )

        bucket = self._make_scalar_bucket(range(2))
        self.assertIsNotNone(bucket._calculation_result_cache_signature())
        self.assertIsNone(bucket._normalized_sort_key())
        sorted_bucket = bucket.sort(key="value")
        self.assertEqual(sorted_bucket._normalized_sort_key(), ("value",))
        self.assertIsNotNone(sorted_bucket._bucket_index_source_signature())
        self.assertEqual(
            [manager.identification for manager in bucket.all()],
            [
                {"value": 0},
                {"value": 1},
            ],
        )

    def test_terminal_stream_owns_one_run_context_and_cleans_evidence(self) -> None:
        bucket = self._make_scalar_bucket(range(2))
        contexts = []
        original_iter = bucket._iter_terminal_combinations

        def observed_iter():
            for combination in original_iter():
                contexts.append(current_calculation_run_context())
                yield combination

        bucket._iter_terminal_combinations = observed_iter
        with self.assertRaises(MultipleCalculationMatchError):
            bucket.get()

        self.assertEqual(len(contexts), 2)
        self.assertTrue(all(context is contexts[0] for context in contexts))
        self.assertIsNotNone(contexts[0])
        self.assertIsNone(current_calculation_run_context())
        self.assertEqual(bucket._combination_evidence, {})

    def test_early_terminal_then_generate_combinations_rebuilds_complete_cache(
        self,
    ) -> None:
        bucket = self._make_scalar_bucket(range(3))

        first = bucket.first()

        self.assertIsNotNone(first)
        self.assertIsNone(bucket._data)
        self.assertEqual(
            bucket.generate_combinations(),
            [{"value": 0}, {"value": 1}, {"value": 2}],
        )

    def test_filtered_get_and_sorted_terminals_use_materialization_path(self) -> None:
        bucket = self._make_scalar_bucket(range(3))

        self.assertEqual(bucket.get(value=1).identification, {"value": 1})
        self.assertIsNone(bucket._data)

        sorted_bucket = self._make_scalar_bucket(range(3)).sort("value", reverse=True)
        self.assertEqual(sorted_bucket.first().identification, {"value": 2})
        self.assertEqual(
            sorted_bucket._data,
            [{"value": 2}, {"value": 1}, {"value": 0}],
        )

    def test_terminal_stream_rejects_public_filter_maps_and_invalid_inputs(
        self,
    ) -> None:
        for mutate in (
            lambda current: current.filter_definitions.update({"value": {}}),
            lambda current: current.exclude_definitions.update({"value": {}}),
            lambda current: current.input_fields["value"].__dict__.update(
                {"unexpected": object()}
            ),
            lambda current: current.input_fields.__setitem__("value", object()),
        ):
            current = self._make_scalar_bucket(range(3))
            mutate(current)
            self.assertFalse(current._terminal_stream_supported())

        class CustomInitCalculation(GeneralManager):
            class Interface(CalculationInterface):
                value = Input(int, possible_values=(0, 1))

            def __init__(self, **identification):
                super().__init__(**identification)

        GeneralManagerMeta.ensure_attributes_initialized(CustomInitCalculation)
        custom_bucket = CalculationBucket(CustomInitCalculation)
        self.assertFalse(custom_bucket._terminal_stream_supported())
        self.assertEqual(list(custom_bucket._iter_terminal_managers()), [])

    def test_range_evidence_rejects_non_integer_candidates(self) -> None:
        witness = _BuiltinRangeEnumerationWitness(range(3), "not-an-int", object())
        self.assertFalse(witness.authorizes("not-an-int"))
        input_field = Input(int, possible_values=range(3))
        self.assertIsNone(
            _trusted_enumeration_evidence(
                input_field,
                range(3),
                "not-an-int",
                {},
            )
        )
