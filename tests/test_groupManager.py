# type: ignore
from datetime import date
from django.test import TestCase
from general_manager.api.graphql import GraphQLProperty
from general_manager.manager.groupManager import (
    GroupManager,
)
from general_manager.bucket.groupBucket import GroupBucket
from general_manager.measurement import Measurement


# Stub Interface to simulate attribute definitions
class DummyInterface:
    attr_types = {
        "a": {"type": int},
        "b": {"type": str},
        "c": {"type": list},
        "date": {"type": date},
        "flag": {"type": bool},
        "items": {"type": dict},
    }

    @staticmethod
    def getAttributes():
        return {attr: {} for attr in DummyInterface.attr_types}

    @staticmethod
    def getAttributeTypes():
        return DummyInterface.attr_types


# Stub Manager to use with GroupBucket
class DummyManager:
    Interface = DummyInterface

    def __init__(self, **attrs):
        for name, value in attrs.items():
            setattr(self, name, value)

    @GraphQLProperty
    def extraMethod(self) -> str:
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

    # Test that __or__ combines two buckets correctly
    def test_or_combines_buckets(self):
        b1 = GroupBucket(DummyManager, ("a",), ListBucket([DummyManager(a=1)]))
        b2 = GroupBucket(DummyManager, ("a",), ListBucket([DummyManager(a=2)]))
        combined = b1 | b2
        self.assertEqual(combined.count(), 2)

    # Test that filter and exclude delegate to underlying bucket
    def test_filter_and_exclude_delegate(self):
        items = [DummyManager(a=1), DummyManager(a=2)]
        gb = GroupBucket(DummyManager, ("a",), ListBucket(items))
        filtered = gb.filter(a=1)
        self.assertTrue(all(group.a == 1 for group in filtered))

        excluded = gb.exclude(a=1)
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


class GroupManagerCombineValueTests(TestCase):
    def setUp(self):
        self.original_attr_types = DummyInterface.attr_types.copy()

    def tearDown(self) -> None:
        DummyInterface.attr_types = self.original_attr_types

    # Parametrized tests for combineValue on various data types
    def helper_make_group_manager(self, values, value_type):
        # Create dummy entries with attribute 'field' set to each value
        entries = [DummyManager(field=v) for v in values]
        bucket = ListBucket(entries)
        # Temporarily inject type info for 'field'
        DummyInterface.attr_types["field"] = {"type": value_type}
        return GroupManager(DummyManager, {}, bucket)

    def test_combine_integers_sum(self):
        gm = self.helper_make_group_manager([1, 2, 3], int)
        self.assertEqual(gm.combineValue("field"), 6)

    def test_combine_strings_concat(self):
        gm = self.helper_make_group_manager(["a", "b"], str)
        self.assertEqual(gm.combineValue("field"), "a, b")

    def test_combine_unique_strings_concat(self):
        gm = self.helper_make_group_manager(["a", "b", "b", "a"], str)
        self.assertEqual(gm.combineValue("field"), "a, b")

    def test_combine_lists_extend(self):
        gm = self.helper_make_group_manager([[1], [2, 3]], list)
        self.assertEqual(gm.combineValue("field"), [1, 2, 3])

    def test_combine_only_none(self):
        gm = self.helper_make_group_manager([None, None], type(None))
        self.assertIsNone(gm.combineValue("field"))

    def test_combine_none_and_value(self):
        gm = self.helper_make_group_manager([None, 1], int)
        self.assertEqual(gm.combineValue("field"), 1)

    def test_combine_dicts_merge(self):
        gm = self.helper_make_group_manager([{"x": 1}, {"y": 2}], dict)
        self.assertEqual(gm.combineValue("field"), {"x": 1, "y": 2})

    def test_combine_bools_any(self):
        gm = self.helper_make_group_manager([True, False], bool)
        self.assertTrue(gm.combineValue("field"))

    def test_combine_dates_max(self):
        dates = [date(2020, 1, 1), date(2021, 1, 1)]
        gm = self.helper_make_group_manager(dates, date)
        self.assertEqual(gm.combineValue("field"), date(2021, 1, 1))

    def test_combine_measurement_sum(self):

        gm = self.helper_make_group_manager(
            [Measurement(1, "m"), Measurement(2, "m")], Measurement
        )
        result = gm.combineValue("field")
        self.assertEqual(result, Measurement(3, "m"))

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
                {"a": i, "b": str(i**2), "extraMethod": "extra method result"},
            )
