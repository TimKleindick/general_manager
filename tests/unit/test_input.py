from django.test import TestCase
from decimal import Decimal
from datetime import timedelta
import functools
import gc
import inspect
import weakref
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from general_manager.manager import input as input_module
from general_manager.cache.run_context import CalculationRunContext
from general_manager.manager.input import (
    DateRangeDomain,
    Input,
    NumericRangeDomain,
    _invoke_callable,
)
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

    def test_callable_possible_values_are_not_cached_outside_run_context(self):
        calls = 0

        class Owner:
            pass

        def possible_values():
            nonlocal calls
            calls += 1
            return [calls]

        input_obj = Input(int, possible_values=possible_values)

        first = input_obj.resolve_possible_values(
            {},
            cache_context=(Owner, "number"),
        )
        second = input_obj.resolve_possible_values(
            {},
            cache_context=(Owner, "number"),
        )

        self.assertEqual(first, [1])
        self.assertEqual(second, [2])
        self.assertEqual(calls, 2)

    def test_callable_possible_values_are_cached_inside_run_context(self):
        calls = 0

        class Owner:
            pass

        def possible_values():
            nonlocal calls
            calls += 1
            return [calls]

        input_obj = Input(int, possible_values=possible_values)

        with CalculationRunContext():
            first = input_obj.resolve_possible_values(
                {},
                cache_context=(Owner, "number"),
            )
            second = input_obj.resolve_possible_values(
                {},
                cache_context=(Owner, "number"),
            )

        self.assertEqual(first, [1])
        self.assertEqual(second, [1])
        self.assertEqual(calls, 1)

    def test_callable_possible_values_cache_key_uses_declared_dependencies_only(self):
        calls: list[dict[str, list[int]]] = []

        class Owner:
            pass

        def possible_values(filters):
            calls.append(filters)
            return [sum(filters["ids"])]

        input_obj = Input(
            int,
            possible_values=possible_values,
            depends_on=["filters"],
        )

        with CalculationRunContext():
            first = input_obj.resolve_possible_values(
                {"filters": {"ids": [1, 2]}, "unrelated": "a"},
                cache_context=(Owner, "total"),
            )
            second = input_obj.resolve_possible_values(
                {"filters": {"ids": [1, 2]}, "unrelated": "b"},
                cache_context=(Owner, "total"),
            )
            third = input_obj.resolve_possible_values(
                {"filters": {"ids": [3]}, "unrelated": "b"},
                cache_context=(Owner, "total"),
            )

        self.assertEqual(first, [3])
        self.assertEqual(second, [3])
        self.assertEqual(third, [3])
        self.assertEqual(calls, [{"ids": [1, 2]}, {"ids": [3]}])

    def test_callable_possible_values_materializes_one_shot_iterators_before_caching(
        self,
    ):
        calls = 0

        class Owner:
            pass

        def possible_values():
            nonlocal calls
            calls += 1
            return (value for value in [1, 2])

        input_obj = Input(int, possible_values=possible_values)

        with CalculationRunContext():
            first = input_obj.resolve_possible_values(
                {},
                cache_context=(Owner, "number"),
            )
            second = input_obj.resolve_possible_values(
                {},
                cache_context=(Owner, "number"),
            )

            self.assertEqual(list(first), [1, 2])
            self.assertEqual(list(second), [1, 2])

        self.assertEqual(calls, 1)

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

    def test_date_range_helper_honors_explicit_empty_dependencies(self):
        input_obj = Input.date_range(
            start=lambda base=date(2024, 1, 1): base,
            end=lambda limit=date(2024, 1, 31): limit,
            depends_on=[],
        )
        self.assertEqual(input_obj.depends_on, [])
        self.assertIsInstance(input_obj.possible_values, DateRangeDomain)

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

    def test_numeric_range_domain_contains_float_with_rounding_error(self):
        domain = NumericRangeDomain(0.0, 0.3, step=0.1)
        self.assertTrue(domain.contains(0.3))

    def test_numeric_range_domain_iterates_inclusive_float_endpoint(self):
        domain = NumericRangeDomain(0.0, 0.3, step=0.1)
        values = list(domain)
        self.assertEqual(len(values), 4)
        self.assertAlmostEqual(values[-1], 0.3)

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

    def test_date_range_domain_contains_accepts_datetime(self):
        domain = DateRangeDomain(
            date(2024, 1, 1),
            date(2024, 1, 31),
            frequency="day",
        )
        self.assertTrue(domain.contains(datetime(2024, 1, 31, 23, 59, 59)))

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

    def test_input_normalize_skips_callable_possible_values_without_normalizer(self):
        calls = 0

        def possible_values():
            nonlocal calls
            calls += 1
            return [1, 2, 3]

        input_obj = Input(int, possible_values=possible_values)

        self.assertEqual(input_obj.cast("2"), 2)
        self.assertEqual(calls, 0)

    def test_input_normalize_resolves_possible_values_for_custom_normalizer(self):
        calls = 0

        def possible_values():
            nonlocal calls
            calls += 1
            return [1, 2, 3]

        def normalize_with_domain(value, *, domain):
            return domain[-1] if value not in domain else value

        input_obj = Input(
            int,
            possible_values=possible_values,
            normalizer=normalize_with_domain,
        )

        self.assertEqual(input_obj.cast("4"), 3)
        self.assertEqual(calls, 1)

    def test_input_normalize_uses_static_domain_without_resolving_callable_values(self):
        input_obj = Input(
            date,
            possible_values=DateRangeDomain(
                date(2024, 1, 1),
                date(2024, 3, 31),
                frequency="month_end",
            ),
        )

        self.assertEqual(input_obj.cast("2024-02-15"), date(2024, 2, 29))

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

    def test_invoke_callable_does_not_duplicate_variadics(self):
        def capture(*args, **kwargs):
            return args, kwargs

        result_args, result_kwargs = _invoke_callable(capture, 1, 2, a=3, b=4)
        self.assertEqual(result_args, (1, 2))
        self.assertEqual(result_kwargs, {"a": 3, "b": 4})

    def test_invoke_callable_does_not_duplicate_named_parameter(self):
        def capture(a, **kwargs):
            return a, kwargs

        result_arg, result_kwargs = _invoke_callable(capture, 1, a=1, b=2)
        self.assertEqual(result_arg, 1)
        self.assertEqual(result_kwargs, {"b": 2})

    def test_invoke_callable_routes_every_parameter_kind_and_defaults(self):
        def capture(
            positional_only,
            positional_or_keyword=2,
            /,
            *values,
            keyword_only,
            optional=7,
            **keywords,
        ):
            return (
                positional_only,
                positional_or_keyword,
                values,
                keyword_only,
                optional,
                keywords,
            )

        self.assertEqual(
            _invoke_callable(
                capture,
                1,
                3,
                4,
                5,
                keyword_only=6,
                optional=8,
                extra=9,
            ),
            (1, 3, (4, 5), 6, 8, {"extra": 9}),
        )
        self.assertEqual(
            _invoke_callable(capture, 1, keyword_only=6),
            (1, 2, (), 6, 7, {}),
        )

    def test_invoke_callable_preserves_missing_argument_and_exception_behavior(self):
        def requires_value(value):
            return value

        with self.assertRaises(TypeError):
            _invoke_callable(requires_value)

        def raises(value):
            raise ValueError(value)

        with self.assertRaisesRegex(ValueError, "boom"):
            _invoke_callable(raises, "boom")

    def test_invoke_callable_does_not_cache_mutable_wrapped_callbacks(self):
        def first_target(value):
            return value

        target = first_target

        def wrapper(*args, **kwargs):
            return target(*args, **kwargs)

        wrapper.__wrapped__ = target
        self.assertEqual(_invoke_callable(wrapper, 1), 1)

        def second_target(value, extra):
            return value + extra

        target = second_target
        wrapper.__wrapped__ = target
        self.assertEqual(_invoke_callable(wrapper, 1, 2), 3)

    def test_invoke_callable_descriptor_wrapped_metadata_is_uncached(self):
        def first_target(value):
            return value

        def second_target(value, extra):
            return value + extra

        class Callback:
            target = staticmethod(first_target)

            @property
            def __wrapped__(self):
                return self.target

            def __call__(self, *args, **kwargs):
                return self.target(*args, **kwargs)

        callback = Callback()
        self.assertEqual(_invoke_callable(callback, 1), 1)
        callback.target = second_target
        self.assertEqual(_invoke_callable(callback, 1, 2), 3)

    def test_invoke_callable_reuses_compiled_signature_plan(self):
        def capture(value, *, suffix="!"):
            return f"{value}{suffix}"

        with patch(
            "general_manager.manager.input.inspect.signature",
            wraps=__import__("inspect").signature,
        ) as signature:
            self.assertEqual(_invoke_callable(capture, "ok"), "ok!")
            self.assertEqual(_invoke_callable(capture, "ready", suffix="?"), "ready?")

        self.assertEqual(signature.call_count, 1)

    def test_invoke_callable_caches_callable_instances_without_hashing(self):
        class Callback:
            __hash__ = None

            def __eq__(self, other):
                raise AssertionError

            def __call__(self, value):
                return value + 1

        callback = Callback()
        original_signature = inspect.signature
        with patch(
            "general_manager.manager.input.inspect.signature",
            wraps=original_signature,
        ) as signature:
            self.assertEqual(_invoke_callable(callback, 1), 2)
            self.assertEqual(_invoke_callable(callback, 2), 3)

        self.assertEqual(signature.call_count, 1)

    def test_invoke_callable_caches_partial_and_bound_method_callbacks(self):
        def add(left, right):
            return left + right

        partial = functools.partial(add, 2)

        class Callback:
            def add(self, left, right):
                return left + right

        bound_method = Callback().add
        original_signature = inspect.signature
        with patch(
            "general_manager.manager.input.inspect.signature",
            wraps=original_signature,
        ) as signature:
            self.assertEqual(_invoke_callable(partial, 3), 5)
            self.assertEqual(_invoke_callable(partial, 4), 6)
            self.assertEqual(_invoke_callable(bound_method, 3, right=4), 7)
            self.assertEqual(_invoke_callable(bound_method, 4, right=5), 9)

        self.assertEqual(signature.call_count, 2)

    def test_invoke_callable_explicit_signature_is_always_reflected(self):
        def capture(value):
            return value

        capture.__signature__ = inspect.signature(capture)
        original_signature = inspect.signature
        with patch(
            "general_manager.manager.input.inspect.signature",
            wraps=original_signature,
        ) as signature:
            self.assertEqual(_invoke_callable(capture, "first"), "first")
            self.assertEqual(_invoke_callable(capture, "second"), "second")

        self.assertEqual(signature.call_count, 2)

    def test_invoke_callable_decorator_with_explicit_wrapped_signature_is_uncached(
        self,
    ):
        def original(value):
            return value

        original.__signature__ = inspect.signature(original)

        @functools.wraps(original)
        def decorated(value):
            return original(value)

        original_signature = inspect.signature
        with patch(
            "general_manager.manager.input.inspect.signature",
            wraps=original_signature,
        ) as signature:
            self.assertEqual(_invoke_callable(decorated, "first"), "first")
            self.assertEqual(_invoke_callable(decorated, "second"), "second")

        self.assertEqual(signature.call_count, 2)

    def test_invoke_callable_bound_method_underlying_signature_is_uncached(self):
        class Callback:
            def capture(self, value):
                return value

        Callback.capture.__signature__ = inspect.signature(Callback.capture)
        callback = Callback().capture
        original_signature = inspect.signature
        with patch(
            "general_manager.manager.input.inspect.signature",
            wraps=original_signature,
        ) as signature:
            self.assertEqual(_invoke_callable(callback, "first"), "first")
            self.assertEqual(_invoke_callable(callback, "second"), "second")

        self.assertEqual(signature.call_count, 2)

    def test_invoke_callable_partial_underlying_signature_is_uncached(self):
        def capture(prefix, value):
            return f"{prefix}{value}"

        capture.__signature__ = inspect.signature(capture)
        callback = functools.partial(capture, "item:")
        original_signature = inspect.signature
        with patch(
            "general_manager.manager.input.inspect.signature",
            wraps=original_signature,
        ) as signature:
            self.assertEqual(_invoke_callable(callback, 1), "item:1")
            self.assertEqual(_invoke_callable(callback, 2), "item:2")

        self.assertEqual(signature.call_count, 2)

    def test_invoke_callable_custom_signature_metadata_is_uncached(self):
        def callback(value):
            return value

        class CustomSignature(inspect.Signature):
            pass

        standard_signature = inspect.signature(callback)
        custom_signature = CustomSignature(
            parameters=tuple(standard_signature.parameters.values())
        )
        with patch(
            "general_manager.manager.input.inspect.signature",
            return_value=custom_signature,
        ) as signature:
            self.assertEqual(_invoke_callable(callback, "first"), "first")
            self.assertEqual(_invoke_callable(callback, "second"), "second")

        self.assertEqual(signature.call_count, 2)

    def test_invoke_callable_non_weak_callback_is_not_cached(self):
        class Callback:
            __slots__ = ()

            def __call__(self, value):
                return value * 2

        callback = Callback()
        original_signature = inspect.signature
        with patch(
            "general_manager.manager.input.inspect.signature",
            wraps=original_signature,
        ) as signature:
            self.assertEqual(_invoke_callable(callback, 2), 4)
            self.assertEqual(_invoke_callable(callback, 3), 6)

        self.assertEqual(signature.call_count, 2)

    def test_invoke_callable_signature_failure_is_not_cached(self):
        def callback(value):
            return value

        with patch(
            "general_manager.manager.input.inspect.signature",
            side_effect=RuntimeError("signature unavailable"),
        ) as signature:
            with self.assertRaisesRegex(RuntimeError, "signature unavailable"):
                _invoke_callable(callback, 1)
            with self.assertRaisesRegex(RuntimeError, "signature unavailable"):
                _invoke_callable(callback, 2)

        self.assertEqual(signature.call_count, 2)

    def test_invoke_callable_concurrent_cache_access_keeps_results_correct(self):
        def callback(value):
            return value * 2

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(lambda value: _invoke_callable(callback, value), range(20))
            )

        self.assertEqual(results, [value * 2 for value in range(20)])

    def test_dead_callable_plan_entry_is_removed_after_collection(self):
        def callback(value):
            return value

        callback_id = id(callback)
        _invoke_callable(callback, 1)
        self.assertIn(callback_id, input_module._callable_invocation_plan_cache)
        del callback
        gc.collect()
        self.assertNotIn(callback_id, input_module._callable_invocation_plan_cache)

    def test_dead_callable_cleanup_does_not_remove_replacement_entry(self):
        class Callback:
            def __call__(self, value):
                return value

        first = Callback()
        _invoke_callable(first, 1)
        first_id = id(first)
        first_entry = input_module._callable_invocation_plan_cache[first_id]

        second = Callback()
        second_reference = weakref.ref(second)
        input_module._callable_invocation_plan_cache[first_id] = (
            second_reference,
            first_entry[1],
        )
        del first
        gc.collect()

        self.assertIs(
            input_module._callable_invocation_plan_cache[first_id][0],
            second_reference,
        )
        del second
        gc.collect()

    def test_invocation_plan_guards_cover_dynamic_metadata_and_stale_entries(self):
        def callback(value):
            return value

        with patch.object(
            input_module.inspect,
            "getattr_static",
            side_effect=RuntimeError("metadata unavailable"),
        ):
            self.assertTrue(
                input_module._callable_invocation_requires_uncached_plan(callback)
            )

        class NonWeakCallback:
            __slots__ = ()

            def __call__(self, value):
                return value

        non_weak = NonWeakCallback()
        self.assertFalse(
            input_module._callable_invocation_requires_uncached_plan(non_weak)
        )
        self.assertEqual(input_module._invoke_callable(non_weak, 3), 3)

        fake_signature = SimpleNamespace(parameters=MappingProxyType({}))
        with patch.object(
            input_module.inspect,
            "signature",
            return_value=fake_signature,
        ):
            compiled = input_module._compile_callable_invocation_plan(callback)
        self.assertIsNone(compiled.plan)
        self.assertEqual(compiled.parameters, ())

        def first(value):
            return value

        def second(value):
            return value + 1

        stale_plan = input_module._CallableInvocationPlan(())
        input_module._callable_invocation_plan_cache[id(first)] = (
            weakref.ref(second),
            stale_plan,
        )
        plan = input_module._get_callable_invocation_plan(first)
        self.assertIsInstance(plan, input_module._CallableInvocationPlan)
        self.assertIsNot(plan, stale_plan)
        input_module._callable_invocation_plan_cache.pop(id(first), None)

        signature = inspect.signature(callback)
        with patch.object(input_module, "MappingProxyType", dict):
            mutable_metadata = input_module._compile_callable_invocation_plan(callback)
        self.assertIsNone(mutable_metadata.plan)

        signature._parameters = MappingProxyType({"value": object()})
        with patch.object(input_module.inspect, "signature", return_value=signature):
            malformed_parameter = input_module._compile_callable_invocation_plan(
                callback
            )
        self.assertIsNone(malformed_parameter.plan)

        class DeadCallback:
            def __call__(self, value):
                return value

        cache = input_module._callable_invocation_plan_cache
        saved_cache = dict(cache)
        cache.clear()
        try:
            dead = DeadCallback()
            dead_id = id(dead)
            dead_reference = weakref.ref(dead)
            del dead
            gc.collect()
            cache[dead_id] = (dead_reference, stale_plan)
            input_module._get_callable_invocation_plan(callback)
            self.assertNotIn(dead_id, cache)

            winning_plan = input_module._CallableInvocationPlan(())
            cache.pop(id(callback), None)
            original_compile = input_module._compile_callable_invocation_plan

            def compile_and_publish(func):
                compiled = original_compile(func)
                cache[id(func)] = (weakref.ref(func), winning_plan)
                return compiled

            with patch.object(
                input_module,
                "_compile_callable_invocation_plan",
                side_effect=compile_and_publish,
            ):
                self.assertIs(
                    input_module._get_callable_invocation_plan(callback),
                    winning_plan,
                )
        finally:
            cache.clear()
            cache.update(saved_cache)

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

    def test_input_from_manager_query_callable_mapping_result_filters(self):
        filtered_result = object()

        class MockManager:
            @classmethod
            def filter(cls, **kwargs):
                return filtered_result

        def query():
            return MappingProxyType({"status": "active"})

        with patch("general_manager.manager.input.issubclass", return_value=True):
            input_obj = Input.from_manager_query(MockManager, query=query)

        self.assertIs(input_obj.resolve_possible_values({}), filtered_result)

    def test_input_from_manager_query_honors_explicit_empty_dependencies(self):
        class MockManager:
            @classmethod
            def filter(cls, **kwargs):
                return kwargs

        def query(base_id=7):
            return {"id": base_id}

        with patch("general_manager.manager.input.issubclass", return_value=True):
            input_obj = Input.from_manager_query(
                MockManager,
                query=query,
                depends_on=[],
            )

        self.assertEqual(input_obj.depends_on, [])
        self.assertEqual(input_obj.resolve_possible_values({}), {"id": 7})

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
