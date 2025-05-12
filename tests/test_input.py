from django.test import TestCase
from unittest.mock import patch
from general_manager.manager.input import Input
from general_manager.measurement import Measurement
from datetime import date, datetime


class TestInput(TestCase):

    def test_simple_input_initialization(self):
        # Test with a simple type
        input_obj = Input(int)
        self.assertEqual(input_obj.type, int)
        self.assertIsNone(input_obj.possible_values)
        self.assertEqual(input_obj.depends_on, [])

    def test_input_initialization_with_callable_possible_values(self):
        # Test with a callable for possible_values
        """
        Tests that initializing Input with a callable possible_values assigns attributes correctly.
        
        Verifies that the type is set, possible_values references the callable, and depends_on is an empty list.
        """
        def possible_values_func():
            return [1, 2, 3]

        input_obj = Input(int, possible_values=possible_values_func)
        self.assertEqual(input_obj.type, int)
        self.assertEqual(input_obj.possible_values, possible_values_func)
        self.assertEqual(input_obj.depends_on, [])

    def test_input_initialization_with_list_depends_on(self):
        # Test with a list for depends_on
        """
        Tests that initializing an Input with a list for depends_on sets the attribute correctly.
        
        Verifies that the type is set to int, possible_values is None, and depends_on matches the provided list.
        """
        input_obj = Input(int, depends_on=["input1", "input2"])
        self.assertEqual(input_obj.type, int)
        self.assertIsNone(input_obj.possible_values)
        self.assertEqual(input_obj.depends_on, ["input1", "input2"])

    def test_input_initialization_with_type_not_matching_possible_values(self):
        # Test with a type that doesn't match the possible_values
        """
        Tests initialization of Input with a type that does not match possible_values.
        
        Verifies that the Input object accepts possible_values of a different type than the declared type without validation, and sets attributes accordingly.
        """
        input_obj = Input(str, possible_values=[1, 2, 3])
        self.assertEqual(input_obj.type, str)
        self.assertEqual(input_obj.possible_values, [1, 2, 3])
        self.assertEqual(input_obj.depends_on, [])

    def test_input_initialization_with_callable_and_list_depends_on(self):
        # Test with both callable and list for depends_on
        """
        Tests that an Input can be initialized with a callable for possible_values and a list for depends_on.
        
        Verifies that the type, possible_values, and depends_on attributes are set correctly when both are provided.
        """
        def possible_values_func():
            return [1, 2, 3]

        input_obj = Input(
            int, possible_values=possible_values_func, depends_on=["input1"]
        )
        self.assertEqual(input_obj.type, int)
        self.assertEqual(input_obj.possible_values, possible_values_func)
        self.assertEqual(input_obj.depends_on, ["input1"])

    def test_simple_input_casting(self):
        # Test casting a value to the specified type
        """
        Tests that the Input class correctly casts values to integers and raises exceptions for invalid inputs.
        
        Verifies successful casting from strings, integers, and floats to int, and asserts that invalid strings, None, or unsupported types raise ValueError or TypeError.
        """
        input_obj = Input(int)
        self.assertEqual(input_obj.cast("123"), 123)
        self.assertEqual(input_obj.cast(456), 456)
        self.assertEqual(input_obj.cast(789.0), 789)

        with self.assertRaises(ValueError):
            input_obj.cast("abc")
        with self.assertRaises(TypeError):
            input_obj.cast(None)
        with self.assertRaises(TypeError):
            input_obj.cast([1, 2, 3])

    def test_input_casting_with_general_manager(self):
        # Test casting with a GeneralManager subclass
        """
        Tests casting to a GeneralManager subclass using the Input class.
        
        Verifies that casting a dictionary or integer to an Input expecting a GeneralManager subclass returns an instance with the correct id attribute. Uses mocking to simulate subclass checks.
        """
        class MockGeneralManager:
            def __init__(self, id):
                self.id = id

        with patch("general_manager.manager.input.issubclass", return_value=True):
            input_obj = Input(MockGeneralManager)
            self.assertEqual(input_obj.cast({"id": 1}).id, 1)
            self.assertEqual(input_obj.cast(2).id, 2)

    def test_input_casting_with_date(self):
        # Test casting with date
        """
        Tests that the Input class correctly casts values to date objects.
        
        Verifies successful casting from ISO format strings and datetime objects to date,
        and asserts that invalid strings, None, or unsupported types raise exceptions.
        """
        input_obj = Input(date)
        self.assertEqual(input_obj.cast("2023-10-01"), date(2023, 10, 1))
        self.assertEqual(
            input_obj.cast(datetime(2023, 10, 1, 12, 1, 5)), date(2023, 10, 1)
        )
        with self.assertRaises(ValueError):
            input_obj.cast("invalid-date")
        with self.assertRaises(TypeError):
            input_obj.cast(None)
        with self.assertRaises(TypeError):
            input_obj.cast([1, 2, 3])

    def test_input_casting_with_datetime(self):
        # Test casting with datetime
        """
        Tests that the Input class correctly casts values to datetime objects.
        
        Verifies successful casting from ISO format strings and date objects to datetime,
        and asserts that invalid strings, None, or unsupported types raise appropriate exceptions.
        """
        input_obj = Input(datetime)
        self.assertEqual(
            input_obj.cast("2023-10-01T12:00:00"), datetime(2023, 10, 1, 12, 0, 0)
        )
        self.assertEqual(
            input_obj.cast(date(2023, 10, 1)), datetime(2023, 10, 1, 0, 0, 0)
        )
        with self.assertRaises(ValueError):
            input_obj.cast("invalid-datetime")
        with self.assertRaises(TypeError):
            input_obj.cast(None)
        with self.assertRaises(TypeError):
            input_obj.cast([1, 2, 3])

    def test_input_casting_with_measurement(self):
        # Test casting with Measurement
        """
        Tests that the Input class correctly casts values to Measurement instances.
        
        Verifies successful casting from valid measurement strings and Measurement objects, and asserts that invalid strings, None, or unsupported types raise appropriate exceptions.
        """
        input_obj = Input(Measurement)
        self.assertEqual(input_obj.cast("1.0 m"), Measurement(1.0, "m"))
        self.assertEqual(input_obj.cast(Measurement(2.0, "m")), Measurement(2.0, "m"))
        with self.assertRaises(ValueError):
            input_obj.cast("invalid-measurement")
        with self.assertRaises(TypeError):
            input_obj.cast(None)
        with self.assertRaises(TypeError):
            input_obj.cast([1, 2, 3])
