# type: ignore
from django.test import SimpleTestCase, override_settings
from general_manager.interface.baseInterface import InterfaceBase
from general_manager.manager.generalManager import GeneralManager

from general_manager.interface.baseInterface import Bucket
from general_manager.manager.groupManager import GroupBucket


# Dummy InputField implementation for testing
class DummyInput:
    def __init__(self, type_, depends_on=None, possible_values=None):
        """
        Initializes a DummyInput instance with a type, dependencies, and possible values.

        Args:
            type_: The expected type for the input value.
            depends_on: Optional list of field names this input depends on.
            possible_values: Optional list or callable specifying allowed values for the input.
        """
        self.type = type_
        self.depends_on = depends_on or []
        self.possible_values = possible_values

    def cast(self, value):
        """
        Returns the input value unchanged.

        Args:
            value: The value to be returned.

        Returns:
            The same value that was provided as input.
        """
        return value


# Dummy GeneralManager subclass for testing formatIdentification
class DummyGM(GeneralManager):  # type: ignore[misc]
    def __init__(self, identification):
        """
        Initializes the DummyGM instance with the given identification value.
        """
        self._identification = identification

    @property
    def identification(self):
        """
        Returns the identification value associated with this instance.
        """
        return self._identification


# Concrete test implementation of InterfaceBase
test_input_fields = {
    "a": DummyInput(int),
    "b": DummyInput(str, depends_on=["a"]),
    "gm": DummyInput(DummyGM),
    "vals": DummyInput(int, possible_values=[1, 2, 3]),
    "c": DummyInput(int, depends_on=["a"], possible_values=lambda a: [a, a + 1]),
}


class TestInterface(InterfaceBase):
    input_fields = test_input_fields

    def getData(self, search_date=None):
        """
        Returns the identification associated with this interface instance.

        Args:
            search_date: Optional parameter for compatibility; ignored in this implementation.

        Returns:
            The identification value of the interface.
        """
        return self.identification

    @classmethod
    def getAttributeTypes(cls):
        """
        Returns an empty dictionary representing attribute types for the class.
        """
        return {}

    @classmethod
    def getAttributes(cls):
        """
        Returns an empty dictionary of attributes for the class.

        Intended as a stub for subclasses to override with actual attribute definitions.
        """
        return {}

    @classmethod
    def filter(cls, **kwargs):
        """
        Filters items based on provided keyword arguments.

        Returns:
            None. This is a stub implementation for testing purposes.
        """
        return None

    @classmethod
    def exclude(cls, **kwargs):
        """
        Stub method for excluding items based on provided criteria.

        Returns:
            None
        """
        return None

    @classmethod
    def handleInterface(cls):
        """
        Returns stub handler functions for interface processing.

        The first returned function accepts any arguments and returns a tuple of the arguments, an empty dictionary, and None. The second returned function accepts any arguments and returns None.
        """
        return (lambda *args: (args, {}, None), lambda *args: None)

    @classmethod
    def getFieldType(cls, field_name):
        """
        Returns the type of the specified input field.

        Args:
            field_name: The name of the input field.

        Returns:
            The type associated with the given input field.
        """
        return TestInterface.input_fields[field_name].type


class InterfaceBaseTests(SimpleTestCase):
    def test_valid_input_kwargs(self):
        # Normal case: all inputs provided as kwargs
        """
        Tests that TestInterface correctly initializes when all required inputs are provided as keyword arguments.
        """
        gm = DummyGM({"id": 1})
        inst = TestInterface(a=1, b="foo", gm=gm, vals=2, c=1)
        self.assertEqual(
            inst.identification,
            {"a": 1, "b": "foo", "gm": {"id": 1}, "vals": 2, "c": 1},
        )

    def test_valid_input_args(self):
        # Positional args instead of kwargs
        """
        Tests that TestInterface correctly accepts valid positional arguments and assigns them to input fields.
        """
        gm = DummyGM({"id": 2})
        inst = TestInterface(2, "bar", gm, 3, 2)
        self.assertEqual(inst.identification["a"], 2)

    def test_missing_required_input(self):
        # Missing 'a' should raise TypeError
        """
        Tests that omitting a required input field when initializing TestInterface raises a TypeError.
        """
        with self.assertRaises(TypeError):
            TestInterface(b="foo", gm=DummyGM({"id": 3}), vals=1, c=1)

    def test_extra_input(self):
        # Unexpected argument 'extra' raises TypeError
        """
        Tests that providing an unexpected input argument raises a TypeError.
        """
        with self.assertRaises(TypeError):
            TestInterface(a=1, b="foo", gm=DummyGM({"id": 4}), vals=1, c=1, extra=5)

    def test_extra_input_id_suffix(self):
        # Argument 'gm_id' is remapped to 'gm'
        """
        Tests that input arguments with an '_id' suffix are remapped to their corresponding field names, overriding previous values.
        """
        inst = TestInterface(
            a=1, b="baz", gm=DummyGM({"id": 5}), vals=1, c=1, gm_id=DummyGM({"id": 6})
        )
        self.assertEqual(inst.identification["gm"], {"id": 6})

    def test_type_mismatch(self):
        # Passing wrong type for 'a' should raise TypeError
        """
        Tests that providing an incorrect type for an input field raises a TypeError.

        Verifies that passing a value of the wrong type for the 'a' field in TestInterface
        results in a TypeError during initialization.
        """
        with self.assertRaises(TypeError):
            TestInterface(a="not_int", b="foo", gm=DummyGM({"id": 7}), vals=1, c=1)

    @override_settings(DEBUG=True)
    def test_invalid_value_list(self):
        # 'vals' not in allowed [1,2,3] raises ValueError
        """
        Tests that providing a value for 'vals' outside the allowed list raises a ValueError.
        """
        with self.assertRaises(ValueError):
            TestInterface(a=1, b="foo", gm=DummyGM({"id": 8}), vals=99, c=1)

    @override_settings(DEBUG=True)
    def test_invalid_value_callable(self):
        # 'c' not in allowed from lambda [a, a+1] raises ValueError
        """
        Tests that providing a value for 'c' not in the allowed set generated by a callable raises ValueError.
        """
        with self.assertRaises(ValueError):
            TestInterface(a=5, b="foo", gm=DummyGM({"id": 9}), vals=1, c=3)

    @override_settings(DEBUG=True)
    def test_possible_values_invalid_type(self):
        # possible_values is invalid type (not iterable/callable)
        """
        Tests that providing a non-iterable, non-callable value for possible_values raises TypeError.
        """
        with self.assertRaises(TypeError):
            TestInterface(a=1, b="foo", gm=DummyGM({"id": 10}), vals=1, c=1, x=2)

    def test_circular_dependency(self):
        # Two inputs depending on each other -> ValueError
        """
        Tests that defining input fields with circular dependencies raises a ValueError.
        """

        class Circ(InterfaceBase):
            input_fields = {
                "a": DummyInput(int, depends_on=["b"]),
                "b": DummyInput(int, depends_on=["a"]),
            }

            def getData(self, search_date=None):
                return {}

            @classmethod
            def getAttributeTypes(cls):
                return {}

            @classmethod
            def getAttributes(cls):
                return {}

            @classmethod
            def filter(cls, **kwargs):
                return None

            @classmethod
            def exclude(cls, **kwargs):
                return None

            @classmethod
            def handleInterface(cls):
                return (lambda *args: (args, {}, None), lambda *args: None)

            @classmethod
            def getFieldType(cls, field_name):
                return int

        with self.assertRaises(ValueError):
            Circ(a=1, b=2)

    def test_format_identification_list_and_gm(self):
        # formatIdentification converts nested GeneralManager and lists correctly
        """
        Tests that formatIdentification correctly converts nested GeneralManager instances and lists to dictionaries within the identification attribute.
        """
        gm = DummyGM({"id": 11})
        inst = TestInterface(a=1, b="foo", gm=gm, vals=2, c=1)
        # inject a mixed list
        inst.identification["mixed"] = [DummyGM({"id": 12}), 42]
        formatted = inst.formatIdentification()
        self.assertEqual(formatted["mixed"], [{"id": 12}, 42])


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
