from django.test import TestCase
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
from general_manager.manager.input import DateRangeDomain, Input, NumericRangeDomain
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
        self.assertTrue(input_obj.required)
        self.assertIsNone(input_obj.min_value)
        self.assertIsNone(input_obj.max_value)

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

    def test_optional_input_initialization(self):
        input_obj = Input(int, required=False)
        self.assertFalse(input_obj.required)

    def test_input_initialization_with_scalar_constraints(self):
        input_obj = Input(int, min_value=1, max_value=5, validator=lambda _value: True)
        self.assertEqual(input_obj.min_value, 1)
        self.assertEqual(input_obj.max_value, 5)
        self.assertTrue(input_obj.validate_bounds(3))
        self.assertFalse(input_obj.validate_bounds(0))

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

    def test_input_initialization_ignores_variadic_callable_dependencies(self):
        def possible_values_func(a, *args, b=1, **kwargs):
            return [a, b]

        input_obj = Input(int, possible_values=possible_values_func)
        self.assertEqual(input_obj.depends_on, ["a", "b"])

    def test_simple_input_casting(self):
        """
        Test that the Input class casts values to integers and preserves `None`.

        Casts valid string, integer, and float inputs to int, returns `None` unchanged, and ensures that invalid strings or unsupported types raise ValueError or TypeError.
        """
        input_obj = Input(int)
        self.assertEqual(input_obj.cast("123"), 123)
        self.assertEqual(input_obj.cast(456), 456)
        self.assertEqual(input_obj.cast(789.0), 789)

        with self.assertRaises(ValueError):
            input_obj.cast("abc")
        self.assertIsNone(input_obj.cast(None))
        with self.assertRaises(TypeError):
            input_obj.cast([1, 2, 3])

    def test_optional_input_casting_accepts_none(self):
        input_obj = Input(int, required=False)
        self.assertIsNone(input_obj.cast(None))

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
        Test that the Input class casts values to date objects and preserves `None`.

        Casts ISO format strings, date, and datetime objects to date. Asserts that invalid strings or unsupported types raise ValueError or TypeError, while `None` remains `None`.
        """
        input_obj = Input(date)
        self.assertEqual(input_obj.cast(date(2023, 10, 1)), date(2023, 10, 1))
        self.assertEqual(input_obj.cast("2023-10-01"), date(2023, 10, 1))
        self.assertEqual(
            input_obj.cast(datetime(2023, 10, 1, 12, 1, 5)), date(2023, 10, 1)
        )
        with self.assertRaises(ValueError):
            input_obj.cast("invalid-date")
        self.assertIsNone(input_obj.cast(None))
        with self.assertRaises(TypeError):
            input_obj.cast([1, 2, 3])

    def test_input_casting_with_datetime(self):
        """
        Tests that the Input class casts values to datetime objects.

        Casts ISO format strings, datetime objects, and date objects to datetime, preserves `None`, and verifies that invalid strings or unsupported types raise exceptions.
        """
        input_obj = Input(datetime)
        self.assertEqual(
            input_obj.cast("2023-10-01T12:00:00"), datetime(2023, 10, 1, 12, 0, 0)
        )
        self.assertEqual(
            input_obj.cast(datetime(2023, 10, 1, 12, 0, 0)),
            datetime(2023, 10, 1, 12, 0, 0),
        )
        self.assertEqual(
            input_obj.cast(date(2023, 10, 1)), datetime(2023, 10, 1, 0, 0, 0)
        )
        with self.assertRaises(ValueError):
            input_obj.cast("invalid-datetime")
        self.assertIsNone(input_obj.cast(None))
        with self.assertRaises(TypeError):
            input_obj.cast([1, 2, 3])

    def test_input_casting_with_measurement(self):
        """
        Test that the Input class casts values to Measurement instances and preserves `None`.

        Casts valid measurement strings and Measurement objects to Measurement instances. Raises ValueError for invalid strings and TypeError for unsupported types, while `None` remains `None`.
        """
        input_obj = Input(Measurement)
        self.assertEqual(input_obj.cast("1.0 m"), Measurement(1.0, "m"))
        self.assertEqual(input_obj.cast(Measurement(2.0, "m")), Measurement(2.0, "m"))
        with self.assertRaises(ValueError):
            input_obj.cast("invalid-measurement")
        self.assertIsNone(input_obj.cast(None))
        with self.assertRaises(TypeError):
            input_obj.cast([1, 2, 3])

    def test_date_range_domain_contains_and_iterates(self):
        domain = DateRangeDomain(
            date(2024, 1, 1),
            date(2024, 3, 31),
            frequency="month_end",
        )
        self.assertIn(date(2024, 1, 31), list(domain))
        self.assertTrue(domain.contains(date(2024, 2, 10)))
        self.assertFalse(domain.contains(date(2024, 4, 1)))

    def test_numeric_range_domain_contains_and_iterates(self):
        domain = NumericRangeDomain(1, 5, step=2)
        self.assertEqual(list(domain), [1, 3, 5])
        self.assertTrue(domain.contains(3))
        self.assertFalse(domain.contains(4))

    def test_monthly_date_helper_normalizes_values(self):
        input_obj = Input.monthly_date(
            start=date(2024, 1, 1),
            end=date(2024, 3, 31),
            anchor="end",
        )
        self.assertEqual(input_obj.cast("2024-02-10"), date(2024, 2, 29))
        resolved = input_obj.resolve_possible_values({})
        self.assertIsInstance(resolved, DateRangeDomain)

    def test_date_range_helper_infers_dependencies(self):
        input_obj = Input.date_range(
            start=lambda base: base,
            end=lambda limit: limit,
            depends_on=["base", "limit"],
        )
        resolved = input_obj.resolve_possible_values(
            {"base": date(2024, 1, 1), "limit": date(2024, 1, 31)}
        )
        self.assertIsInstance(resolved, DateRangeDomain)
        resolved_values = list(resolved)
        self.assertEqual(next(iter(resolved_values)), date(2024, 1, 1))
        self.assertEqual(resolved_values[-1], date(2024, 1, 31))

    def test_date_range_helper_inferred_dependencies_ignore_variadics(self):
        input_obj = Input.date_range(
            start=lambda base, *_args: base,
            end=lambda limit, **_kwargs: limit,
        )
        self.assertEqual(input_obj.depends_on, ["base", "limit"])

    def test_date_range_domain_with_daily_frequency(self):
        domain = DateRangeDomain(
            date(2024, 1, 1),
            date(2024, 1, 5),
            frequency="day",
        )
        values = list(domain)
        self.assertEqual(values, [date(2024, 1, day) for day in range(1, 6)])

    def test_date_range_domain_with_week_end_frequency(self):
        domain = DateRangeDomain(
            date(2024, 1, 1),
            date(2024, 1, 31),
            frequency="week_end",
        )
        self.assertEqual(
            list(domain),
            [
                date(2024, 1, 7),
                date(2024, 1, 14),
                date(2024, 1, 21),
                date(2024, 1, 28),
            ],
        )

    def test_date_range_domain_with_quarter_end_frequency(self):
        domain = DateRangeDomain(
            date(2024, 1, 1),
            date(2024, 12, 31),
            frequency="quarter_end",
        )
        self.assertEqual(
            list(domain),
            [
                date(2024, 3, 31),
                date(2024, 6, 30),
                date(2024, 9, 30),
                date(2024, 12, 31),
            ],
        )

    def test_date_range_domain_invalid_bounds_raise(self):
        with self.assertRaises(ValueError):
            DateRangeDomain(
                date(2024, 1, 31),
                date(2024, 1, 1),
                frequency="day",
            )

    def test_date_range_domain_single_day(self):
        domain = DateRangeDomain(
            date(2024, 1, 15),
            date(2024, 1, 15),
            frequency="day",
        )
        self.assertEqual(list(domain), [date(2024, 1, 15)])

    def test_date_range_domain_year_end_frequency(self):
        domain = DateRangeDomain(
            date(2020, 1, 1),
            date(2023, 12, 31),
            frequency="year_end",
        )
        values = list(domain)
        self.assertEqual(len(values), 4)
        self.assertTrue(all(value.month == 12 and value.day == 31 for value in values))

    def test_numeric_range_domain_with_float_step(self):
        domain = NumericRangeDomain(0.0, 1.0, step=0.25)
        values = list(domain)
        self.assertEqual(len(values), 5)
        self.assertAlmostEqual(values[0], 0.0)
        self.assertAlmostEqual(values[-1], 1.0)

    def test_numeric_range_domain_with_decimal_step(self):
        domain = NumericRangeDomain(Decimal("0.0"), Decimal("1.0"), step=Decimal("0.5"))
        self.assertEqual(
            list(domain),
            [Decimal("0.0"), Decimal("0.5"), Decimal("1.0")],
        )

    def test_numeric_range_domain_negative_range(self):
        domain = NumericRangeDomain(-10, -1, step=3)
        self.assertEqual(list(domain), [-10, -7, -4, -1])

    def test_numeric_range_domain_invalid_bounds_raise(self):
        with self.assertRaises(ValueError):
            NumericRangeDomain(10, 1, step=1)

    def test_numeric_range_domain_single_value(self):
        domain = NumericRangeDomain(5, 5, step=1)
        self.assertEqual(list(domain), [5])

    def test_numeric_range_domain_zero_step_raises(self):
        with self.assertRaises(ValueError):
            NumericRangeDomain(1, 10, step=0)

    def test_date_range_domain_contains_boundary(self):
        domain = DateRangeDomain(
            date(2024, 1, 1),
            date(2024, 1, 31),
            frequency="day",
        )
        self.assertTrue(domain.contains(date(2024, 1, 1)))
        self.assertTrue(domain.contains(date(2024, 1, 31)))
        self.assertFalse(domain.contains(date(2023, 12, 31)))
        self.assertFalse(domain.contains(date(2024, 2, 1)))

    def test_numeric_range_domain_contains_boundary(self):
        domain = NumericRangeDomain(1, 10, step=1)
        self.assertTrue(domain.contains(1))
        self.assertTrue(domain.contains(10))
        self.assertFalse(domain.contains(0))
        self.assertFalse(domain.contains(11))

    def test_input_with_validator_function(self):
        def is_positive(value):
            return value > 0

        input_obj = Input(int, validator=is_positive)
        self.assertTrue(input_obj.validate_with_callable(5))
        self.assertFalse(input_obj.validate_with_callable(-1))
        self.assertFalse(input_obj.validate_with_callable(0))

    def test_input_with_normalizer_function(self):
        def round_to_nearest_10(value):
            return round(value / 10) * 10

        input_obj = Input(int, normalizer=round_to_nearest_10)
        self.assertEqual(input_obj.cast(47), 50)

    def test_input_validate_bounds_with_min_only(self):
        input_obj = Input(int, min_value=5)
        self.assertTrue(input_obj.validate_bounds(5))
        self.assertTrue(input_obj.validate_bounds(10))
        self.assertFalse(input_obj.validate_bounds(4))

    def test_input_validate_bounds_with_max_only(self):
        input_obj = Input(int, max_value=100)
        self.assertTrue(input_obj.validate_bounds(100))
        self.assertTrue(input_obj.validate_bounds(50))
        self.assertFalse(input_obj.validate_bounds(101))

    def test_input_validate_bounds_with_both_min_max(self):
        input_obj = Input(int, min_value=10, max_value=20)
        self.assertFalse(input_obj.validate_bounds(9))
        self.assertTrue(input_obj.validate_bounds(10))
        self.assertTrue(input_obj.validate_bounds(15))
        self.assertTrue(input_obj.validate_bounds(20))
        self.assertFalse(input_obj.validate_bounds(21))

    def test_input_validate_bounds_without_constraints(self):
        input_obj = Input(int)
        self.assertTrue(input_obj.validate_bounds(-1000))
        self.assertTrue(input_obj.validate_bounds(0))
        self.assertTrue(input_obj.validate_bounds(1000))

    def test_yearly_date_helper(self):
        input_obj = Input.yearly_date(
            start=date(2020, 1, 1),
            end=date(2023, 12, 31),
            anchor="end",
        )
        resolved = input_obj.resolve_possible_values({})
        self.assertIsInstance(resolved, DateRangeDomain)
        values = list(resolved)
        self.assertEqual(len(values), 4)
        self.assertTrue(all(value.month == 12 and value.day == 31 for value in values))

    def test_input_from_manager_query_helper(self):
        class MockManager:
            @classmethod
            def all(cls):
                return ["all"]

        with patch("general_manager.manager.input.issubclass", return_value=True):
            input_obj = Input.from_manager_query(MockManager)

        self.assertEqual(input_obj.type, MockManager)
        self.assertEqual(input_obj.resolve_possible_values({}), ["all"])

    def test_input_from_manager_query_with_filter_dict(self):
        class MockManager:
            @classmethod
            def filter(cls, **kwargs):
                return kwargs

        with patch("general_manager.manager.input.issubclass", return_value=True):
            input_obj = Input.from_manager_query(
                MockManager, query={"status": "active"}
            )

        self.assertEqual(
            input_obj.resolve_possible_values({}),
            {"status": "active"},
        )

    def test_input_resolve_possible_values_with_callable(self):
        def get_values():
            return [1, 2, 3]

        input_obj = Input(int, possible_values=get_values)
        resolved = input_obj.resolve_possible_values({})
        self.assertEqual(resolved, [1, 2, 3])

    def test_input_resolve_possible_values_with_static_list(self):
        input_obj = Input(int, possible_values=[10, 20, 30])
        resolved = input_obj.resolve_possible_values({})
        self.assertEqual(resolved, [10, 20, 30])

    def test_input_resolve_possible_values_with_dependencies(self):
        def get_values(min_val, max_val):
            return list(range(min_val, max_val + 1))

        input_obj = Input(
            int,
            possible_values=get_values,
            depends_on=["min_val", "max_val"],
        )
        resolved = input_obj.resolve_possible_values({"min_val": 5, "max_val": 8})
        self.assertEqual(resolved, [5, 6, 7, 8])

    def test_input_resolve_with_domain_object(self):
        domain = DateRangeDomain(
            date(2024, 1, 1),
            date(2024, 1, 31),
            frequency="day",
        )
        input_obj = Input(date, possible_values=domain)
        self.assertIs(input_obj.resolve_possible_values({}), domain)

    def test_date_range_with_callable_boundaries(self):
        def get_start(base_date):
            return base_date

        def get_end(base_date):
            return base_date + timedelta(days=5)

        input_obj = Input.date_range(
            start=get_start,
            end=get_end,
            depends_on=["base_date"],
            frequency="day",
        )

        resolved = input_obj.resolve_possible_values({"base_date": date(2024, 2, 1)})
        self.assertIsInstance(resolved, DateRangeDomain)
        values = list(resolved)
        self.assertEqual(values[0], date(2024, 2, 1))
        self.assertEqual(values[-1], date(2024, 2, 6))

    def test_input_cast_with_normalizer_and_domain(self):
        def normalize_to_month_start(value):
            return value.replace(day=1)

        input_obj = Input(
            date,
            normalizer=normalize_to_month_start,
            possible_values=DateRangeDomain(
                date(2024, 1, 1),
                date(2024, 3, 31),
                frequency="month_end",
            ),
        )
        self.assertEqual(input_obj.cast("2024-02-29"), date(2024, 2, 1))

    def test_input_bounds_with_float_values(self):
        input_obj = Input(float, min_value=0.5, max_value=10.5)
        self.assertTrue(input_obj.validate_bounds(0.5))
        self.assertTrue(input_obj.validate_bounds(5.0))
        self.assertTrue(input_obj.validate_bounds(10.5))
        self.assertFalse(input_obj.validate_bounds(0.49))
        self.assertFalse(input_obj.validate_bounds(10.51))

    def test_monthly_date_normalization_start_anchor(self):
        input_obj = Input.monthly_date(
            start=date(2024, 1, 1),
            end=date(2024, 3, 31),
            anchor="start",
        )
        self.assertEqual(input_obj.cast("2024-02-15"), date(2024, 2, 1))

    def test_date_range_monthly_with_different_anchors(self):
        input_start = Input.monthly_date(
            start=date(2024, 1, 1),
            end=date(2024, 3, 31),
            anchor="start",
        )
        values_start = list(input_start.resolve_possible_values({}))
        self.assertTrue(all(value.day == 1 for value in values_start))

        input_end = Input.monthly_date(
            start=date(2024, 1, 1),
            end=date(2024, 3, 31),
            anchor="end",
        )
        values_end = list(input_end.resolve_possible_values({}))
        self.assertIn(date(2024, 1, 31), values_end)
        self.assertIn(date(2024, 2, 29), values_end)
        self.assertIn(date(2024, 3, 31), values_end)
