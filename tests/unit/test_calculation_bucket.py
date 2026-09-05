# type: ignore
import pickle
from inspect import signature

from django.test import TestCase, override_settings
from datetime import date, datetime, UTC
from types import SimpleNamespace
from unittest.mock import patch
from general_manager.api.property import GraphQLProperty, graph_ql_property
from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.bucket._ordering import InvalidOrderingError
from general_manager.as_of import (
    HistoricalContextConflictError,
    as_of,
)
from general_manager.cache.run_context import (
    CalculationRunContext,
    current_calculation_run_context,
)
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.interface import CalculationInterface
from general_manager.manager.input import DateRangeDomain, Input
from general_manager.manager import GeneralManager
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.utils.filter_parser import parse_filters
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


class InputIdentificationInterface(CalculationInterface):
    input_fields: ClassVar[dict] = {
        "field1": Input(str, possible_values=["first", "second"]),
        "field2": Input(int, possible_values=[1, 2]),
    }


class InputIdentificationManager:
    Interface = InputIdentificationInterface

    def __init__(self, **kwargs):
        self.identification = dict(kwargs)


InputIdentificationInterface._parent_class = InputIdentificationManager


class InputCalculationInterface(CalculationInterface):
    input_fields: ClassVar[dict[str, Input]] = {
        "region": Input(str, possible_values=["EU"]),
        "year": Input(int, possible_values=[2025, 2026]),
    }


class InputCalculationManager:
    Interface = InputCalculationInterface

    def __init__(self, **kwargs):
        self.identification = dict(kwargs)
        self.region = kwargs["region"]
        self.year = kwargs["year"]

    @GraphQLProperty
    def computed_total(self) -> int:
        return self.year + 1


InputCalculationInterface._parent_class = InputCalculationManager


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
    def test_input_only_projection_does_not_construct_managers(self, _mock_parse):
        _mock_parse.return_value = {}
        bucket = CalculationBucket(InputCalculationManager)

        with patch.object(
            InputCalculationManager,
            "__init__",
            side_effect=AssertionError("input projection constructed a manager"),
        ):
            self.assertEqual(
                bucket.values_list("region", "year"),
                (("EU", 2025), ("EU", 2026)),
            )

    def test_mixed_property_projection_uses_portable_path(self, _mock_parse):
        _mock_parse.return_value = {}
        bucket = CalculationBucket(InputCalculationManager)

        result = bucket.values_list("year", "computed_total")

        self.assertEqual(result, ((2025, 2026), (2026, 2027)))

    def test_extracted_input_resolver_matches_dependent_interface_accessor(
        self, _mock_parse
    ):
        class DependentCalculationInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "base": Input(str, possible_values=["alpha"]),
                "normalized": Input(
                    str,
                    possible_values=lambda base: [base.upper()],
                    depends_on=["base"],
                    normalizer=lambda value, base: f"{base}:{value.upper()}",
                ),
            }

        class DependentCalculationManager:
            Interface = DependentCalculationInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DependentCalculationInterface._parent_class = DependentCalculationManager
        interface = DependentCalculationInterface(base="alpha", normalized="alpha")
        attributes = DependentCalculationInterface.get_attributes()

        from general_manager.interface.capabilities.calculation.input_resolution import (
            resolve_calculation_input_value,
        )

        resolved_values: dict[str, object] = {}
        self.assertEqual(
            resolve_calculation_input_value(
                DependentCalculationInterface,
                interface.identification,
                "normalized",
                resolved_values,
            ),
            attributes["normalized"](interface),
        )

    def test_optional_input_projection_matches_manager_access(self, _mock_parse):
        _mock_parse.return_value = {}

        class OptionalInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "required": Input(int, possible_values=[1]),
                "optional": Input(int, required=False),
            }

        class OptionalManager:
            Interface = OptionalInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)
                self.required = kwargs["required"]
                self.optional = kwargs.get("optional")

        OptionalInterface._parent_class = OptionalManager
        bucket = CalculationBucket(OptionalManager)

        self.assertEqual(
            bucket.values_list("required", "optional"),
            ((1, None),),
        )

    def test_input_filter_and_exclude_projection_matches_manager_access(
        self, _mock_parse
    ):
        _mock_parse.return_value = {}

        class FilterInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "number": Input(int, possible_values=[1, 2, 3, 4]),
            }

        class FilterManager:
            Interface = FilterInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)
                self.number = kwargs["number"]

        FilterInterface._parent_class = FilterManager
        bucket = CalculationBucket(FilterManager)
        bucket._filters = {
            "number": {"filter_funcs": [lambda value: value >= 2]},
        }
        bucket._excludes = {
            "number": {"filter_funcs": [lambda value: value == 3]},
        }

        self.assertEqual(
            bucket.values_list("number"),
            ((2,), (4,)),
        )

    def test_input_sort_and_reverse_projection_preserve_combination_order(
        self, _mock_parse
    ):
        _mock_parse.return_value = {}

        class SortedInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "number": Input(int, possible_values=[2, 1, 3]),
            }

        class SortedManager:
            Interface = SortedInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)
                self.number = kwargs["number"]

        SortedInterface._parent_class = SortedManager
        bucket = CalculationBucket(SortedManager).sort("-number")

        self.assertEqual(bucket.values_list("number", flat=True), (3, 2, 1))

    def test_projection_honors_allowed_identifications_via_portable_fallback(
        self, _mock_parse
    ):
        _mock_parse.return_value = {}
        manager_constructions: list[dict[str, object]] = []

        class AllowedInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "number": Input(int, possible_values=[1, 2, 3]),
            }

        class AllowedManager:
            Interface = AllowedInterface

            def __init__(self, **kwargs):
                manager_constructions.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.number = kwargs["number"] * 10

        AllowedInterface._parent_class = AllowedManager
        bucket = CalculationBucket(AllowedManager)
        bucket._allowed_identifications = [{"number": 2}]

        projected = bucket.values_list("number", flat=True)

        self.assertEqual(projected, (20,))
        self.assertTrue(manager_constructions)

    def test_projection_honors_property_filters_via_portable_fallback(
        self, _mock_parse
    ):
        _mock_parse.return_value = {}
        manager_constructions: list[dict[str, object]] = []

        class PropertyFilterInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "number": Input(int, possible_values=[1, 2, 3]),
            }

        class PropertyFilterManager:
            Interface = PropertyFilterInterface

            def __init__(self, **kwargs):
                manager_constructions.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.number = kwargs["number"] * 10

            @GraphQLProperty
            def doubled(self) -> int:
                return self.number * 2

        PropertyFilterInterface._parent_class = PropertyFilterManager
        bucket = CalculationBucket(PropertyFilterManager)
        bucket._filters = {
            "doubled": {"filter_funcs": [lambda value: value >= 40]},
        }

        projected = bucket.values_list("number", flat=True)

        self.assertEqual(projected, (20, 30))
        self.assertTrue(manager_constructions)

    def test_values_property_exclude_uses_portable_fallback(self, _mock_parse):
        _mock_parse.return_value = {}
        manager_constructions: list[dict[str, object]] = []

        class PropertyExcludeInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "number": Input(int, possible_values=[1, 2, 3]),
            }

        class PropertyExcludeManager:
            Interface = PropertyExcludeInterface

            def __init__(self, **kwargs):
                manager_constructions.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.number = kwargs["number"] * 10

            @GraphQLProperty
            def doubled(self) -> int:
                return self.number * 2

        PropertyExcludeInterface._parent_class = PropertyExcludeManager
        bucket = CalculationBucket(PropertyExcludeManager)
        bucket._excludes = {
            "doubled": {"filter_funcs": [lambda value: value == 40]},
        }

        projected = bucket.values("number")
        projection_constructions = len(manager_constructions)

        self.assertEqual(projected, ({"number": 10}, {"number": 30}))
        self.assertGreaterEqual(projection_constructions, 3)

    def test_values_list_property_sort_uses_portable_fallback(self, _mock_parse):
        _mock_parse.return_value = {}
        manager_constructions: list[dict[str, object]] = []

        class PropertySortInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "number": Input(int, possible_values=[1, 2, 3]),
            }

        class PropertySortManager:
            Interface = PropertySortInterface

            def __init__(self, **kwargs):
                manager_constructions.append(dict(kwargs))
                self.identification = dict(kwargs)
                self.number = kwargs["number"] * 10

            @graph_ql_property(sortable=True, cache="none")
            def descending(self) -> int:
                return -self.number

        PropertySortInterface._parent_class = PropertySortManager
        bucket = CalculationBucket(PropertySortManager).sort("descending")

        projected = bucket.values_list("number", flat=True)
        projection_constructions = len(manager_constructions)

        self.assertEqual(projected, (30, 20, 10))
        self.assertGreaterEqual(projection_constructions, 3)

    def test_fresh_manager_input_projection_tracks_identification_dependency(
        self, _mock_parse
    ):
        _mock_parse.return_value = {}

        with override_settings(AUTOCREATE_GRAPHQL=False):

            class RelatedInterface:
                def __init__(self, manager_id=None, *, id=None):
                    if id is not None:
                        manager_id = id
                    self.identification = {"id": manager_id}

            class RelatedManager(GeneralManager):
                pass

        RelatedManager.Interface = RelatedInterface  # type: ignore[assignment]
        RelatedManager.Permission = ManagerBasedPermission  # type: ignore[assignment]
        RelatedManager._attributes = {}
        related = RelatedManager(id="related-id")

        class ManagerInputInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "related": Input(RelatedManager, possible_values=[related]),
            }

        class ManagerInputCalculation:
            Interface = ManagerInputInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        ManagerInputInterface._parent_class = ManagerInputCalculation
        bucket = CalculationBucket(ManagerInputCalculation)

        with CalculationRunContext():
            with DependencyTracker() as dependencies:
                projected = bucket.values_list("related", flat=True)

        self.assertEqual(projected, (related,))
        self.assertIn(
            (
                RelatedManager.__name__,
                "identification",
                '{"id": "related-id"}',
            ),
            dependencies,
        )

    def test_reused_manager_input_projection_replays_identification_dependency(
        self, _mock_parse
    ):
        _mock_parse.return_value = {}

        with override_settings(AUTOCREATE_GRAPHQL=False):

            class RelatedInterface:
                def __init__(self, manager_id=None, *, id=None):
                    if id is not None:
                        manager_id = id
                    self.identification = {"id": manager_id}

            class RelatedManager(GeneralManager):
                pass

        RelatedManager.Interface = RelatedInterface  # type: ignore[assignment]
        RelatedManager.Permission = ManagerBasedPermission  # type: ignore[assignment]
        RelatedManager._attributes = {}
        related = RelatedManager(id="related-id")

        class ManagerDependencyInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "related": Input(RelatedManager, possible_values=[related]),
                "label": Input(
                    str,
                    possible_values=["related-id"],
                    depends_on=["related"],
                    normalizer=lambda _value, related: related.identification["id"],
                ),
            }

        class ManagerDependencyCalculation:
            Interface = ManagerDependencyInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        ManagerDependencyInterface._parent_class = ManagerDependencyCalculation
        bucket = CalculationBucket(ManagerDependencyCalculation)

        with CalculationRunContext():
            with DependencyTracker() as dependencies:
                projected = bucket.values_list("related", "label")

        self.assertEqual(projected, ((related, "related-id"),))
        self.assertIn(
            (
                RelatedManager.__name__,
                "identification",
                '{"id": "related-id"}',
            ),
            dependencies,
        )

    def test_projection_historical_context_matches_bound_bucket_and_rejects_conflict(
        self, _mock_parse
    ):
        _mock_parse.return_value = {}
        snapshot = datetime(2022, 1, 1, tzinfo=UTC)
        with as_of(snapshot):
            bucket = CalculationBucket(InputCalculationManager)
            self.assertEqual(
                bucket.values_list("year", flat=True),
                (2025, 2026),
            )

        with as_of(datetime(2022, 1, 2, tzinfo=UTC)):
            with self.assertRaises(HistoricalContextConflictError):
                bucket.values_list("year", flat=True)

    def test_binds_active_as_of_date_and_preserves_it_on_derived_buckets(
        self, _mock_parse
    ):
        snapshot = datetime(2022, 1, 1, tzinfo=UTC)

        with as_of(datetime.fromisoformat("2022-01-01T01:00:00+01:00")):
            bucket = CalculationBucket(DummyGeneralManager)
            derived = bucket.filter(dummy=1).exclude(dummy=2).sort().all()

        self.assertEqual(bucket._effective_search_date, snapshot)
        self.assertEqual(derived._effective_search_date, snapshot)

        with as_of(snapshot):
            derived._data = [{"dummy": 1}]
            self.assertEqual(derived[0].identification, {"dummy": 1})

    def test_rejects_live_bucket_at_historical_public_boundaries(self, _mock_parse):
        bucket = CalculationBucket(DummyGeneralManager)
        bucket._data = [{}]

        with as_of("2022-01-01"):
            operations = (
                bucket.all,
                lambda: bucket.filter(dummy=1),
                lambda: bucket.exclude(dummy=1),
                bucket.generate_combinations,
                lambda: list(bucket),
                lambda: bucket[0],
                lambda: bucket.group_by("dummy"),
                lambda: bucket.index_by("dummy"),
                lambda: bucket.get_possible_values(
                    "dummy", Input(int, possible_values=[1]), {}
                ),
                lambda: repr(bucket),
                bucket.__reduce__,
            )
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaises(HistoricalContextConflictError):
                        operation()

    def test_historical_bucket_requires_matching_context_after_pickle_round_trip(
        self, _mock_parse
    ):
        with as_of("2022-01-01") as snapshot:
            bucket = CalculationBucket(DummyGeneralManager)
            bucket._data = [{"dummy": 1}]
            payload = pickle.dumps(bucket)

        restored = pickle.loads(payload)  # noqa: S301

        with self.assertRaises(HistoricalContextConflictError):
            list(bucket)
        with self.assertRaises(HistoricalContextConflictError):
            list(restored)
        with as_of(snapshot):
            self.assertEqual(
                [manager.identification for manager in restored],
                [{"dummy": 1}],
            )

    def test_group_bucket_preserves_calculation_snapshot_at_all_boundaries(
        self, _mock_parse
    ):
        _mock_parse.return_value = {}

        class GroupInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "value": Input(int, possible_values=[1, 2]),
            }

        class GroupManager:
            Interface = GroupInterface

            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                self.identification = dict(kwargs)

        GroupInterface._parent_class = GroupManager
        snapshot = datetime(2022, 1, 1, tzinfo=UTC)
        live_group = CalculationBucket(GroupManager).group_by("value")

        with as_of(snapshot):
            with self.assertRaises(HistoricalContextConflictError):
                list(live_group)

        with as_of(snapshot):
            group = CalculationBucket(GroupManager).group_by("value")
        self.assertEqual(group._effective_search_date, snapshot)

        with self.assertRaises(HistoricalContextConflictError):
            group.filter(value=1)
        with as_of(snapshot):
            derived = group.filter(value=1)
        self.assertEqual(derived._effective_search_date, snapshot)
        with as_of(datetime.fromisoformat("2022-01-01T01:00:00+01:00")):
            self.assertEqual(len(list(group)), 2)
            self.assertEqual(group.count(), 2)
            self.assertIs(group.all(), group)
            self.assertIsNotNone(group.first())
            self.assertIsNotNone(group.last())
            self.assertIsNotNone(group[0])
            self.assertEqual(len(group.sort("value")), 2)
            self.assertEqual(len(group.group_by("value")), 2)
            self.assertIsInstance(group.__reduce__(), tuple)

        with as_of("2022-01-02"):
            operations = (
                lambda: list(group),
                group.all,
                group.first,
                group.last,
                group.count,
                lambda: group[0],
                lambda: len(group),
                lambda: group == group,
                lambda: group | group,
                lambda: GroupManager(value=1) in group,
                lambda: group.filter(value=1),
                lambda: group.exclude(value=1),
                lambda: group.get(value=1),
                lambda: group.sort("value"),
                lambda: group.group_by("value"),
                group.none,
                group.__reduce__,
            )
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaises(HistoricalContextConflictError):
                        operation()

    def test_rejects_differently_dated_bucket(self, _mock_parse):
        with as_of("2022-01-01"):
            bucket = CalculationBucket(DummyGeneralManager)

        with as_of("2022-01-02"):
            with self.assertRaises(HistoricalContextConflictError):
                bucket.generate_combinations()

    def test_initialization_defaults(self, _mock_parse):
        # Test basic initialization without optional parameters

        """
        Tests that CalculationBucket initializes with default values when only the manager class is provided.

        Verifies that filters and excludes are initialized and ordering is empty.
        """
        bucket = CalculationBucket(manager_class=DummyGeneralManager)
        self.assertIsInstance(bucket, CalculationBucket)
        self.assertEqual(bucket._manager_class, DummyGeneralManager)
        self.assertEqual(bucket.filters, {})
        self.assertEqual(bucket.excludes, {})
        self.assertEqual(bucket._sort_fields, ())
        # input_fields should come from the interface
        self.assertEqual(bucket.input_fields, DummyCalculationInterface.input_fields)

    def test_initialization_with_filters_and_excludes(self, _mock_parse):
        """The constructor retains only filter and exclude state."""
        fdefs = {"f": {"filter_kwargs": {"f": 1}}}
        edefs = {"e": {"filter_kwargs": {"e": 2}}}
        bucket = CalculationBucket(DummyGeneralManager, fdefs, edefs)
        self.assertEqual(bucket.filter_definitions, fdefs)
        self.assertEqual(bucket.exclude_definitions, edefs)
        self.assertEqual(bucket._sort_fields, ())

    def test_constructor_rejects_legacy_ordering_keywords(self, _mock_parse):
        """Public ordering is available only through signed ``sort`` fields."""
        parameters = signature(CalculationBucket).parameters

        self.assertNotIn("sort_key", parameters)
        self.assertNotIn("reverse", parameters)
        with self.assertRaises(TypeError):
            CalculationBucket(DummyGeneralManager, sort_key="key")
        with self.assertRaises(TypeError):
            CalculationBucket(DummyGeneralManager, reverse=True)

    def test_signed_ordering_survives_filter_exclude_clone_and_pickle(
        self, _mock_parse
    ):
        """Derived calculation buckets retain private signed ordering state."""
        _mock_parse.side_effect = parse_filters
        source = (
            CalculationBucket(InputIdentificationManager)
            .sort("-field2", "field1")
            .filter(field1__in=["first", "second"])
            .exclude(field2=1)
        )

        clone = source.all()
        restored = pickle.loads(pickle.dumps(source))  # noqa: S301

        expected = [{"field1": "first", "field2": 2}, {"field1": "second", "field2": 2}]
        self.assertEqual(source.generate_combinations(), expected)
        self.assertEqual(clone.generate_combinations(), expected)
        self.assertEqual(restored.generate_combinations(), expected)
        self.assertEqual(source._sort_fields, ("-field2", "field1"))
        self.assertEqual(restored._sort_fields, source._sort_fields)
        self.assertEqual(
            repr(source),
            "CalculationBucket(InputIdentificationManager, {'field1__in': "
            "['first', 'second']}, {'field2': 1}).sort('-field2', 'field1')",
        )

    def test_reduce_and_setstate(self, _mock_parse):
        # Test pickling support

        """
        Tests that CalculationBucket supports pickling and unpickling via __reduce__ and __setstate__.

        Verifies that the reduced state includes current combinations and that state restoration
        correctly sets the internal combinations on a new instance.
        """
        bucket = CalculationBucket(DummyGeneralManager, {"a": 1}, {"b": 2})
        # Prepopulate state
        bucket._data = [{"x": 10}]
        cls, args, state = bucket.__reduce__()
        # Check reduce data
        self.assertEqual(cls, CalculationBucket)
        self.assertEqual(
            args,
            (DummyGeneralManager, {"a": 1}, {"b": 2}, ({"a": 1},), ({"b": 2},)),
        )
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
        self.assertEqual([manager.identification for manager in combined], [{}])

    def test_or_intersects_distinct_allowed_instance_subsets(self, _mock_parse):
        """Combining exact subsets returns a left-first deduplicated union."""

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "num": Input(type=int, possible_values=[1, 2, 3]),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        source = CalculationBucket(DynManager)
        left = source.with_instances([DynManager(num=1), DynManager(num=2)])
        right = source.with_instances([DynManager(num=2), DynManager(num=3)])

        combined = left | right

        self.assertEqual(
            [manager.identification for manager in combined],
            [{"num": 1}, {"num": 2}, {"num": 3}],
        )

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
            f"CalculationBucket({DummyGeneralManager.__name__}, {{}}, {{}})",
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
        self.assertEqual(
            [manager.identification for manager in sliced], [{"i": 1}, {"i": 2}]
        )

    def test_with_instances_materializes_non_id_identifications_in_order(
        self, _mock_parse
    ) -> None:
        """Keep exact input identifications without reconstructing through filters."""
        _mock_parse.side_effect = parse_filters
        snapshot = datetime(2022, 1, 1, tzinfo=UTC)
        with as_of(snapshot):
            source = CalculationBucket(
                InputIdentificationManager,
                {"field1__in": ["first", "second"]},
                {"field2": 0},
            ).sort("field1", "-field2")
            source._data = [{"field1": "cached", "field2": 99}]
            selected = [
                InputIdentificationManager(field1="second", field2=2),
                InputIdentificationManager(field1="first", field2=1),
            ]

            subset = source.with_instances(selected)
            empty = source.with_instances(())
            restored = pickle.loads(pickle.dumps(subset))  # noqa: S301

            expected_identifications = [
                {"field1": "second", "field2": 2},
                {"field1": "first", "field2": 1},
            ]

            self.assertEqual(
                [item.identification for item in subset],
                expected_identifications,
            )
            self.assertEqual(
                [manager.identification for manager in restored],
                expected_identifications,
            )
            self.assertIsNot(subset, source)
            self.assertEqual(list(empty), [])
            self.assertEqual(
                source.filter_definitions,
                {"field1__in": ["first", "second"]},
            )
            self.assertEqual(source.exclude_definitions, {"field2": 0})
            self.assertEqual(source._sort_fields, ("field1", "-field2"))
            self.assertEqual(source._data, [{"field1": "cached", "field2": 99}])
            self.assertEqual(source._effective_search_date, snapshot)

    def test_exact_subset_keeps_supplied_instances_and_empty_slice_membership(
        self, _mock_parse
    ) -> None:
        """Exact subset operations cannot reconstruct rows outside the subset."""
        source = CalculationBucket(InputCalculationManager)
        first, second = tuple(source)

        subset = source.with_instances([second, second, first])

        self.assertEqual(list(subset), [second, second, first])
        self.assertIs(subset[0], second)
        self.assertEqual(list(source[:0].sort("year")), [])

    def test_native_filter_and_exclude_preserve_call_groups(self, _mock_parse) -> None:
        """Calculation query calls use Django-style AND and NOT(AND) groups."""
        _mock_parse.side_effect = parse_filters

        class RowInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "row": Input(int, possible_values=[1, 2, 3]),
            }

        class RowManager:
            Interface = RowInterface

            def __init__(self, **kwargs: int) -> None:
                self.identification = dict(kwargs)
                self.row = kwargs["row"]
                self._a, self._b = ((1, 1), (1, 2), (2, 2))[self.row - 1]

            @GraphQLProperty
            def a(self) -> int:
                return self._a

            @GraphQLProperty
            def b(self) -> int:
                return self._b

        RowInterface._parent_class = RowManager
        bucket = CalculationBucket(RowManager)

        self.assertEqual(list(bucket.filter(a=1).filter(a=2)), [])
        self.assertEqual([manager.row for manager in bucket.exclude(a=1, b=1)], [2, 3])
        self.assertEqual(
            [manager.row for manager in bucket.exclude(a=1).exclude(b=1)], [3]
        )
        self.assertEqual(
            [manager.row for manager in bucket.filter(a=1).filter()], [1, 2]
        )
        self.assertEqual([manager.row for manager in bucket.exclude()], [1, 2, 3])
        self.assertEqual(
            [manager.row for manager in bucket.exclude(a=1, b=1).exclude()],
            [2, 3],
        )

    def test_native_groups_preserve_manager_id_aliases_and_nested_lookups(
        self, _mock_parse
    ) -> None:
        """Grouped fallback evaluates normalized manager-input lookup paths."""
        _mock_parse.side_effect = parse_filters

        with override_settings(AUTOCREATE_GRAPHQL=False):

            class RelatedInterface:
                def __init__(self, *, id: int) -> None:
                    self.identification = {"id": id}

            class RelatedManager(GeneralManager):
                pass

        RelatedManager.Interface = RelatedInterface  # type: ignore[assignment]
        RelatedManager.Permission = ManagerBasedPermission  # type: ignore[assignment]
        RelatedManager._attributes = {}
        related = [RelatedManager(id=1), RelatedManager(id=2)]

        class ParentInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "related": Input(RelatedManager, possible_values=related),
            }

        class ParentManager:
            Interface = ParentInterface

            def __init__(self, **kwargs: object) -> None:
                self.related = kwargs["related"]
                self.identification = dict(kwargs)

        ParentInterface._parent_class = ParentManager
        bucket = CalculationBucket(ParentManager)

        self.assertEqual(list(bucket.filter(related_id=1).filter(related__id=2)), [])

    def test_sort_returns_new_bucket(self, _mock_parse):
        """
        Tests that the sort() method returns a new CalculationBucket with updated sort key and reverse flag, leaving the original bucket unchanged.
        """
        bucket = CalculationBucket(DummyGeneralManager, {"a": 1}, {"b": 2})
        with self.assertRaises(InvalidOrderingError):
            bucket.sort("-a")

    def test_sort_rejects_non_sortable_property_before_evaluation(self, _mock_parse):
        bucket = CalculationBucket(InputCalculationManager).none()

        with self.assertRaises(InvalidOrderingError):
            bucket.sort("computed_total")


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

    def test_sort_orders_heterogeneous_declared_values_by_category(self, _mock_parse):
        bucket = self._make_bucket_with_fields(
            {"value": Input(type=object, possible_values=["later", 2, False])}
        )

        managers = list(bucket.sort("value"))

        self.assertEqual([manager.value for manager in managers], [False, 2, "later"])

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

        bucket = CalculationBucket(DynManager).sort("num")

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
            bucket._manager_class, bucket.filters, bucket.excludes
        ).sort("b", "a")

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

            @graph_ql_property(sortable=True, cache="none")
            def descending_value(self) -> int:
                return -self.num

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager).sort("descending_value")

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

            @graph_ql_property(sortable=True, cache="none")
            def descending_value(self) -> int:
                return -self.num

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager).sort("descending_value")
        bucket._filters = {"doubled": {"filter_funcs": [lambda value: value >= 4]}}

        combos = bucket.generate_combinations()

        self.assertEqual(combos, [{"num": 3}, {"num": 2}])
        self.assertEqual(
            calls,
            [{"num": 1}, {"num": 2}, {"num": 3}],
        )

    def test_allowed_subset_reuses_managers_for_property_access(self, _mock_parse):
        calls = []

        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "num": Input(type=int, possible_values=[1, 2, 3]),
            }

        class DynManager(GeneralManager):
            Interface = DynInterface

            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                super().__init__(**kwargs)

            @graph_ql_property(sortable=True)
            def doubled(self) -> int:
                return int(self.identification["num"]) * 2

        DynInterface._parent_class = DynManager
        DynManager.Interface.input_fields = DynInterface.input_fields
        source = CalculationBucket(DynManager)
        subset = source.with_instances([DynManager(num=2), DynManager(num=3)])
        subset = subset.sort("doubled")
        calls.clear()

        self.assertEqual(
            [manager.identification for manager in subset], [{"num": 2}, {"num": 3}]
        )
        self.assertEqual(calls, [])

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

            @graph_ql_property(sortable=True, cache="none")
            def descending_value(self) -> int:
                return -self.num

        DynInterface._parent_class = DynManager

        bucket = CalculationBucket(DynManager).sort("group", "descending_value")

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

    def test_sort_accepts_flattened_compound_relation_paths(self, _mock_parse):
        class Employee:
            def __init__(self, employee_id: int, name: str) -> None:
                self.id = employee_id
                self.name = name

        employees = [Employee(3, "Bob"), Employee(1, "Alice"), Employee(2, "Alice")]
        fields = {
            "rank": Input(type=int, possible_values=[1]),
            "employee": Input(type=Employee, possible_values=employees),
        }
        bucket = self._make_bucket_with_fields(fields)

        with self.assertRaises(InvalidOrderingError):
            bucket.sort("rank", "employee__name", "employee__id")

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
        sorted_bucket = bucket.sort("-v")
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
        self.assertEqual(
            [manager.identification for manager in sliced], [{"i": 1}, {"i": 3}]
        )

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
    def test_str_uncached_preview_honors_allowed_identifications(self, _mock_parse):
        class DynInterface(CalculationInterface):
            input_fields: ClassVar[dict] = {
                "n": Input(type=int, possible_values=range(10)),
            }

        class DynManager:
            Interface = DynInterface

            def __init__(self, **kwargs):
                self.identification = dict(kwargs)

        DynInterface._parent_class = DynManager
        source = CalculationBucket(DynManager)
        restricted = source.with_instances([DynManager(n=7), DynManager(n=9)])
        uncached = restricted.filter()

        self.assertEqual(
            [manager.identification for manager in uncached], [{"n": 7}, {"n": 9}]
        )

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
        bucket = CalculationBucket(DynManager).sort("n")

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
        with self.assertRaises(InvalidOrderingError):
            bucket.sort("a")

    @patch("general_manager.bucket.calculation_bucket.parse_filters", return_value={})
    def test_or_operator_preserves_common_nested_structures(self, _mock_parse):
        """
        __or__ should materialize the represented collection union.
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
        self.assertEqual([manager.identification for manager in combined], [{}])


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

            def sort(self, *fields):
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

        combined = bucket | manager_instance
        self.assertEqual(
            [manager.identification for manager in combined], [{"id": 123}]
        )


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
        bucket = CalculationBucket(SignatureManager, {"x": 1}, {"y": 2}).sort("-x")

        signature = bucket._bucket_index_source_signature()

        self.assertEqual(signature[0], "calculation")
        self.assertIs(signature[1], SignatureManager)
        self.assertEqual(signature[-1], ("-x",))

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
