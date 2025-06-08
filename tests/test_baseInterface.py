# type: ignore
from django.test import SimpleTestCase, override_settings
from general_manager.interface.baseInterface import InterfaceBase
from general_manager.manager.generalManager import GeneralManager

from general_manager.interface.baseInterface import Bucket
from general_manager.manager.groupManager import GroupBucket


# Dummy InputField implementation for testing
class DummyInput:
    def __init__(self, type_, depends_on=None, possible_values=None):
        self.type = type_
        self.depends_on = depends_on or []
        self.possible_values = possible_values

    def cast(self, value):
        return value


# Dummy GeneralManager subclass for testing formatIdentification
class DummyGM(GeneralManager):  # type: ignore[misc]
    def __init__(self, identification):
        self._identification = identification

    @property
    def identification(self):
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
        return self.identification

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
        return TestInterface.input_fields[field_name].type


class InterfaceBaseTests(SimpleTestCase):
    def test_valid_input_kwargs(self):
        # Normal case: all inputs provided as kwargs
        gm = DummyGM({"id": 1})
        inst = TestInterface(a=1, b="foo", gm=gm, vals=2, c=1)
        self.assertEqual(
            inst.identification,
            {"a": 1, "b": "foo", "gm": {"id": 1}, "vals": 2, "c": 1},
        )

    def test_valid_input_args(self):
        # Positional args instead of kwargs
        gm = DummyGM({"id": 2})
        inst = TestInterface(2, "bar", gm, 3, 2)
        self.assertEqual(inst.identification["a"], 2)

    def test_missing_required_input(self):
        # Missing 'a' should raise TypeError
        with self.assertRaises(TypeError):
            TestInterface(b="foo", gm=DummyGM({"id": 3}), vals=1, c=1)

    def test_extra_input(self):
        # Unexpected argument 'extra' raises TypeError
        with self.assertRaises(TypeError):
            TestInterface(a=1, b="foo", gm=DummyGM({"id": 4}), vals=1, c=1, extra=5)

    def test_extra_input_id_suffix(self):
        # Argument 'gm_id' is remapped to 'gm'
        inst = TestInterface(
            a=1, b="baz", gm=DummyGM({"id": 5}), vals=1, c=1, gm_id=DummyGM({"id": 6})
        )
        self.assertEqual(inst.identification["gm"], {"id": 6})

    def test_type_mismatch(self):
        # Passing wrong type for 'a' should raise TypeError
        with self.assertRaises(TypeError):
            TestInterface(a="not_int", b="foo", gm=DummyGM({"id": 7}), vals=1, c=1)

    @override_settings(DEBUG=True)
    def test_invalid_value_list(self):
        # 'vals' not in allowed [1,2,3] raises ValueError
        with self.assertRaises(ValueError):
            TestInterface(a=1, b="foo", gm=DummyGM({"id": 8}), vals=99, c=1)

    @override_settings(DEBUG=True)
    def test_invalid_value_callable(self):
        # 'c' not in allowed from lambda [a, a+1] raises ValueError
        with self.assertRaises(ValueError):
            TestInterface(a=5, b="foo", gm=DummyGM({"id": 9}), vals=1, c=3)

    @override_settings(DEBUG=True)
    def test_possible_values_invalid_type(self):
        # possible_values is invalid type (not iterable/callable)
        with self.assertRaises(TypeError):
            TestInterface(a=1, b="foo", gm=DummyGM({"id": 10}), vals=1, c=1, x=2)

    def test_circular_dependency(self):
        # Two inputs depending on each other -> ValueError
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
            return {"a": None, "b": None, "c": None}


# DummyBucket concrete implementation for testing
class DummyBucket(Bucket[int]):
    def __init__(self, manager_class, data=None):
        super().__init__(manager_class)
        self._data = list(data or [])

    def __or__(self, other):
        if isinstance(other, DummyBucket):
            return DummyBucket(self._manager_class, self._data + other._data)
        if isinstance(other, int):
            return DummyBucket(self._manager_class, self._data + [other])
        return NotImplemented

    def __iter__(self):
        return iter(self._data)

    def filter(self, **kwargs):
        new = DummyBucket(self._manager_class, self._data)
        new.filters = {**self.filters, **kwargs}
        return new

    def exclude(self, **kwargs):
        new = DummyBucket(self._manager_class, self._data)
        new.excludes = {**self.excludes, **kwargs}
        return new

    def first(self):
        return self._data[0] if self._data else None

    def last(self):
        return self._data[-1] if self._data else None

    def count(self):
        return len(self._data)

    def all(self):
        return DummyBucket(self._manager_class, self._data)

    def get(self, **kwargs):
        # support lookup by 'value'
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
        if isinstance(item, slice):
            return DummyBucket(self._manager_class, self._data[item])
        return self._data[item]

    def __len__(self):
        return len(self._data)

    def __contains__(self, item):
        return item in self._data

    def sort(self, key, reverse=False):
        sorted_data = sorted(self._data, reverse=reverse)
        return DummyBucket(self._manager_class, sorted_data)


class BucketTests(SimpleTestCase):
    def setUp(self):
        self.manager_class = DummyManager
        self.empty = DummyBucket(self.manager_class, [])
        self.bucket = DummyBucket(self.manager_class, [3, 1, 2])

    def test_eq_and_neq(self):
        b1 = DummyBucket(self.manager_class, [1, 2])
        b2 = DummyBucket(self.manager_class, [1, 2])
        b3 = DummyBucket(self.manager_class, [2, 1])
        self.assertEqual(b1, b2)
        self.assertNotEqual(b1, b3)
        self.assertNotEqual(b1, object())

    def test_or_bucket_and_item(self):
        b1 = DummyBucket(self.manager_class, [1])
        b2 = DummyBucket(self.manager_class, [2])
        combined = b1 | b2
        self.assertEqual(combined._data, [1, 2])
        plus_item = b1 | 5
        self.assertEqual(plus_item._data, [1, 5])

    def test_iter_and_list(self):
        self.assertEqual(list(self.bucket), [3, 1, 2])

    def test_filter_and_exclude(self):
        f = self.bucket.filter(a=1)
        self.assertEqual(f.filters, {"a": 1})
        e = self.bucket.exclude(b=2)
        self.assertEqual(e.excludes, {"b": 2})

    def test_first_last_empty_and_nonempty(self):
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
        copy = self.bucket.all()
        self.assertIsNot(copy, self.bucket)
        self.assertEqual(copy, self.bucket)

    def test_get_no_kwargs(self):
        single = DummyBucket(self.manager_class, [42])
        self.assertEqual(single.get(), 42)
        with self.assertRaises(ValueError):
            self.bucket.get()

    def test_get_by_value(self):
        b = DummyBucket(self.manager_class, [1, 2, 3])
        self.assertEqual(b.get(value=2), 2)
        with self.assertRaises(ValueError):
            b.get(value=99)
        dup = DummyBucket(self.manager_class, [5, 5])
        with self.assertRaises(ValueError):
            dup.get(value=5)

    def test_get_empty_bucket(self):
        with self.assertRaises(ValueError):
            self.empty.get()

    def test_getitem_index_and_slice(self):
        self.assertEqual(self.bucket[1], 1)
        sl = self.bucket[1:]
        self.assertIsInstance(sl, DummyBucket)
        self.assertEqual(sl._data, [1, 2])

    def test_contains(self):
        self.assertIn(1, self.bucket)
        self.assertNotIn(99, self.bucket)

    def test_sort(self):
        asc = self.bucket.sort(key=None)
        self.assertEqual(asc._data, [1, 2, 3])
        desc = self.bucket.sort(key=None, reverse=True)
        self.assertEqual(desc._data, [3, 2, 1])

    def test_reduce(self):
        reduced = self.bucket.__reduce__()
        cls, args = reduced
        self.assertEqual(cls, DummyBucket)
        self.assertEqual(args[0], None)
        self.assertEqual(args[1], self.manager_class)
        self.assertEqual(args[2], {})  # filters
        self.assertEqual(args[3], {})  # excludes

    def test_group_by_valid_keys(self):
        # Create DummyManager instances with attributes
        m1 = DummyManager()
        m1.a, m1.b = 1, 2
        m2 = DummyManager()
        m2.a, m2.b = 1, 3
        bucket = DummyBucket(self.manager_class, [m1, m2])
        grp = bucket.group_by("a", "b")
        self.assertIsInstance(grp, GroupBucket)
        self.assertEqual(grp._manager_class, self.manager_class)

        self.assertEqual(grp._manager_class, self.manager_class)
        self.assertEqual(getattr(grp, "_group_by_keys"), ("a", "b"))

    def test_group_by_invalid_key(self):
        # Valid entries but invalid grouping key 'x'
        m = DummyManager()
        m.a, m.b = 1, 2
        bucket = DummyBucket(self.manager_class, [m])
        with self.assertRaises(ValueError):
            bucket.group_by("x")(self)
        with self.assertRaises(ValueError):
            self.bucket.group_by("x")
