# type: ignore
from django.test import SimpleTestCase

from general_manager.bucket.baseBucket import Bucket
from general_manager.bucket.groupBucket import GroupBucket


# DummyBucket concrete implementation for testing
class DummyManager:
    class Interface:
        @staticmethod
        def getAttributes():
            """
            Returns a dictionary of attribute names mapped to None values.

            This method provides a fixed set of attribute keys for testing or interface purposes.
            """
            return {"a": None, "b": None, "c": None}


# DummyBucket concrete implementation for testing
class DummyBucket(Bucket[int]):
    def __init__(self, manager_class, data=None):
        """
        Initializes a DummyBucket with the given manager class and optional data.

        Args:
            manager_class: The manager class associated with this bucket.
            data: Optional iterable of items to populate the bucket. If not provided, the bucket is initialized empty.
        """
        super().__init__(manager_class)
        self._data = list(data or [])

    def __or__(self, other):
        """
        Returns a new DummyBucket containing the union of this bucket's data and another DummyBucket or integer.

        If `other` is a DummyBucket, combines their data. If `other` is an integer, appends it to this bucket's data.
        """
        if isinstance(other, DummyBucket):
            return DummyBucket(self._manager_class, self._data + other._data)
        if isinstance(other, int):
            return DummyBucket(self._manager_class, self._data + [other])
        return NotImplemented

    def __iter__(self):
        """
        Returns an iterator over the elements in the bucket.
        """
        return iter(self._data)

    def filter(self, **kwargs):
        """
        Returns a new DummyBucket with updated filters applied.

        The returned bucket contains the same data as the original, but its filters dictionary is updated with the provided keyword arguments.
        """
        new = DummyBucket(self._manager_class, self._data)
        new.filters = {**self.filters, **kwargs}
        return new

    def exclude(self, **kwargs):
        """
        Returns a new DummyBucket with updated exclusion filters applied.

        Args:
            **kwargs: Key-value pairs specifying exclusion criteria.

        Returns:
            A new DummyBucket instance with the combined excludes.
        """
        new = DummyBucket(self._manager_class, self._data)
        new.excludes = {**self.excludes, **kwargs}
        return new

    def first(self):
        """
        Returns the first element in the bucket, or None if the bucket is empty.
        """
        return self._data[0] if self._data else None

    def last(self):
        """
        Returns the last element in the bucket, or None if the bucket is empty.
        """
        return self._data[-1] if self._data else None

    def count(self):
        """
        Returns the number of elements in the bucket.
        """
        return len(self._data)

    def all(self):
        """
        Returns a new DummyBucket instance containing the same data as the current bucket.
        """
        return DummyBucket(self._manager_class, self._data)

    def get(self, **kwargs):
        # support lookup by 'value'
        """
        Retrieves a single item from the bucket matching the given criteria.

        If called with a 'value' keyword argument, returns the unique item equal to that value.
        If called with no arguments, returns the item if the bucket contains exactly one element.
        Raises a ValueError if zero or multiple matches are found.
        """
        if "value" in kwargs:
            matches = [item for item in self._data if item == kwargs["value"]]
            if len(matches) == 1:
                return matches[0]
            raise ValueError(f"get() returned {len(matches)} matches")
        # no kwargs
        if len(self._data) == 1:
            return self._data[0]
        raise ValueError("get() requires exactly one match")

    def __getitem__(self, item):
        """
        Returns the item at the specified index or a new DummyBucket for a slice.

        If a slice is provided, returns a new DummyBucket containing the sliced data.
        """
        if isinstance(item, slice):
            return DummyBucket(self._manager_class, self._data[item])
        return self._data[item]

    def __len__(self):
        """
        Returns the number of elements in the bucket.
        """
        return len(self._data)

    def __contains__(self, item):
        """
        Checks if the specified item is present in the bucket.

        Returns:
            True if the item exists in the bucket; otherwise, False.
        """
        return item in self._data

    def sort(self, key, reverse=False):
        """
        Returns a new DummyBucket with elements sorted.

        Args:
            key: Ignored in this implementation.
            reverse: If True, sorts in descending order.

        Returns:
            A new DummyBucket containing the sorted elements.
        """
        sorted_data = sorted(self._data, reverse=reverse)
        return DummyBucket(self._manager_class, sorted_data)


class BucketTests(SimpleTestCase):
    def setUp(self):
        """
        Initializes test fixtures for bucket tests.

        Creates an empty DummyBucket and a DummyBucket populated with integers 3, 1, and 2, both using DummyManager as the manager class.
        """
        self.manager_class = DummyManager
        self.empty = DummyBucket(self.manager_class, [])
        self.bucket = DummyBucket(self.manager_class, [3, 1, 2])

    def test_eq_and_neq(self):
        """
        Tests equality and inequality comparisons between DummyBucket instances.

        Verifies that buckets with identical data are equal, while those with different data or types are not.
        """
        b1 = DummyBucket(self.manager_class, [1, 2])
        b2 = DummyBucket(self.manager_class, [1, 2])
        b3 = DummyBucket(self.manager_class, [2, 1])
        self.assertEqual(b1, b2)
        self.assertNotEqual(b1, b3)
        self.assertNotEqual(b1, object())

    def test_or_bucket_and_item(self):
        """
        Tests the union operation for DummyBucket instances and integers.

        Verifies that using the '|' operator combines the data from two DummyBucket instances or appends an integer to the bucket's data.
        """
        b1 = DummyBucket(self.manager_class, [1])
        b2 = DummyBucket(self.manager_class, [2])
        combined = b1 | b2
        self.assertEqual(combined._data, [1, 2])
        plus_item = b1 | 5
        self.assertEqual(plus_item._data, [1, 5])

    def test_iter_and_list(self):
        self.assertEqual(list(self.bucket), [3, 1, 2])

    def test_filter_and_exclude(self):
        """
        Tests that the filter and exclude methods correctly update the filters and excludes dictionaries in the bucket.
        """
        f = self.bucket.filter(a=1)
        self.assertEqual(f.filters, {"a": 1})
        e = self.bucket.exclude(b=2)
        self.assertEqual(e.excludes, {"b": 2})

    def test_first_last_empty_and_nonempty(self):
        """
        Tests that the first and last methods return None for empty buckets and correct elements for non-empty buckets.
        """
        self.assertIsNone(self.empty.first())
        self.assertIsNone(self.empty.last())
        self.assertEqual(self.bucket.first(), 3)
        self.assertEqual(self.bucket.last(), 2)

    def test_count_and_len(self):
        self.assertEqual(self.empty.count(), 0)
        self.assertEqual(len(self.empty), 0)
        self.assertEqual(self.bucket.count(), 3)
        self.assertEqual(len(self.bucket), 3)

    def test_all_returns_new_equal_bucket(self):
        """
        Tests that the all() method returns a new DummyBucket instance equal to the original but not the same object.
        """
        copy = self.bucket.all()
        self.assertIsNot(copy, self.bucket)
        self.assertEqual(copy, self.bucket)

    def test_get_no_kwargs(self):
        """
        Tests the get() method with no keyword arguments.

        Verifies that get() returns the single item when the bucket contains exactly one element, and raises ValueError when called on a bucket with zero or multiple elements.
        """
        single = DummyBucket(self.manager_class, [42])
        self.assertEqual(single.get(), 42)
        with self.assertRaises(ValueError):
            self.bucket.get()

    def test_get_by_value(self):
        """
        Tests the get() method for retrieving items by value.

        Verifies that get(value=...) returns the correct item, raises ValueError when no match is found, and raises ValueError when multiple matches exist.
        """
        b = DummyBucket(self.manager_class, [1, 2, 3])
        self.assertEqual(b.get(value=2), 2)
        with self.assertRaises(ValueError):
            b.get(value=99)
        dup = DummyBucket(self.manager_class, [5, 5])
        with self.assertRaises(ValueError):
            dup.get(value=5)

    def test_get_empty_bucket(self):
        """
        Tests that calling get() on an empty bucket raises a ValueError.
        """
        with self.assertRaises(ValueError):
            self.empty.get()

    def test_getitem_index_and_slice(self):
        """
        Tests that indexing and slicing a DummyBucket return the correct elements and types.

        Verifies that indexing returns the expected item and slicing returns a new DummyBucket with the correct subset of data.
        """
        self.assertEqual(self.bucket[1], 1)
        sl = self.bucket[1:]
        self.assertIsInstance(sl, DummyBucket)
        self.assertEqual(sl._data, [1, 2])

    def test_contains(self):
        """
        Tests that membership checks correctly identify elements present or absent in the bucket.
        """
        self.assertIn(1, self.bucket)
        self.assertNotIn(99, self.bucket)

    def test_sort(self):
        """
        Tests that the sort method returns a new bucket with data sorted in ascending or descending order.
        """
        asc = self.bucket.sort(key=None)
        self.assertEqual(asc._data, [1, 2, 3])
        desc = self.bucket.sort(key=None, reverse=True)
        self.assertEqual(desc._data, [3, 2, 1])

    def test_reduce(self):
        """
        Tests that the __reduce__ method returns the correct tuple for pickling DummyBucket instances.
        """
        reduced = self.bucket.__reduce__()
        cls, args = reduced
        self.assertEqual(cls, DummyBucket)
        self.assertEqual(args[0], None)
        self.assertEqual(args[1], self.manager_class)
        self.assertEqual(args[2], {})  # filters
        self.assertEqual(args[3], {})  # excludes

    def test_group_by_valid_keys(self):
        # Create DummyManager instances with attributes
        """
        Tests that grouping a DummyBucket by valid attribute keys returns a GroupBucket
        with the correct manager class and grouping keys.
        """
        m1 = DummyManager()
        m1.a, m1.b = 1, 2
        m2 = DummyManager()
        m2.a, m2.b = 1, 3
        bucket = DummyBucket(self.manager_class, [m1, m2])
        grp = bucket.group_by("a", "b")
        self.assertIsInstance(grp, GroupBucket)
        self.assertEqual(grp._manager_class, self.manager_class)

        self.assertEqual(grp._manager_class, self.manager_class)
        self.assertEqual(grp._group_by_keys, ("a", "b"))

    def test_group_by_invalid_key(self):
        # Valid entries but invalid grouping key 'x'
        """
        Tests that grouping a bucket by an invalid key raises a ValueError.

        Verifies that attempting to group by a key not present in the bucket's items results in a ValueError being raised.
        """
        m = DummyManager()
        m.a, m.b = 1, 2
        bucket = DummyBucket(self.manager_class, [m])
        with self.assertRaises(ValueError):
            bucket.group_by("x")(self)
        with self.assertRaises(ValueError):
            self.bucket.group_by("x")
