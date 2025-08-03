# type: ignore
from django.test import TestCase
from unittest.mock import patch
from general_manager.bucket.calculationBucket import CalculationBucket
from general_manager.interface.calculationInterface import CalculationInterface
from general_manager.manager.input import Input


# Create a dummy CalculationInterface with no input fields for simplicity
class DummyCalculationInterface(CalculationInterface):
    input_fields = {}


# Dummy manager class that uses the dummy interface
class DummyGeneralManager:
    Interface = DummyCalculationInterface

    def __init__(self, **kwargs):
        # Initialize with any keyword arguments, simulating a manager
        """
        Initialize the dummy manager with arbitrary keyword arguments.
        
        All provided keyword arguments are stored for later comparison and representation.
        """
        self.kwargs = kwargs

    def __eq__(self, value: object) -> bool:
        """
        Determine if another object is a DummyGeneralManager with identical initialization arguments.
        
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
    def test_initialization_defaults(self, mock_parse):
        # Test basic initialization without optional parameters
        """
        Test that CalculationBucket initializes with default parameters when only the manager class is provided.
        
        Ensures filters and excludes are empty, sort key is None, reverse is False, and input fields are taken from the associated interface.
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

    def test_initialization_with_filters_and_excludes(self, mock_parse):
        # Filters and excludes passed directly to constructor
        """
        Test that CalculationBucket initializes with specified filters, excludes, sort key, and reverse flag.
        
        Verifies that the constructor assigns the provided filter and exclude definitions, sort key, and reverse attribute as expected.
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
        self.assertEqual(bucket.filters, fdefs)
        self.assertEqual(bucket.excludes, edefs)
        self.assertEqual(bucket.sort_key, "key")
        self.assertTrue(bucket.reverse)

    def test_reduce_and_setstate(self, mock_parse):
        # Test pickling support
        """
        Tests that CalculationBucket instances can be pickled and unpickled using __reduce__ and __setstate__.
        
        Ensures that the reduced state includes the current combinations and that restoring state correctly sets the internal combinations on a new instance.
        """
        bucket = CalculationBucket(DummyGeneralManager, {"a": 1}, {"b": 2}, "k", True)
        # Prepopulate state
        bucket._current_combinations = [{"x": 10}]
        cls, args, state = bucket.__reduce__()
        # Check reduce data
        self.assertEqual(cls, CalculationBucket)
        self.assertEqual(args, (DummyGeneralManager, {"a": 1}, {"b": 2}, "k", True))
        self.assertIn("current_combinations", state)
        # Restore state on new instance
        new_bucket = CalculationBucket(*args)
        new_bucket.__setstate__(state)
        self.assertEqual(new_bucket._current_combinations, [{"x": 10}])

    def test_or_with_same_bucket(self, mock_parse):
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
        self.assertEqual(combined.filters, {"f1": 1})
        self.assertEqual(combined.excludes, {"e1": 2})

    def test_or_with_invalid(self, mock_parse):
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

    def test_str_and_repr_formatting(self, mock_parse):
        """
        Tests that the string and repr representations of CalculationBucket instances display the correct format.
        
        Verifies that the string representation shows the total number of combinations and up to five example combinations, using an ellipsis if there are more than five. Also checks that the repr output includes the constructor parameters.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        # Manually set combinations for string formatting tests
        combos = [{"x": i} for i in range(7)]
        bucket._current_combinations = combos
        s = str(bucket)
        # Should show total count and at most 5 entries
        self.assertTrue(s.startswith("CalculationBucket (7)["))
        self.assertIn("...", s)
        # Test below threshold (no ellipsis)
        bucket._current_combinations = combos[:3]
        s2 = str(bucket)
        self.assertFalse("..." in s2)

        s3 = repr(bucket)
        self.assertEqual(
            s3,
            f"CalculationBucket({DummyGeneralManager.__name__}, {{}}, {{}}, None, False)",
        )

    def test_all_iter_len_count(self, mock_parse):
        """
        Test that CalculationBucket's all(), iteration, count(), and length methods return the expected results.
        
        Ensures that all() returns the bucket itself, iteration yields one manager instance per combination, and both count() and len() reflect the number of combinations.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        # Set a single empty combination so manager(**{}) works
        bucket._current_combinations = [{}] * 4
        # all() returns self
        self.assertEqual(bucket.all(), bucket)
        # Iteration yields one manager per combo
        items = list(bucket)
        self.assertEqual(len(items), 4)
        # count() and len() reflect number of combos
        self.assertEqual(bucket.count(), 4)
        self.assertEqual(len(bucket), 4)

    def test_first_last_empty_and_nonempty(self, mock_parse):
        """
        Test that `first()` and `last()` on a `CalculationBucket` return `None` when there are no combinations, and return the same manager instance when only one combination exists.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        # Empty combos
        bucket._current_combinations = []
        self.assertIsNone(bucket.first())
        self.assertIsNone(bucket.last())
        # Single combo
        bucket._current_combinations = [{"test": 1}]
        first = bucket.first()
        last = bucket.last()
        self.assertIsNotNone(first)
        self.assertEqual(first, last)

    def test_getitem_index_and_slice(self, mock_parse):
        """
        Tests that indexing a CalculationBucket returns a manager instance and slicing returns a new CalculationBucket with the correct subset of combinations.
        """
        bucket = CalculationBucket(DummyGeneralManager)
        # Create distinct combos for index and slice
        bucket._current_combinations = [{"i": 1}, {"i": 2}, {"i": 3}]
        # Index __getitem__
        mgr = bucket[1]
        self.assertIsInstance(mgr, DummyGeneralManager)
        # Slice __getitem__
        sliced = bucket[0:2]
        self.assertIsInstance(sliced, CalculationBucket)
        # Sliced bucket should have its own combinations
        self.assertEqual(sliced._current_combinations, [{"i": 1}, {"i": 2}])

    def test_sort_returns_new_bucket(self, mock_parse):
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
        Create a CalculationBucket instance using dynamically generated input fields.
        
        Parameters:
        	fields (list): Input field definitions to assign to the generated interface.
        
        Returns:
        	CalculationBucket: An instance configured with a manager and interface using the specified input fields.
        """

        class DynInterface(CalculationInterface):
            input_fields = fields

        class DynManager:
            Interface = DynInterface

        DynInterface._parent_class = DynManager
        return CalculationBucket(DynManager)

    def test_basic_cartesian_product(self, mock_parse):
        # Two independent fields produce a Cartesian product
        """
        Tests that generate_combinations returns all possible combinations for independent input fields.
        
        Verifies that when two fields have independent possible values, the resulting combinations form the full Cartesian product.
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

    def test_empty_possible_values(self, mock_parse):
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

    def test_dependent_field(self, mock_parse):
        # Field2 depends on field1 and its possible_values is a callable
        """
        Test that dependent input fields with callable possible values generate correct combinations.
        
        Verifies that when a field's possible values are defined as a callable depending on another field, the generated combinations reflect this dependency.
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

    def test_filters_and_excludes(self, mock_parse):
        # Apply filter_funcs to include only even numbers, and exclude a specific value
        """
        Test that filter and exclude functions are correctly applied to input values when generating combinations.
        
        Ensures that only even numbers are included and a specified value is excluded from the resulting combinations.
        """
        fields = {
            "n": Input(type=int, possible_values=[1, 2, 3, 4]),
        }
        bucket = self._make_bucket_with_fields(fields)
        # Manually set filter and exclude definitions
        bucket.filters = {"n": {"filter_funcs": [lambda x: x % 2 == 0]}}
        bucket.excludes = {"n": {"filter_funcs": [lambda x: x == 4]}}
        combos = bucket.generate_combinations()
        # Should include only 2, excluding 4
        self.assertEqual(combos, [{"n": 2}])

    def test_sort_and_reverse_and_caching(self, mock_parse):
        # Three values, sorted and reversed
        """
        Tests that sorting and reversing combinations in a CalculationBucket works as expected and that the generated combinations are cached.
        
        Verifies that combinations are sorted in descending order by the specified key and that repeated calls to `generate_combinations` return the same cached result.
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

    def test_invalid_possible_values_type(self, mock_parse):
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
