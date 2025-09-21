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
            True if the other object is a DummyGeneralManager with identical kwargs; otherwise, False.
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
        Tests that combining a CalculationBucket with an incompatible type or a bucket of a different manager class raises a ValueError.
        """
        b1 = CalculationBucket(DummyGeneralManager)
        # Combining with different type should raise
        with self.assertRaises(ValueError):
            _ = b1 | 123

        # Combining with bucket of different manager class should raise
        class OtherManager:
            Interface = DummyCalculationInterface

        b2 = CalculationBucket(OtherManager)
        with self.assertRaises(ValueError):
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
        self.assertIs(bucket.all(), bucket)
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
            A CalculationBucket instance using a dynamically created manager and interface with the specified input fields.
        """

        class DynInterface(CalculationInterface):
            input_fields = fields

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


# ---------------------------------------------------------------------------
# Additional tests appended by CodeRabbit: expand coverage per PR diff focus.
# Note on framework: These tests use Django's TestCase (unittest style) to
# match the project's existing testing stack; they will run under Django's
# test runner or pytest via pytest-django without introducing new deps.
# ---------------------------------------------------------------------------


@patch("general_manager.bucket.calculationBucket.parse_filters", return_value={"parsed": {}})
class TestCalculationBucket_More(TestCase):
    def test_filters_lazy_parsing_single_call(self, mock_parse):
        """
        Ensures that filters are parsed lazily and cached; accessing twice
        should only invoke parse_filters once.
        """
        fdefs = {"n": {"filter_kwargs": {"eq": 1}}}
        bucket = CalculationBucket(manager_class=DummyGeneralManager, filter_definitions=fdefs)
        # Access twice; should only parse once
        _ = bucket.filters
        _ = bucket.filters
        self.assertEqual(mock_parse.call_count, 1)
        mock_parse.assert_called_with(fdefs)

    def test_excludes_lazy_parsing_single_call(self, mock_parse):
        """
        Ensures that excludes are parsed lazily and cached; accessing twice
        should only invoke parse_filters once.
        """
        edefs = {"n": {"filter_kwargs": {"neq": 2}}}
        bucket = CalculationBucket(manager_class=DummyGeneralManager, exclude_definitions=edefs)
        _ = bucket.excludes
        _ = bucket.excludes
        self.assertEqual(mock_parse.call_count, 1)
        mock_parse.assert_called_with(edefs)

    def test_getitem_out_of_range_raises_index_error(self, _mock_parse):
        """
        Indexing beyond available combinations should raise IndexError.
        """
        bucket = CalculationBucket(manager_class=DummyGeneralManager)
        bucket._data = [{"i": 0}, {"i": 1}]
        with self.assertRaises(IndexError):
            _ = bucket[5]

    def test_getitem_negative_index_returns_manager(self, _mock_parse):
        """
        Negative indices should work similarly to Python sequences and return a manager instance.
        """
        bucket = CalculationBucket(manager_class=DummyGeneralManager)
        bucket._data = [{"i": 1}, {"i": 2}, {"i": 3}]
        mgr = bucket[-1]
        self.assertIsInstance(mgr, DummyGeneralManager)
        self.assertEqual(mgr, DummyGeneralManager(i=3))

    def test_sort_preserves_filters_and_excludes(self, _mock_parse):
        """
        sort() should return a new CalculationBucket while preserving filter/exclude definitions.
        """
        fdefs = {"f": {"filter_kwargs": {"f": 1}}}
        edefs = {"e": {"filter_kwargs": {"e": 2}}}
        bucket = CalculationBucket(DummyGeneralManager, fdefs, edefs, None, False)
        sorted_bucket = bucket.sort(key="x", reverse=False)
        # New instance with updated sort settings
        self.assertIsInstance(sorted_bucket, CalculationBucket)
        self.assertIsNot(sorted_bucket, bucket)
        self.assertEqual(sorted_bucket.sort_key, "x")
        self.assertFalse(sorted_bucket.reverse)
        # Definitions preserved
        self.assertEqual(sorted_bucket.filter_definitions, fdefs)
        self.assertEqual(sorted_bucket.exclude_definitions, edefs)
        # Original unchanged
        self.assertIsNone(bucket.sort_key)
        self.assertEqual(bucket.filter_definitions, fdefs)
        self.assertEqual(bucket.exclude_definitions, edefs)

    def test_repr_non_default_parameters(self, _mock_parse):
        """
        repr should reflect constructor arguments including non-default values.
        """
        fdefs = {"f": {"filter_kwargs": {"f": 1}}}
        edefs = {"e": {"filter_kwargs": {"e": 2}}}
        bucket = CalculationBucket(DummyGeneralManager, fdefs, edefs, "k", True)
        expected = f"CalculationBucket({DummyGeneralManager.__name__}, {fdefs}, {edefs}, 'k', True)"
        self.assertEqual(repr(bucket), expected)

    def test_str_exactly_five_items_no_ellipsis(self, _mock_parse):
        """
        When exactly five combinations exist, str() should not include an ellipsis.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        bucket._data = [{"x": i} for i in range(5)]
        s = str(bucket)
        self.assertTrue(s.startswith("CalculationBucket (5)["))
        self.assertNotIn("...", s)

    def test_or_does_not_mutate_operands(self, _mock_parse):
        """
        Verifies that the bitwise OR operation produces a new bucket and does not mutate the operands.
        """
        b1 = CalculationBucket(DummyGeneralManager, {"f1": 1}, {"e1": 2})
        b2 = CalculationBucket(DummyGeneralManager, {"f1": 1, "f2": 3}, {"e1": 2, "e2": 4})
        combined = b1 | b2
        self.assertIsInstance(combined, CalculationBucket)
        # Operands remain unchanged
        self.assertEqual(b1.filter_definitions, {"f1": 1})
        self.assertEqual(b1.exclude_definitions, {"e1": 2})
        self.assertEqual(b2.filter_definitions, {"f1": 1, "f2": 3})
        self.assertEqual(b2.exclude_definitions, {"e1": 2, "e2": 4})


@patch("general_manager.bucket.calculationBucket.parse_filters", return_value={})
class TestGenerateCombinations_More(TestCase):
    def _bucket_with_fields(self, fields):
        """
        Helper: build a CalculationBucket with dynamic Interface fields.
        """
        class DynInterface(CalculationInterface):
            input_fields = fields

        class DynManager:
            Interface = DynInterface
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                self.identification = kwargs

        DynInterface._parent_class = DynManager
        return CalculationBucket(DynManager)

    def test_iterator_possible_values(self, _mock_parse):
        """
        Supports iterators (single-consumption iterables) as possible_values.
        """
        fields = {"x": Input(type=int, possible_values=iter([10, 20]))}
        bucket = self._bucket_with_fields(fields)
        combos = bucket.generate_combinations()
        self.assertEqual([d["x"] for d in combos], [10, 20])

    def test_tuple_possible_values_preserve_duplicates(self, _mock_parse):
        """
        Supports tuple inputs and preserves duplicate values.
        """
        fields = {"x": Input(type=int, possible_values=(5, 4, 5))}
        bucket = self._bucket_with_fields(fields)
        combos = bucket.generate_combinations()
        self.assertEqual([d["x"] for d in combos], [5, 4, 5])

    def test_multilevel_dependency_chain(self, _mock_parse):
        """
        Handles multi-level dependencies: c depends on b, b depends on a.
        """
        fields = {
            "a": Input(type=int, possible_values=[1]),
            "b": Input(type=int, possible_values=lambda a: [a + 1], depends_on=["a"]),
            "c": Input(type=int, possible_values=lambda b: [b * 10], depends_on=["b"]),
        }
        bucket = self._bucket_with_fields(fields)
        combos = bucket.generate_combinations()
        self.assertEqual(combos, [{"a": 1, "b": 2, "c": 20}])

    def test_len_triggers_generation(self, _mock_parse):
        """
        len(bucket) should lazily generate combinations when not yet computed.
        """
        fields = {
            "x": Input(type=int, possible_values=[1, 2, 3]),
            "y": Input(type=str, possible_values=["a"]),
        }
        bucket = self._bucket_with_fields(fields)
        self.assertEqual(len(bucket), 3)
        # Ensure iteration after len() yields same count
        self.assertEqual(len(list(bucket)), 3)

    def test_slice_with_step(self, _mock_parse):
        """
        Slicing with a step should return a new CalculationBucket reflecting the stepped subset.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        bucket._data = [{"i": 1}, {"i": 2}, {"i": 3}, {"i": 4}, {"i": 5}]
        sliced = bucket[::2]
        self.assertIsInstance(sliced, CalculationBucket)
        self.assertEqual(sliced._data, [{"i": 1}, {"i": 3}, {"i": 5}])

    def test_missing_dependency_raises(self, _mock_parse):
        """
        A field declaring an unknown dependency should raise an error.
        We accept KeyError or ValueError depending on implementation specifics.
        """
        fields = {
            "b": Input(type=int, possible_values=lambda a: [a], depends_on=["a"]),
        }
        bucket = self._bucket_with_fields(fields)
        with self.assertRaises((KeyError, ValueError, TypeError)):
            bucket.generate_combinations()