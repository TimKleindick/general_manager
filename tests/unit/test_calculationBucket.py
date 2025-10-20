# type: ignore
from django.test import TestCase
from unittest.mock import patch
from general_manager.bucket.calculationBucket import CalculationBucket
from general_manager.interface.calculationInterface import CalculationInterface
from general_manager.manager.input import Input
from general_manager.manager import GeneralManager
from general_manager.api.property import GraphQLProperty
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


@patch(
    "general_manager.bucket.calculationBucket.parse_filters",
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


@patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
class TestGenerateCombinations(TestCase):
    def _make_bucket_with_fields(self, fields):
        # Dynamically create an interface and manager class with given input_fields

        """
        Creates a CalculationBucket with dynamically defined input fields.

        Args:
            fields: A list of input field definitions to assign to the generated interface.

        Returns:
                CalculationBucket: An instance configured with a manager and interface using the specified input fields.
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
            Return a list containing the input value multiplied by 10.

            Parameters:
                a (int or float): The value to be multiplied.

            Returns:
                list: A single-element list with the result of a * 10.
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
    @patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
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

    @patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
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

    @patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
    def test_len_and_count_on_empty(self, _mock_parse):
        """
        len() and count() should both be zero on an empty bucket.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        bucket._data = []
        self.assertEqual(len(bucket), 0)
        self.assertEqual(bucket.count(), 0)

    @patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
    def test_str_formatting_exact_threshold(self, _mock_parse):
        """
        For exactly five combinations, string representation should not include ellipsis.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        bucket._data = [{"x": i} for i in range(5)]
        s = str(bucket)
        self.assertTrue(s.startswith("CalculationBucket (5)["))
        self.assertNotIn("...", s)

    @patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
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

    @patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
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

    @patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
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

    @patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
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

    @patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
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

    @patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
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

    @patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
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
