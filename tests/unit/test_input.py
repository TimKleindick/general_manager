from django.test import TestCase
from unittest.mock import patch
from general_manager.manager.input import Input
from general_manager.measurement import Measurement
from datetime import date, datetime


class TestInput(TestCase):

    def test_simple_input_initialization(self):
        """
        Test that initializing an Input with a type sets the type attribute, leaves possible_values as None, and depends_on as an empty list.
        """
        input_obj = Input(int)
        self.assertEqual(input_obj.type, int)
        self.assertIsNone(input_obj.possible_values)
        self.assertEqual(input_obj.depends_on, [])

    def test_input_initialization_with_callable_possible_values(self):
        """
        Test initialization of Input with a callable for possible_values.

        Ensures that the type is set to int, possible_values references the provided callable, and depends_on is an empty list.
        """

        def possible_values_func():
            """
            Return a list of possible values for input, specifically the integers 1, 2, and 3.

            Returns:
                list: A list containing the integers 1, 2, and 3.
            """
            return [1, 2, 3]

        input_obj = Input(int, possible_values=possible_values_func)
        self.assertEqual(input_obj.type, int)
        self.assertEqual(input_obj.possible_values, possible_values_func)
        self.assertEqual(input_obj.depends_on, [])

    def test_input_initialization_with_list_depends_on(self):
        """
        Verify that initializing an Input with a list for depends_on sets the type to int, possible_values to None, and depends_on to the provided list.
        """
        input_obj = Input(int, depends_on=["input1", "input2"])
        self.assertEqual(input_obj.type, int)
        self.assertIsNone(input_obj.possible_values)
        self.assertEqual(input_obj.depends_on, ["input1", "input2"])

    def test_input_initialization_with_type_not_matching_possible_values(self):
        """
        Test that Input accepts possible_values of a different type than its declared type without validation.

        Verifies that the Input object sets the type and possible_values attributes as provided, even when their types do not match.
        """
        input_obj = Input(str, possible_values=[1, 2, 3])
        self.assertEqual(input_obj.type, str)
        self.assertEqual(input_obj.possible_values, [1, 2, 3])
        self.assertEqual(input_obj.depends_on, [])

    def test_input_initialization_with_callable_and_list_depends_on(self):
        """
        Test initialization of Input with both a callable for possible_values and a list for depends_on.

        Verifies that the type, possible_values, and depends_on attributes are correctly assigned when both are provided during Input initialization.
        """

        def possible_values_func():
            """
            Return a list of possible values for input, specifically the integers 1, 2, and 3.

            Returns:
                list: A list containing the integers 1, 2, and 3.
            """
            return [1, 2, 3]

        input_obj = Input(
            int, possible_values=possible_values_func, depends_on=["input1"]
        )
        self.assertEqual(input_obj.type, int)
        self.assertEqual(input_obj.possible_values, possible_values_func)
        self.assertEqual(input_obj.depends_on, ["input1"])

    def test_simple_input_casting(self):
        """
        Test that the Input class casts values to integers and raises exceptions for invalid inputs.

        Casts valid string, integer, and float inputs to int, and ensures that invalid strings, None, or unsupported types raise ValueError or TypeError.
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
        """
        Test that Input correctly casts values to a GeneralManager subclass.

        Casts a dictionary or integer to an Input configured for a GeneralManager subclass and verifies the resulting instance has the expected `id` attribute. Uses mocking to simulate subclass checks.
        """

        class MockGeneralManager:
            def __init__(self, id):
                """
                Initialize an instance with the specified identifier.

                Parameters:
                    id: The identifier to assign to the instance.
                """
                self.id = id

        with patch("general_manager.manager.input.issubclass", return_value=True):
            input_obj = Input(MockGeneralManager)
            self.assertEqual(input_obj.cast({"id": 1}).id, 1)
            self.assertEqual(input_obj.cast(2).id, 2)

    def test_input_casting_with_date(self):
        """
        Test that the Input class casts values to date objects and raises exceptions for invalid inputs.

        Casts ISO format strings, date, and datetime objects to date. Asserts that invalid strings, None, or unsupported types raise ValueError or TypeError.
        """
        input_obj = Input(date)
        self.assertEqual(input_obj.cast(date(2023, 10, 1)), date(2023, 10, 1))
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
        """
        Tests that the Input class casts values to datetime objects.

        Casts ISO format strings and date objects to datetime, and verifies that invalid strings, None, or unsupported types raise exceptions.
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
        """
        Test that the Input class casts values to Measurement instances or raises exceptions for invalid input.

        Casts valid measurement strings and Measurement objects to Measurement instances. Raises ValueError for invalid strings and TypeError for None or unsupported types.
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
