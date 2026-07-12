# type: ignore
from datetime import date
import pickle
from types import MappingProxyType
from typing import ClassVar
from unittest.mock import patch
from django.test import TestCase
from general_manager.api.property import GraphQLProperty
from general_manager.manager.group_manager import (
    GroupManager,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.group_bucket import (
    EmptyGroupBucketSliceError,
    GroupBucket,
    GroupBucketKeysMismatchError,
    GroupBucketManagerMismatchError,
    GroupBucketTypeMismatchError,
    InvalidGroupBucketIndexError,
    _MaterializedGroupBucket,
    _NoMaterializationBucket,
    _freeze_group_value,
    _group_filter_kwargs,
    _supports_materialized_group_data,
)
from general_manager.measurement import Measurement
from general_manager.cache.cache_tracker import DependencyTracker
import general_manager.manager.group_manager as group_manager_module


# Stub Interface to simulate attribute definitions
class DummyInterface:
    attr_types: ClassVar[dict[str, dict[str, object]]] = {
        "a": {"type": int},
        "b": {"type": str},
        "c": {"type": list},
        "date": {"type": date},
        "flag": {"type": bool},
        "items": {"type": dict},
    }

    @staticmethod
    def get_attributes():
        return {attr: {} for attr in DummyInterface.attr_types}

    @staticmethod
    def get_attribute_types():
        return DummyInterface.attr_types


# Stub Manager to use with GroupBucket
class DummyManager:
    Interface = DummyInterface

    def __init__(self, **attrs):
        for name, value in attrs.items():
            setattr(self, name, value)

    @GraphQLProperty
    def extra_method(self) -> str:
        return "extra method result"


# Simple list-based Bucket stub
class ListBucket(list):
    def __init__(self, items):
        super().__init__(items)

    def filter(self, **kwargs):
        # Return items matching all kwargs
        return ListBucket(
            [
                item
                for item in self
                if all(getattr(item, k) == v for k, v in kwargs.items())
            ]
        )

    def exclude(self, **kwargs):
        # Return items not matching any kwargs
        return ListBucket(
            [
                item
                for item in self
                if not all(getattr(item, k) == v for k, v in kwargs.items())
            ]
        )

    def sort(self, key, **kwargs):
        # Sort using given key function
        return ListBucket(sorted(self, key=key))

    def __or__(self, other):
        # Combine two buckets
        return ListBucket(list(self) + list(other))


class DependencyListBucket(ListBucket):
    _group_materialization_safe = True

    def __iter__(self):
        DependencyTracker.track("DummyManager", "all", "snapshot")
        yield from super().__iter__()


class IterationCounter:
    value = 0


class CountingMaterializedBucket(ListBucket):
    _group_materialization_safe = True

    def __init__(self, items, counter):
        super().__init__(items)
        self.counter = counter

    def __iter__(self):
        self.counter.value += 1
        yield from super().__iter__()


class CountingLiveBucket(ListBucket):
    def __init__(self, items, counter):
        super().__init__(items)
        self.counter = counter

    def __iter__(self):
        self.counter.value += 1
        yield from super().__iter__()


class FailingMaterializedBucket(CountingMaterializedBucket):
    def __init__(self, items, counter):
        super().__init__(items, counter)
        self.fail = True

    def __iter__(self):
        self.counter.value += 1
        for index, item in enumerate(list.__iter__(self)):
            if self.fail and index == 1:
                raise RuntimeError
            yield item


class GroupBucketTests(TestCase):
    # Test that non-string group_by arguments raise TypeError
    def test_invalid_group_by_type_raises(self):
        with self.assertRaises(TypeError):
            GroupBucket(DummyManager, (123,), ListBucket([]))

    # Test that invalid attribute names raise TypeError
    def test_invalid_group_by_key_raises(self):
        """
        Tests that creating a GroupBucket with a non-existent attribute name raises a ValueError.
        """
        with self.assertRaises(ValueError):
            GroupBucket(DummyManager, ("nonexistent",), ListBucket([]))

    # Test grouping logic produces correct number of groups and keys
    def test_build_grouped_manager(self):
        items = [
            DummyManager(
                a=1, b="x", c=[1], date=date(2020, 1, 1), flag=True, items={"k": 1}
            ),
            DummyManager(
                a=1, b="x", c=[2], date=date(2021, 1, 1), flag=False, items={"k2": 2}
            ),
            DummyManager(
                a=2, b="y", c=[3], date=date(2019, 1, 1), flag=True, items={"k3": 3}
            ),
        ]
        bucket = GroupBucket(DummyManager, ("a", "b"), ListBucket(items))
        # There should be two groups: (1, 'x') and (2, 'y')
        self.assertEqual(bucket.count(), 2)
        keys = {(group.a, group.b) for group in bucket}
        self.assertSetEqual(keys, {(1, "x"), (2, "y")})

    def test_grouping_dict_values_with_mixed_key_types(self):
        items = [
            DummyManager(items={"k": 1, 2: "two"}),
            DummyManager(items={2: "two", "k": 1}),
            DummyManager(items={"k": 2, 2: "two"}),
        ]

        bucket = GroupBucket(DummyManager, ("items",), ListBucket(items))

        self.assertEqual(bucket.count(), 2)

    # Test that __or__ combines two buckets correctly
    def test_or_combines_buckets(self):
        b1 = GroupBucket(DummyManager, ("a",), ListBucket([DummyManager(a=1)]))
        b2 = GroupBucket(DummyManager, ("a",), ListBucket([DummyManager(a=2)]))
        combined = b1 | b2
        self.assertEqual(combined.count(), 2)

        restored = pickle.loads(pickle.dumps(combined))  # noqa: S301 - local test data
        self.assertEqual(restored.count(), 2)

    def test_or_rejects_different_grouping_keys(self):
        b1 = GroupBucket(DummyManager, ("a",), ListBucket([DummyManager(a=1, b="x")]))
        b2 = GroupBucket(DummyManager, ("b",), ListBucket([DummyManager(a=2, b="y")]))

        with self.assertRaises(GroupBucketKeysMismatchError):
            _ = b1 | b2

    # Test that filter and exclude delegate to underlying bucket
    def test_filter_and_exclude_delegate(self):
        items = [DummyManager(a=1), DummyManager(a=2)]
        gb = GroupBucket(DummyManager, ("a",), ListBucket(items))
        filtered = gb.filter(a=1)
        self.assertTrue(all(isinstance(group, GroupManager) for group in filtered))
        self.assertTrue(all(group.a == 1 for group in filtered))

        excluded = gb.exclude(a=1)
        self.assertTrue(all(isinstance(group, GroupManager) for group in excluded))
        self.assertTrue(all(group.a != 1 for group in excluded))

    # Test indexing and slicing behavior
    def test_getitem_and_slice(self):
        items = [DummyManager(a=i) for i in (1, 1, 2, 2)]
        gb = GroupBucket(DummyManager, ("a",), ListBucket(items))
        # Single index returns a GroupManager
        gm0 = gb[0]
        self.assertIsInstance(gm0, GroupManager)
        # Slice returns a GroupBucket with combined base data
        slice_gb = gb[0:1]
        self.assertIsInstance(slice_gb, GroupBucket)
        self.assertEqual(slice_gb.count(), 1)

    # Test get() method for present and missing values
    def test_get_returns_and_raises(self):
        items = [DummyManager(a=1), DummyManager(a=2)]
        gb = GroupBucket(DummyManager, ("a",), ListBucket(items))
        # Getting existing group
        result = gb.get(a=2)
        self.assertEqual(result.a, 2)
        # Getting non-existing raises ValueError
        with self.assertRaises(ValueError):
            gb.get(a=3)

    def test_last(self):
        # Test that last() returns the last item based on the grouping key
        items = [
            DummyManager(a=1),
            DummyManager(a=2),
            DummyManager(a=5),
            DummyManager(a=4),
        ]
        gb = GroupBucket(DummyManager, ("a",), ListBucket(items))
        result = gb.last()
        self.assertEqual(result.a, 5)

    def test_group_manager_data_order(self):
        items = [
            DummyManager(a=1),
            DummyManager(a=2),
            DummyManager(a=3),
            DummyManager(a=4),
        ]
        gb1 = GroupBucket(DummyManager, ("a",), ListBucket(items))
        gb2 = GroupBucket(DummyManager, ("a",), ListBucket(items))

        self.assertEqual(gb1, gb2)
        for i in range(4):
            self.assertEqual(gb1[i].a, gb2[i].a)

    def test_group_manager_data_with_sorting(self):
        # Test sorting within a GroupBucket
        items = [
            DummyManager(a=1, b="d"),
            DummyManager(a=2, b="b"),
            DummyManager(a=3, b="c"),
            DummyManager(a=4, b="a"),
        ]
        gb = GroupBucket(DummyManager, ("a",), ListBucket(items))
        sorted_gm = gb.sort("b")
        self.assertEqual(
            [gm.b for gm in sorted_gm],
            ["a", "b", "c", "d"],
        )
        reverse_sorted_gm = gb.sort("b", reverse=True)
        self.assertEqual(
            [gm.b for gm in reverse_sorted_gm],
            ["d", "c", "b", "a"],
        )

    def test_group_manager_all(self):
        # Test that all() returns a GroupManager with all items
        items = [DummyManager(a=i) for i in range(5)]
        gb = GroupBucket(DummyManager, ("a",), ListBucket(items))
        all_gm = gb.all()
        self.assertIsInstance(all_gm, GroupBucket)
        self.assertTrue(all(isinstance(group, GroupManager) for group in all_gm))
        self.assertEqual(len(all_gm), 5)
        self.assertEqual(all_gm[0].a, 0)
        self.assertEqual(all_gm[4].a, 4)

    def test_group_manager_count(self):
        # Test that count() returns the correct number of groups
        items = [DummyManager(a=i) for i in range(5)]
        gb = GroupBucket(DummyManager, ("a",), ListBucket(items))
        self.assertEqual(gb.count(), 5)

    def test_group_manager_contains(self):
        # Test that __contains__ checks for group existence
        items = [DummyManager(a=i) for i in range(5)]
        gb = GroupBucket(DummyManager, ("a",), ListBucket(items))
        self.assertTrue(items[0] in gb)
        self.assertFalse(DummyManager(a=6) in gb)

    def test_double_grouping(self):
        # Test grouping by multiple attributes
        items = [
            DummyManager(a=1, b="x"),
            DummyManager(a=1, b="y"),
            DummyManager(a=2, b="x"),
        ]
        gb = GroupBucket(DummyManager, ("a", "b"), ListBucket(items))
        self.assertEqual(gb.count(), 3)
        keys = {(group.a, group.b) for group in gb}
        self.assertSetEqual(keys, {(1, "x"), (1, "y"), (2, "x")})

    def test_serial_grouping(self):
        # Test that serializing and deserializing works correctly
        items = [
            DummyManager(a=1, b="x", c=[1]),
            DummyManager(a=1, b="y", c=[2]),
            DummyManager(a=2, b="x", c=[3]),
        ]
        gb = GroupBucket(DummyManager, ("a",), ListBucket(items))

        self.assertEqual(gb.count(), 2)
        gb = gb.group_by("b")
        self.assertEqual(gb.count(), 3)

    def test_materialized_grouping_pickle_round_trip(self):
        items = [
            DummyManager(a=1, b="x"),
            DummyManager(a=1, b="y"),
            DummyManager(a=2, b="x"),
        ]
        bucket = GroupBucket(DummyManager, ("a",), ListBucket(items))

        restored = pickle.loads(pickle.dumps(bucket))  # noqa: S301 - local test data

        self.assertEqual(restored.count(), 2)
        self.assertEqual(
            [group._data.count() for group in restored],
            [2, 1],
        )

    def test_materialized_grouping_replays_dependencies(self):
        bucket = GroupBucket(
            DummyManager,
            ("a",),
            DependencyListBucket([DummyManager(a=1), DummyManager(a=2)]),
        )

        with DependencyTracker() as dependencies:
            bucket.count()

        self.assertIn(("DummyManager", "all", "snapshot"), dependencies)

    def test_private_group_bucket_views_cover_local_and_fallback_operations(self):
        class FallbackBucket(ListBucket):
            def __init__(self, items):
                super().__init__(items)
                self.calls = []

            def filter(self, **kwargs):
                self.calls.append(("filter", kwargs))
                return self

            def exclude(self, **kwargs):
                self.calls.append(("exclude", kwargs))
                return self

            def get(self, **kwargs):
                self.calls.append(("get", kwargs))
                return self[0]

        values = [DummyManager(a=2, b="b"), DummyManager(a=1, b="a")]
        fallback = FallbackBucket(values)
        view = _MaterializedGroupBucket(
            DummyManager,
            values,
            fallback_factory=lambda: fallback,
        )

        self.assertEqual([item.a for item in view.filter(a=1)], [1])
        self.assertEqual([item.a for item in view.exclude(a=1)], [2])
        self.assertIs(view.filter(a__gt=1), fallback)
        self.assertIs(view.exclude(a__gt=1), fallback)
        self.assertIs(view.get(a__gt=1), values[0])
        self.assertEqual(
            fallback.calls,
            [
                ("filter", {"a__gt": 1}),
                ("exclude", {"a__gt": 1}),
                ("get", {"a__gt": 1}),
            ],
        )

        self.assertIs(view.first(), values[0])
        self.assertIs(view.last(), values[1])
        self.assertEqual(view.count(), 2)
        self.assertIs(view.all(), view)
        self.assertEqual(view[0], values[0])
        self.assertEqual(list(view[0:1]), [values[0]])
        self.assertEqual(len(view), 2)
        self.assertIn(values[1], view)
        self.assertEqual([item.a for item in view.sort("a")], [1, 2])
        self.assertEqual([item.a for item in view.sort(("a",), reverse=True)], [2, 1])
        self.assertEqual(view.none().count(), 0)
        self.assertEqual(list(view), values)

        other = _MaterializedGroupBucket(DummyManager, [DummyManager(a=3, b="c")])
        self.assertEqual((view | other).count(), 3)
        self.assertEqual((view | values[0]).count(), 3)
        with self.assertRaises(TypeError):
            view | object()

        nested = {
            "mapping": MappingProxyType({"b": [2, 1], "a": {3, 4}}),
            "tuple": (1, {"x": 2}),
        }
        frozen = _freeze_group_value(nested)
        self.assertIsInstance(frozen, tuple)
        mini = object.__new__(GeneralManager)
        mini._GeneralManager__id = {"id": 9}
        self.assertIsInstance(_freeze_group_value(mini), tuple)
        self.assertEqual(
            _group_filter_kwargs(
                DummyManager,
                (("owner", values[0]), ("a", 1)),
                {"owner": {"filter_lookup": "owner_id"}},
            ),
            {"owner_id": values[0], "a": 1},
        )

        self.assertEqual(
            _group_filter_kwargs(
                GeneralManager,
                (("owner", mini),),
                {"owner": {"filter_lookup": "owner"}},
            ),
            {"owner__id": 9},
        )
        self.assertEqual(_group_filter_kwargs(DummyManager, (("a", 1),)), {"a": 1})
        no_fallback = _MaterializedGroupBucket(DummyManager, values)
        self.assertIsNone(no_fallback._fallback("filter", {}))
        self.assertIs(view.exclude(), view)
        self.assertIs(view.get(a=1), values[1])
        with self.assertRaises(LookupError):
            view.get(a=99)
        restored = pickle.loads(pickle.dumps(no_fallback))  # noqa: S301 - local test data
        self.assertEqual(restored.count(), 2)

    def test_no_materialization_bucket_delegates_and_group_admission_is_conservative(
        self,
    ):
        class DelegatingBucket(ListBucket):
            def get(self, **kwargs):
                matches = self.filter(**kwargs)
                if len(matches) != 1:
                    raise LookupError
                return matches[0]

            def first(self):
                return self[0] if self else None

            def last(self):
                return self[-1] if self else None

            def count(self):
                return len(self)

            def all(self):
                return self

            def sort(self, key, reverse=False):
                keys = (key,) if isinstance(key, str) else key
                return DelegatingBucket(
                    sorted(
                        self,
                        key=lambda value: tuple(getattr(value, k) for k in keys),
                        reverse=reverse,
                    )
                )

        source = DelegatingBucket([DummyManager(a=1, b="x"), DummyManager(a=2, b="y")])
        wrapped = _NoMaterializationBucket(source, DummyManager)
        self.assertEqual(list(wrapped), list(source))
        self.assertEqual(wrapped.filter(a=1), source.filter(a=1))
        self.assertEqual(wrapped.exclude(a=1), source.exclude(a=1))
        self.assertEqual(wrapped.first(), source.first())
        self.assertEqual(wrapped.last(), source.last())
        self.assertEqual(wrapped.count(), 2)
        self.assertIsInstance(wrapped.all(), _NoMaterializationBucket)
        self.assertEqual(wrapped.get(a=1), source.get(a=1))
        self.assertEqual(wrapped[0], source[0])
        self.assertEqual(wrapped[0:1], source[0:1])
        self.assertEqual(len(wrapped), 2)
        self.assertIn(source[0], wrapped)
        self.assertEqual(list(wrapped.sort("a")), list(source.sort("a")))
        self.assertIsInstance(wrapped | source[0:1], _NoMaterializationBucket)

        self.assertFalse(_supports_materialized_group_data(wrapped))
        self.assertFalse(
            _supports_materialized_group_data(
                type("Disabled", (), {"_allow_group_materialization": False})()
            )
        )
        self.assertTrue(_supports_materialized_group_data([1, 2]))
        self.assertTrue(_supports_materialized_group_data((1, 2)))
        self.assertTrue(
            _supports_materialized_group_data(
                type("Safe", (), {"_group_materialization_safe": True})()
            )
        )

        from general_manager.bucket.calculation_bucket import CalculationBucket
        from general_manager.bucket.database_bucket import DatabaseBucket
        from general_manager.bucket.request_bucket import RequestBucket

        request_bucket = object.__new__(RequestBucket)
        request_bucket._materialized = True
        self.assertTrue(_supports_materialized_group_data(request_bucket))
        request_bucket._materialized = False
        self.assertFalse(_supports_materialized_group_data(request_bucket))
        with patch(
            "general_manager.bucket.group_bucket.current_calculation_run_context",
            return_value=object(),
        ):
            self.assertTrue(
                _supports_materialized_group_data(object.__new__(DatabaseBucket))
            )
            self.assertTrue(
                _supports_materialized_group_data(object.__new__(CalculationBucket))
            )
        with patch(
            "general_manager.bucket.group_bucket.current_calculation_run_context",
            return_value=None,
        ):
            self.assertFalse(
                _supports_materialized_group_data(object.__new__(DatabaseBucket))
            )
            self.assertFalse(
                _supports_materialized_group_data(object.__new__(CalculationBucket))
            )

        grouped = GroupBucket(DummyManager, ("a",), wrapped)
        self.assertFalse(grouped._materialized)
        self.assertIsInstance(grouped[0:1], GroupBucket)
        self.assertIsInstance(grouped[:], GroupBucket)
        with self.assertRaises(EmptyGroupBucketSliceError):
            grouped[99:100]
        self.assertIsInstance(grouped.sort("a"), GroupBucket)
        self.assertIsInstance(grouped.group_by("b"), GroupBucket)
        with self.assertRaises(NotImplementedError):
            grouped.none()
        self.assertIsInstance(grouped.__reduce__(), tuple)
        self.assertIsInstance(grouped | grouped, GroupBucket)

        materialized_values = [DummyManager(a=1, b="x"), DummyManager(a=2, b="y")]
        materialized = GroupBucket(
            DummyManager, ("a",), ListBucket(materialized_values)
        )
        self.assertIsNone(GroupBucket(DummyManager, ("a",), ListBucket([])).last())
        selected = materialized[0:1]
        self.assertEqual(selected._basis_data.filter(a=1).count(), 1)
        self.assertEqual(
            selected._basis_data._fallback_factory(), [materialized_values[0]]
        )
        with self.assertRaises(EmptyGroupBucketSliceError):
            materialized[99:100]
        self.assertIsInstance(materialized.sort("a", reverse=True), GroupBucket)
        self.assertIsInstance(materialized.sort(("a",)), GroupBucket)
        self.assertIsInstance(materialized.none(), GroupBucket)
        self.assertFalse(materialized == object())
        self.assertIs(materialized.all(), materialized)

    def test_group_bucket_mismatch_and_index_errors_are_explicit(self):
        bucket = GroupBucket(DummyManager, ("a",), ListBucket([DummyManager(a=1)]))

        class OtherManager(DummyManager):
            pass

        with self.assertRaises(GroupBucketTypeMismatchError):
            bucket | object()
        with self.assertRaises(GroupBucketManagerMismatchError):
            bucket | GroupBucket(OtherManager, ("a",), ListBucket([OtherManager(a=1)]))
        with self.assertRaises(InvalidGroupBucketIndexError):
            bucket["bad"]


class GroupManagerCombineValueTests(TestCase):
    def setUp(self):
        self.original_attr_types = DummyInterface.attr_types.copy()

    def tearDown(self) -> None:
        DummyInterface.attr_types = self.original_attr_types

    # Parametrized tests for combine_value on various data types
    def helper_make_group_manager(self, values, value_type):
        # Create dummy entries with attribute 'field' set to each value
        entries = [DummyManager(field=v) for v in values]
        bucket = ListBucket(entries)
        # Temporarily inject type info for 'field'
        DummyInterface.attr_types["field"] = {"type": value_type}
        return GroupManager(DummyManager, {}, bucket)

    def test_combine_integers_sum(self):
        gm = self.helper_make_group_manager([1, 2, 3], int)
        self.assertEqual(gm.combine_value("field"), 6)

    def test_combine_strings_concat(self):
        gm = self.helper_make_group_manager(["a", "b"], str)
        self.assertEqual(gm.combine_value("field"), "a, b")

    def test_combine_unique_strings_concat(self):
        gm = self.helper_make_group_manager(["a", "b", "b", "a"], str)
        self.assertEqual(gm.combine_value("field"), "a, b")

    def test_combine_lists_extend(self):
        gm = self.helper_make_group_manager([[1], [2, 3]], list)
        self.assertEqual(gm.combine_value("field"), [1, 2, 3])

    def test_combine_only_none(self):
        gm = self.helper_make_group_manager([None, None], type(None))
        self.assertIsNone(gm.combine_value("field"))

    def test_combine_empty_containers_preserves_non_none_values(self):
        self.assertEqual(
            self.helper_make_group_manager([[]], list).combine_value("field"), []
        )
        self.assertEqual(
            self.helper_make_group_manager([{}], dict).combine_value("field"), {}
        )
        self.assertEqual(
            self.helper_make_group_manager([""], str).combine_value("field"), ""
        )

    def test_combine_none_and_value(self):
        gm = self.helper_make_group_manager([None, 1], int)
        self.assertEqual(gm.combine_value("field"), 1)

    def test_combine_dicts_merge(self):
        gm = self.helper_make_group_manager([{"x": 1}, {"y": 2}], dict)
        self.assertEqual(gm.combine_value("field"), {"x": 1, "y": 2})

    def test_combine_bools_any(self):
        gm = self.helper_make_group_manager([True, False], bool)
        self.assertTrue(gm.combine_value("field"))

    def test_combine_dates_max(self):
        dates = [date(2020, 1, 1), date(2021, 1, 1)]
        gm = self.helper_make_group_manager(dates, date)
        self.assertEqual(gm.combine_value("field"), date(2021, 1, 1))

    def test_combine_measurement_sum(self):
        gm = self.helper_make_group_manager(
            [Measurement(1, "m"), Measurement(2, "m")], Measurement
        )
        result = gm.combine_value("field")
        self.assertEqual(result, Measurement(3, "m"))

    def test_materialized_snapshot_reuses_source_for_multiple_aggregates(self):
        DummyInterface.attr_types.update(
            {
                "field": {"type": int},
                "label": {"type": str},
            }
        )
        counter = IterationCounter()
        entries = [
            DummyManager(field=1, label="a"),
            DummyManager(field=2, label="b"),
            DummyManager(field=3, label="a"),
        ]
        manager = GroupManager(
            DummyManager,
            {},
            CountingMaterializedBucket(entries, counter),
        )

        self.assertEqual(manager.combine_value("field"), 6)
        self.assertEqual(manager.combine_value("label"), "a, b")
        self.assertIsNone(manager.combine_value("id"))
        self.assertEqual(counter.value, 1)

    def test_live_snapshot_fallback_observes_mutations_for_aggregate_and_identity(self):
        DummyInterface.attr_types["field"] = {"type": int}
        counter = IterationCounter()
        entries = [DummyManager(field=1)]
        source = CountingLiveBucket(entries, counter)
        manager = GroupManager(DummyManager, {}, source)
        other = GroupManager(
            DummyManager, {}, CountingLiveBucket(list(entries), counter)
        )

        self.assertEqual(manager.combine_value("field"), 1)
        source.append(DummyManager(field=2))
        self.assertEqual(manager.combine_value("field"), 3)
        self.assertNotEqual(hash(manager), hash(other))
        self.assertNotEqual(manager, other)
        self.assertGreaterEqual(counter.value, 4)

    def test_materialized_cache_hit_replays_dependencies_in_nested_trackers(self):
        DummyInterface.attr_types["label"] = {"type": str}
        manager = GroupManager(
            DummyManager,
            {},
            DependencyListBucket([DummyManager(label="a")]),
        )

        with DependencyTracker() as outer:
            with DependencyTracker() as inner:
                self.assertEqual(manager.label, "a")

        with DependencyTracker() as cache_hit:
            self.assertEqual(manager.label, "a")

        dependency = ("DummyManager", "all", "snapshot")
        self.assertIn(dependency, outer)
        self.assertIn(dependency, inner)
        self.assertIn(dependency, cache_hit)

    def test_materialized_identity_cache_hits_replay_dependencies(self):
        DummyInterface.attr_types["label"] = {"type": str}
        manager = GroupManager(
            DummyManager,
            {},
            DependencyListBucket([DummyManager(label="a")]),
        )

        with DependencyTracker() as first_hash:
            hash(manager)
        with DependencyTracker() as second_hash:
            hash(manager)
        with DependencyTracker() as equality_hit:
            _ = manager == manager

        dependency = ("DummyManager", "all", "snapshot")
        self.assertIn(dependency, first_hash)
        self.assertIn(dependency, second_hash)
        self.assertIn(dependency, equality_hit)

    def test_materialized_identity_reuses_frozen_entries(self):
        DummyInterface.attr_types["field"] = {"type": int}
        counter = IterationCounter()
        entry = object.__new__(GeneralManager)
        entry._GeneralManager__id = {"id": 1}
        source = CountingMaterializedBucket([entry], counter)
        manager = GroupManager(DummyManager, {}, source)

        with patch(
            "general_manager.manager.group_manager._freeze_manager_value",
            wraps=group_manager_module._freeze_manager_value,
        ) as freeze:
            hash(manager)
            _ = manager == manager
            first_manager_freezes = sum(
                isinstance(call.args[0], GeneralManager)
                for call in freeze.call_args_list
            )
            hash(manager)
            _ = manager == manager
            second_manager_freezes = sum(
                isinstance(call.args[0], GeneralManager)
                for call in freeze.call_args_list
            )

        self.assertEqual(first_manager_freezes, second_manager_freezes)
        self.assertEqual(counter.value, 1)

    def test_materialized_group_manager_pickle_preserves_cached_aggregate(self):
        DummyInterface.attr_types["field"] = {"type": int}
        manager = GroupManager(
            DummyManager,
            {},
            CountingMaterializedBucket([DummyManager(field=3)], IterationCounter()),
        )
        self.assertEqual(manager.field, 3)

        restored = pickle.loads(pickle.dumps(manager))  # noqa: S301 - local test data

        self.assertEqual(restored.field, 3)

    def test_identity_reflects_mutated_group_mapping(self):
        DummyInterface.attr_types["field"] = {"type": int}
        entry = DummyManager(field=1)
        source = CountingMaterializedBucket([entry], IterationCounter())
        manager = GroupManager(DummyManager, {}, source)
        other = GroupManager(
            DummyManager,
            {},
            CountingMaterializedBucket([entry], IterationCounter()),
        )
        self.assertEqual(manager, other)

        manager._group_by_value["group"] = "new"

        self.assertNotEqual(manager, other)

    def test_failed_materialized_iteration_does_not_publish_partial_snapshot(self):
        DummyInterface.attr_types["field"] = {"type": int}
        counter = IterationCounter()
        source = FailingMaterializedBucket(
            [DummyManager(field=1), DummyManager(field=2)], counter
        )
        manager = GroupManager(DummyManager, {}, source)

        with self.assertRaises(RuntimeError):
            manager.combine_value("field")
        source.fail = False

        self.assertEqual(manager.combine_value("field"), 3)
        self.assertEqual(counter.value, 2)

    def test_manager_bucket_union_preserves_existing_order(self):
        DummyInterface.attr_types["field"] = {"type": Bucket}
        first = ListBucket([DummyManager(field=1)])
        second = ListBucket([DummyManager(field=2)])
        manager = GroupManager(
            DummyManager,
            {},
            ListBucket([DummyManager(field=first), DummyManager(field=second)]),
        )

        combined = manager.combine_value("field")

        self.assertEqual([entry.field for entry in combined], [2, 1])

    def test_iterate_group_manager(self):
        # Test that iterating over GroupManager yields correct items
        DummyInterface.attr_types = {
            "a": {"type": int},
            "b": {"type": str},
        }
        items = [DummyManager(a=i, b=str(i**2)) for i in range(5)]
        gb = GroupBucket(DummyManager, ("a",), ListBucket(items))
        gm = gb.all()
        self.assertEqual(len(list(gm)), 5)
        for i, item in enumerate(gm):
            self.assertEqual(
                dict(item),
                {"a": i, "b": str(i**2), "extra_method": "extra method result"},
            )
