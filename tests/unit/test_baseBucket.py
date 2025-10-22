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
            Provide a fixed mapping of attribute names to None.

            Returns:
                attributes (dict): A dictionary with keys 'a', 'b', and 'c', each mapped to None.
            """
            return {"a": None, "b": None, "c": None}


# DummyBucket concrete implementation for testing
class MultipleMatchesError(ValueError):
    """Raised when DummyBucket.get encounters more than one matching item."""

    def __init__(self, count: int) -> None:
        """
        Initialize the error with a message indicating how many matching items were found.

        Parameters:
            count (int): Number of matches that were found; used in the exception message "get() returned {count} matches."
        """
        super().__init__(f"get() returned {count} matches.")


class SingleMatchRequiredError(ValueError):
    """Raised when DummyBucket.get requires exactly one value but none are available."""

    def __init__(self) -> None:
        """
        Create the exception signaling that `get()` requires exactly one match.

        The exception instance carries the message "get() requires exactly one match."
        """
        super().__init__("get() requires exactly one match.")


class DummyBucket(Bucket[int]):
    def __init__(self, manager_class, data=None):
        """
        Create a DummyBucket bound to a manager class, optionally seeded with initial items.

        Parameters:
            manager_class: The manager class associated with this bucket.
            data (iterable[int] | None): Optional iterable of items to populate the bucket; items are copied into the bucket's internal list.
        """
        super().__init__(manager_class)
        self._data = list(data or [])

    def __or__(self, other):
        """
        Create a new DummyBucket by combining this bucket's contents with another bucket or by appending an integer.

        Parameters:
            other (DummyBucket | int): A DummyBucket whose elements will be concatenated to this bucket, or an integer to append.

        Returns:
            DummyBucket: A new bucket containing the combined elements.
            NotImplemented: If `other` is not a DummyBucket or an int.
        """
        if isinstance(other, DummyBucket):
            return DummyBucket(self._manager_class, [*self._data, *other._data])
        if isinstance(other, int):
            return DummyBucket(self._manager_class, [*self._data, other])
        return NotImplemented

    def __iter__(self):
        """
        Provide an iterator over the bucket's elements.

        Returns:
            iterator: An iterator that yields each element in the bucket in order.
        """
        return iter(self._data)

    def filter(self, **kwargs):
        """
        Create a new DummyBucket with merged filter criteria.

        Merges the provided keyword arguments into the bucket's existing filters and returns a new DummyBucket instance with the same data and manager class.

        Parameters:
            **kwargs: Filter expressions to add or override in the returned bucket's filters.

        Returns:
            DummyBucket: A new bucket with the same data and manager class and filters updated to include the provided kwargs.
        """
        new = DummyBucket(self._manager_class, self._data)
        new.filters = {**self.filters, **kwargs}
        return new

    def exclude(self, **kwargs):
        """
        Create a new DummyBucket with updated exclusion criteria.

        The returned bucket contains the same manager class and data as the original, but its excludes mapping is merged with the provided keyword arguments (provided keys override existing excludes).

        Parameters:
            **kwargs: Exclusion criteria keyed by attribute name; values are the exclusion values.

        Returns:
            DummyBucket: A new bucket whose `excludes` is the original `excludes` updated with `kwargs`.
        """
        new = DummyBucket(self._manager_class, self._data)
        new.excludes = {**self.excludes, **kwargs}
        return new

    def first(self):
        """
        Returns the first element in the bucket, or None if the bucket contains no elements.
        """
        return self._data[0] if self._data else None

    def last(self):
        """
        Returns the last element in the bucket, or None if the bucket is empty.
        """
        return self._data[-1] if self._data else None

    def count(self):
        """
        Returns the number of elements contained in the bucket.
        """
        return len(self._data)

    def all(self):
        """
        Returns a new DummyBucket containing the same elements as this bucket.
        """
        return DummyBucket(self._manager_class, self._data)

    def get(self, **kwargs):
        # support lookup by 'value'
        """
        Return the single item from the bucket that matches the provided criteria.

        If called with the keyword argument `value`, return the item equal to that value; if the number of matching items is not exactly one, raise a MultipleMatchesError containing the match count. If called with no keyword arguments, return the single element in the bucket when the bucket contains exactly one item; otherwise raise SingleMatchRequiredError.

        Parameters:
            value: (optional) The value to match against items in the bucket.

        Returns:
            The matching item.

        Raises:
            MultipleMatchesError: When `value` is provided and the count of matching items is not exactly one.
            SingleMatchRequiredError: When no criteria are provided and the bucket does not contain exactly one item.
        """
        if "value" in kwargs:
            matches = [item for item in self._data if item == kwargs["value"]]
            if len(matches) == 1:
                return matches[0]
            raise MultipleMatchesError(len(matches))
        # no kwargs
        if len(self._data) == 1:
            return self._data[0]
        raise SingleMatchRequiredError()

    def __getitem__(self, item):
        """
        Retrieve an item by index or a new DummyBucket for a slice.

        Parameters:
            item (int or slice): Index of the element to return, or a slice describing the subset to return.

        Returns:
            The element at the given index, or a new DummyBucket containing the sliced elements.
        """
        if isinstance(item, slice):
            return DummyBucket(self._manager_class, self._data[item])
        return self._data[item]

    def __len__(self):
        """
        Returns the number of elements contained in the bucket.
        """
        return len(self._data)

    def __contains__(self, item):
        """
        Returns True if the specified item is present in the bucket; otherwise, returns False.
        """
        return item in self._data

    def sort(self, key, reverse=False):
        """
        Create a new DummyBucket containing the bucket's elements in sorted order.

        Parameters:
            key: Ignored — accepted for API compatibility.
            reverse (bool): If True, sort in descending order; otherwise sort in ascending order.

        Returns:
            A new DummyBucket containing the sorted elements.
        """
        sorted_data = sorted(self._data, key=key, reverse=reverse)
        return DummyBucket(self._manager_class, sorted_data)


class BucketTests(SimpleTestCase):
    def setUp(self):
        """
        Initializes test fixtures for DummyBucket tests.

        Creates an empty DummyBucket and another DummyBucket containing the integers 3, 1, and 2, both using DummyManager as the manager class.
        """
        self.manager_class = DummyManager
        self.empty = DummyBucket(self.manager_class, [])
        self.bucket = DummyBucket(self.manager_class, [3, 1, 2])

    def test_eq_and_neq(self):
        """
        Tests equality and inequality of DummyBucket instances.

        Asserts that buckets with the same data are equal, while those with different data or types are not considered equal.
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

        Asserts that the '|' operator combines the data of two DummyBucket instances or appends an integer to the bucket's data, producing a new DummyBucket with the expected contents.
        """
        b1 = DummyBucket(self.manager_class, [1])
        b2 = DummyBucket(self.manager_class, [2])
        combined = b1 | b2
        self.assertEqual(combined._data, [1, 2])
        plus_item = b1 | 5
        self.assertEqual(plus_item._data, [1, 5])

    def test_iter_and_list(self):
        """
        Tests that iterating over the bucket yields the correct list of elements.
        """
        self.assertEqual(list(self.bucket), [3, 1, 2])

    def test_filter_and_exclude(self):
        """
        Verifies that the filter and exclude methods return new buckets with updated filters and excludes dictionaries.
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
        Tests the get() method without arguments.

        Ensures get() returns the single element if the bucket contains exactly one item, and raises ValueError if the bucket is empty or contains multiple elements.
        """
        single = DummyBucket(self.manager_class, [42])
        self.assertEqual(single.get(), 42)
        with self.assertRaises(ValueError):
            self.bucket.get()

    def test_get_by_value(self):
        """
        Tests that get(value=...) retrieves the unique matching item or raises ValueError.

        Ensures get(value=...) returns the correct item when exactly one match exists, raises ValueError if no match is found, and raises ValueError if multiple matches are present.
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
        Tests indexing and slicing behavior of DummyBucket.

        Asserts that indexing returns the correct element and slicing returns a new DummyBucket containing the expected subset of data.
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
        Tests that the sort method returns a new DummyBucket with elements sorted in ascending or descending order.
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
        Tests grouping a DummyBucket by valid attribute keys.

        Asserts that grouping by valid keys returns a GroupBucket with the correct manager class and grouping keys.
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
