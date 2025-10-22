from django.test import TestCase
from general_manager.measurement.measurement import Measurement, ureg
from decimal import Decimal
from random import Random
import pickle
from typing import Any


def _trusted_pickle_loads(data: bytes) -> Any:
    """Deserialize pickle data that was created within this test module."""

    return pickle.loads(data)  # noqa: S301 - data originates from the current test


class MeasurementTestCase(TestCase):
    def test_initialization_with_physical_units(self):
        m = Measurement(5, "meter")
        self.assertEqual(str(m), "5 meter")

    def test_initialization_with_currency(self):
        """
        Tests initialization of a Measurement instance with a currency unit and verifies its string representation.
        """
        m = Measurement(100, "USD")
        self.assertEqual(str(m), "100 USD")

    def test_invalid_value_type(self):
        """
        Verifies that initializing a Measurement with a non-numeric value raises a ValueError.
        """
        with self.assertRaises(ValueError):
            Measurement("invalid", "meter")

    def test_currency_conversion(self):
        """
        Tests conversion of a currency `Measurement` from EUR to USD using a specified exchange rate and verifies the converted value and unit.
        """
        m = Measurement(100, "EUR")
        converted = m.to("USD", exchange_rate=1.2)
        self.assertEqual(str(converted), "120 USD")

    def test_invalid_currency_conversion(self):
        m = Measurement(100, "EUR")
        with self.assertRaises(ValueError):
            m.to("USD")

    def test_physical_unit_conversion(self):
        m = Measurement(1, "kilometer")
        converted = m.to("meter")
        self.assertEqual(str(converted), "1000 meter")

    def test_addition_same_units(self):
        m1 = Measurement(1, "meter")
        m2 = Measurement(2, "meter")
        result = m1 + m2
        self.assertEqual(str(result), "3 meter")

    def test_addition_different_units_same_dimension(self):
        """
        Test addition of `Measurement` instances with different units of the same physical dimension.

        Verifies that addition correctly converts units and is commutative. Also checks that adding zero returns the original measurement and that adding a plain number raises a `TypeError`.
        """
        m1 = Measurement(1, "kilometer")  # 1000 meter
        m2 = Measurement(500, "meter")
        result = m1 + m2
        self.assertEqual(str(result), "1.5 kilometer")

        result = m2 + m1  # Commutative property
        self.assertEqual(str(result), "1500 meter")

        result = 0 + m1
        self.assertEqual(str(result), "1 kilometer")

        with self.assertRaises(TypeError):
            _ = 10 + m1  # Adding a number to a Measurement should raise an error

    def test_subtraction_different_units_same_dimension(self):
        """
        Tests subtraction of two Measurement instances with different units but the same physical dimension, verifying correct unit conversion and result.
        """
        m1 = Measurement(2, "kilometer")  # 2000 meter
        m2 = Measurement(500, "meter")
        result = m1 - m2
        self.assertEqual(str(result), "1.5 kilometer")

    def test_addition_different_units_different_dimensions(self):
        m1 = Measurement(1, "meter")
        m2 = Measurement(1, "second")
        with self.assertRaises(ValueError):
            _ = m1 + m2

    def test_subtraction_different_units_different_dimensions(self):
        m1 = Measurement(2, "meter")
        m2 = Measurement(1, "second")
        with self.assertRaises(ValueError):
            _ = m1 - m2

    def test_multiplication_same_units(self):
        m1 = Measurement(2, "meter")
        result = m1 * 3
        self.assertEqual(str(result), "6 meter")

    def test_multiplication_different_units(self):
        m1 = Measurement(2, "meter")
        m2 = Measurement(3, "second")
        result = m1 * m2
        self.assertEqual(str(result), "6 meter * second")

    def test_division_same_units(self):
        m1 = Measurement(10, "meter")
        result = m1 / 2
        self.assertEqual(str(result), "5 meter")

    def test_division_different_units_same_dimension(self):
        m1 = Measurement(1, "kilometer")  # 1000 meter
        m2 = Measurement(500, "meter")
        result = m1 / m2
        self.assertEqual(str(result), "2")

    def test_division_different_units_different_dimensions(self):
        m1 = Measurement(10, "meter")
        m2 = Measurement(5, "second")
        result = m1 / m2
        self.assertEqual(str(result), "2 meter / second")

    def test_addition_same_currency(self):
        m1 = Measurement(100, "EUR")
        m2 = Measurement(50, "EUR")
        result = m1 + m2
        self.assertEqual(str(result), "150 EUR")

    def test_subtraction_same_units(self):
        m1 = Measurement(2, "meter")
        m2 = Measurement(1, "meter")
        result = m1 - m2
        self.assertEqual(str(result), "1 meter")

    def test_random_measurements(self):
        """
        Performs randomized tests of addition and subtraction between Measurement instances with various physical and currency units.

        Randomly generates pairs of Measurement objects using both physical and currency units, verifying that arithmetic operations succeed when units match and raise appropriate exceptions when units are incompatible or when mixing currency and physical units.
        """
        units = ["meter", "second", "kilogram", "liter", "EUR", "USD"]
        rng = Random(42)  # noqa: S311 - use a fixed seed for reproducibility
        for _ in range(100):
            random_value_1 = Decimal(rng.uniform(1, 1000))
            random_value_2 = Decimal(rng.uniform(1, 1000))

            random_unit_1 = rng.choice(units)
            random_unit_2 = rng.choice(units)

            measurement_1 = Measurement(random_value_1, random_unit_1)
            measurement_2 = Measurement(random_value_2, random_unit_2)

            if random_unit_1 == random_unit_2:
                result_add = measurement_1 + measurement_2
                result_sub = measurement_1 - measurement_2
                self.assertEqual(result_add.quantity.units, ureg(random_unit_1))
                self.assertEqual(result_sub.quantity.units, ureg(random_unit_1))
            else:
                if (
                    measurement_1.is_currency() and not measurement_2.is_currency()
                ) or (not measurement_1.is_currency() and measurement_2.is_currency()):
                    with self.assertRaises(TypeError):
                        result_add = measurement_1 + measurement_2

                    with self.assertRaises(TypeError):
                        result_sub = measurement_1 - measurement_2
                else:
                    with self.assertRaises(ValueError):
                        result_add = measurement_1 + measurement_2

                    with self.assertRaises(ValueError):
                        result_sub = measurement_1 - measurement_2

    def test_pickleable(self):
        """
        Tests that a Measurement instance can be pickled and unpickled, preserving its value and units.
        """
        m = Measurement(10, "meter")
        m_pickled = pickle.dumps(m)
        m_unpickled = _trusted_pickle_loads(m_pickled)
        self.assertEqual(str(m), str(m_unpickled))
        self.assertEqual(m.quantity.units, m_unpickled.quantity.units)
        self.assertEqual(m.quantity.magnitude, m_unpickled.quantity.magnitude)

    def test_equality(self):
        """
        Tests equality comparisons between Measurement instances, including correct handling of value and unit differences and appropriate exception raising for invalid comparisons.
        """
        m1 = Measurement(10, "meter")
        m2 = Measurement(10, "meter")
        m3 = Measurement(5, "meter")

        self.assertEqual(m1, m2)
        self.assertNotEqual(m1, m3)
        with self.assertRaises(ValueError):
            _ = m1 == "not a measurement"
        with self.assertRaises(TypeError):
            _ = m1 == 10
        with self.assertRaises(ValueError):
            _ = m1 == Measurement(10, "second")

    def test_inequality(self):
        """
        Verify inequality behavior of Measurement instances.
        Asserts that two measurements with the same value and unit are not unequal (`!=` is False), that measurements with different magnitudes are unequal, and that comparisons with incompatible types or incompatible units raise the expected exceptions: `ValueError` for non-Measurement types that are not numeric (e.g., strings) and for measurements with incompatible units, and `TypeError` for numeric types that are not Measurement instances.
        """
        m1 = Measurement(10, "meter")
        m2 = Measurement(10, "meter")
        m3 = Measurement(5, "meter")

        self.assertFalse(m1 != m2)
        self.assertTrue(m1 != m3)
        with self.assertRaises(ValueError):
            _ = m1 != "not a measurement"
        with self.assertRaises(TypeError):
            _ = m1 != 10
        with self.assertRaises(ValueError):
            _ = m1 != Measurement(10, "second")

    def test_comparison(self):
        """
        Test relational comparison operators for Measurement instances.

        Verifies correct behavior of equality and ordering comparisons between Measurement objects with identical or differing values and units. Ensures that comparisons with incompatible types or units raise the appropriate exceptions.
        """
        m1 = Measurement(10, "meter")
        m2 = Measurement(10, "meter")
        m3 = Measurement(5, "meter")

        self.assertTrue(m1 == m2)
        self.assertFalse(m1 < m2)
        self.assertFalse(m1 > m2)
        self.assertTrue(m1 >= m2)
        self.assertTrue(m1 <= m2)

        self.assertTrue(m1 > m3)
        self.assertFalse(m1 < m3)
        self.assertTrue(m1 >= m3)
        self.assertFalse(m1 <= m3)

        with self.assertRaises(ValueError):
            _ = m1 < "not a measurement"
        with self.assertRaises(TypeError):
            _ = m1 < 10
        with self.assertRaises(ValueError):
            _ = m1 < Measurement(10, "second")

    def test_hash(self):
        """
        Verify that Measurement instances with identical values and units produce the same hash, while differing values, units, or types yield different hashes.
        """
        m1 = Measurement(10, "meter")
        m2 = Measurement(10, "meter")
        m3 = Measurement(5, "meter")

        self.assertEqual(hash(m1), hash(m2))
        self.assertNotEqual(hash(m1), hash(m3))
        self.assertNotEqual(hash(m1), hash(Measurement(10, "second")))
        self.assertNotEqual(hash(m1), hash("not a measurement"))

    def test_percentage_values(self):
        """
        Tests initialization, arithmetic operations, string representation, and unit conversion for `Measurement` instances with percentage units.

        Verifies correct handling of both "%" and "percent" units, addition and subtraction of percentage values, conversion between percentage and unitless representations, and conversion from unitless to percentage.
        """
        m1 = Measurement(50, "%")
        m2 = Measurement(25, "percent")

        self.assertEqual(str(m1), "50 percent")
        self.assertEqual(str(m2), "25 percent")

        result_add = m1 + m2
        self.assertEqual(str(result_add), "75 percent")

        result_sub = m1 - m2
        self.assertEqual(str(result_sub), "25 percent")

        self.assertEqual(str(m1.to("")), "0.5")
        self.assertEqual(str(m2.to("")), "0.25")

        m3 = Measurement(100, "")
        self.assertEqual(str(m3.to("%")), "10000 percent")

    def test_dimensionless_values(self):
        """
        Test initialization, arithmetic operations, and comparisons for dimensionless Measurement instances.

        Verifies correct behavior for string representation, addition, subtraction, and equality when using dimensionless units or empty unit strings.
        """
        m1 = Measurement(1, "dimensionless")
        m2 = Measurement(2, "dimensionless")

        self.assertEqual(str(m1), "1")
        self.assertEqual(str(m2), "2")

        result_add = m1 + m2
        self.assertEqual(str(result_add), "3")

        result_sub = m1 - m2
        self.assertEqual(str(result_sub), "-1")

        m3 = Measurement(100, "")
        m4 = Measurement.from_string("100")
        self.assertEqual(str(m3), "100")
        self.assertEqual(str(m4), "100")
        self.assertEqual(m3, m4)

    def test_calculation_between_currency_and_dimensionless(self):
        """
        Tests arithmetic operations between currency and dimensionless Measurement instances.

        Verifies that addition and subtraction raise TypeError, while multiplication and division are allowed and yield correct results with appropriate units.
        """
        m1 = Measurement(100, "EUR")
        m2 = Measurement(2, "dimensionless")

        with self.assertRaises(TypeError):
            _ = m1 + m2

        with self.assertRaises(TypeError):
            _ = m1 - m2

        result_mul = m1 * m2
        self.assertEqual(str(result_mul), "200 EUR")

        result_div = m1 / m2
        self.assertEqual(str(result_div), "50 EUR")
        self.assertEqual(result_div.quantity.units, ureg("EUR / dimensionless"))

    def test_calculation_with_other_dimension_and_currency(self):
        """
        Tests arithmetic operations between a currency measurement and a measurement with a different physical dimension.

        Verifies that addition and subtraction raise a TypeError, while multiplication and division are permitted and result in combined unit expressions.
        """
        m1 = Measurement(100, "EUR")
        m2 = Measurement(2, "meter")

        with self.assertRaises(TypeError):
            _ = m1 + m2

        with self.assertRaises(TypeError):
            _ = m1 - m2

        result_mul = m1 * m2
        self.assertIn(str(result_mul), ["200 meter * EUR", "200 EUR * meter"])

        result_div = m1 / m2
        self.assertEqual(str(result_div), "50 EUR / meter")

    def test_conversion_to_dimensionless(self):
        """
        Tests conversion of a compound-unit Measurement to a dimensionless value and back.

        Verifies that multiplying a currency Measurement by a percentage yields a compound unit, and converting this result to the original currency unit produces the correct value.
        """
        m1 = Measurement(100, "EUR")
        m2 = Measurement(20, "%")

        m3 = m1 * m2
        self.assertEqual(str(m3), "2000 EUR * percent")
        self.assertEqual(str(m3.to("EUR")), "20 EUR")

    def test_conversion_to_complex_units(self):
        """
        Tests conversion of a Measurement with compound units to another compatible complex unit.

        Verifies that multiplying two Measurements with different units produces a compound unit, and that converting this result to another compatible complex unit yields the correct value and unit.
        """
        m1 = Measurement(100, "EUR")
        m2 = Measurement(2, "meter")

        m3 = m1 * m2
        self.assertIn(str(m3), ["200 meter * EUR", "200 EUR * meter"])
        self.assertEqual(str(m3.to("EUR * kilometer")), "0.2 EUR * kilometer")

    def test_invalid_measurement_initialization_error(self):
        """Test that InvalidMeasurementInitializationError is raised for invalid value types."""
        from general_manager.measurement.measurement import (
            InvalidMeasurementInitializationError,
        )

        with self.assertRaises(InvalidMeasurementInitializationError) as ctx:
            Measurement("not-a-number", "meter")
        self.assertIn("Decimal, float, int or compatible", str(ctx.exception))

    def test_invalid_dimensionless_value_error(self):
        """Test that InvalidDimensionlessValueError is raised for invalid dimensionless parsing."""
        from general_manager.measurement.measurement import (
            InvalidDimensionlessValueError,
        )

        with self.assertRaises(InvalidDimensionlessValueError) as ctx:
            Measurement.from_string("invalid_number")
        self.assertIn("Invalid value for dimensionless measurement", str(ctx.exception))

    def test_invalid_measurement_string_error(self):
        """Test that InvalidMeasurementStringError is raised for malformed string format."""
        from general_manager.measurement.measurement import (
            InvalidMeasurementStringError,
        )

        with self.assertRaises(InvalidMeasurementStringError) as ctx:
            Measurement.from_string("10 20 30 invalid format")
        self.assertIn("format 'value unit'", str(ctx.exception))

    def test_missing_exchange_rate_error(self):
        """Test that MissingExchangeRateError is raised when converting currency without rate."""
        from general_manager.measurement.measurement import MissingExchangeRateError

        m = Measurement(100, "EUR")
        with self.assertRaises(MissingExchangeRateError) as ctx:
            m.to("USD")
        self.assertIn("exchange rate", str(ctx.exception))

    def test_measurement_operand_type_error_addition(self):
        """Test that MeasurementOperandTypeError is raised for non-measurement operands in addition."""
        from general_manager.measurement.measurement import MeasurementOperandTypeError

        m = Measurement(10, "meter")
        with self.assertRaises(MeasurementOperandTypeError) as ctx:
            _ = m + 5
        self.assertIn(
            "Addition is only allowed between Measurement instances", str(ctx.exception)
        )

    def test_measurement_operand_type_error_subtraction(self):
        """Test that MeasurementOperandTypeError is raised for non-measurement operands in subtraction."""
        from general_manager.measurement.measurement import MeasurementOperandTypeError

        m = Measurement(10, "meter")
        with self.assertRaises(MeasurementOperandTypeError) as ctx:
            _ = m - 5
        self.assertIn(
            "Subtraction is only allowed between Measurement instances",
            str(ctx.exception),
        )

    def test_currency_mismatch_error_addition(self):
        """Test that CurrencyMismatchError is raised when adding different currencies."""
        from general_manager.measurement.measurement import CurrencyMismatchError

        m1 = Measurement(100, "EUR")
        m2 = Measurement(50, "USD")
        with self.assertRaises(CurrencyMismatchError) as ctx:
            _ = m1 + m2
        self.assertIn("Addition between different currencies", str(ctx.exception))

    def test_currency_mismatch_error_subtraction(self):
        """Test that CurrencyMismatchError is raised when subtracting different currencies."""
        from general_manager.measurement.measurement import CurrencyMismatchError

        m1 = Measurement(100, "EUR")
        m2 = Measurement(50, "USD")
        with self.assertRaises(CurrencyMismatchError) as ctx:
            _ = m1 - m2
        self.assertIn("Subtraction between different currencies", str(ctx.exception))

    def test_incompatible_units_error_addition(self):
        """Test that IncompatibleUnitsError is raised for incompatible physical units in addition."""
        from general_manager.measurement.measurement import IncompatibleUnitsError

        m1 = Measurement(10, "meter")
        m2 = Measurement(5, "second")
        with self.assertRaises(IncompatibleUnitsError) as ctx:
            _ = m1 + m2
        self.assertIn("Units are not compatible for addition", str(ctx.exception))

    def test_mixed_unit_operation_error_addition(self):
        """Test that MixedUnitOperationError is raised when mixing currency and physical units."""
        from general_manager.measurement.measurement import MixedUnitOperationError

        m1 = Measurement(100, "EUR")
        m2 = Measurement(10, "meter")
        with self.assertRaises(MixedUnitOperationError) as ctx:
            _ = m1 + m2
        self.assertIn("Addition between currency and physical unit", str(ctx.exception))

    def test_mixed_unit_operation_error_subtraction(self):
        """Test that MixedUnitOperationError is raised when mixing currency and physical units in subtraction."""
        from general_manager.measurement.measurement import MixedUnitOperationError

        m1 = Measurement(100, "EUR")
        m2 = Measurement(10, "meter")
        with self.assertRaises(MixedUnitOperationError) as ctx:
            _ = m1 - m2
        self.assertIn(
            "Subtraction between currency and physical unit", str(ctx.exception)
        )

    def test_currency_scalar_operation_error_multiplication(self):
        """Test that CurrencyScalarOperationError is raised when multiplying two currencies."""
        from general_manager.measurement.measurement import CurrencyScalarOperationError

        m1 = Measurement(100, "EUR")
        m2 = Measurement(50, "USD")
        with self.assertRaises(CurrencyScalarOperationError) as ctx:
            _ = m1 * m2
        self.assertIn("Multiplication between two currency amounts", str(ctx.exception))

    def test_currency_scalar_operation_error_division(self):
        """Test that CurrencyScalarOperationError is raised when dividing two different currencies."""
        from general_manager.measurement.measurement import CurrencyMismatchError

        m1 = Measurement(100, "EUR")
        m2 = Measurement(50, "USD")
        with self.assertRaises(CurrencyMismatchError) as ctx:
            _ = m1 / m2
        self.assertIn("different currencies", str(ctx.exception))

    def test_measurement_scalar_type_error_multiplication(self):
        """Test that MeasurementScalarTypeError is raised for invalid multiplication operands."""
        from general_manager.measurement.measurement import MeasurementScalarTypeError

        m = Measurement(10, "meter")
        with self.assertRaises(MeasurementScalarTypeError) as ctx:
            _ = m * "invalid"
        self.assertIn(
            "Multiplication is only allowed with Measurement or numeric",
            str(ctx.exception),
        )

    def test_measurement_scalar_type_error_division(self):
        """Test that MeasurementScalarTypeError is raised for invalid division operands."""
        from general_manager.measurement.measurement import MeasurementScalarTypeError

        m = Measurement(10, "meter")
        with self.assertRaises(MeasurementScalarTypeError) as ctx:
            _ = m / "invalid"
        self.assertIn(
            "Division is only allowed with Measurement or numeric", str(ctx.exception)
        )

    def test_unsupported_comparison_error(self):
        """Test that UnsupportedComparisonError is raised for non-measurement comparisons."""
        from general_manager.measurement.measurement import UnsupportedComparisonError

        m = Measurement(10, "meter")
        with self.assertRaises(UnsupportedComparisonError):
            _ = m < 1234

    def test_incomparable_measurement_error(self):
        """Test that IncomparableMeasurementError is raised when comparing different dimensions."""
        from general_manager.measurement.measurement import IncomparableMeasurementError

        m1 = Measurement(10, "meter")
        m2 = Measurement(5, "second")
        with self.assertRaises(IncomparableMeasurementError) as ctx:
            _ = m1 < m2
        self.assertIn(
            "Cannot compare measurements with different dimensions", str(ctx.exception)
        )

    def test_measurement_from_string_edge_cases(self):
        """Test edge cases in Measurement.from_string parsing."""
        # Single value should default to dimensionless
        m1 = Measurement.from_string("42.5")
        self.assertEqual(m1.magnitude, Decimal("42.5"))
        self.assertEqual(str(m1), "42.5")

        # Valid value with unit
        m2 = Measurement.from_string("100 meter")
        self.assertEqual(m2.magnitude, Decimal("100"))
        self.assertEqual(m2.unit, "meter")

        # Negative values
        m3 = Measurement.from_string("-5.5 kilogram")
        self.assertEqual(m3.magnitude, Decimal("-5.5"))
        self.assertEqual(m3.unit, "kilogram")

    def test_measurement_decimal_precision(self):
        """Test that Measurement maintains Decimal precision."""
        m = Measurement(Decimal("0.00000001"), "meter")
        self.assertEqual(m.magnitude, Decimal("0.00000001"))

        # Test arithmetic maintains precision
        m2 = Measurement(Decimal("0.00000001"), "meter")
        result = m + m2
        self.assertEqual(result.magnitude, Decimal("0.00000002"))

    def test_measurement_radd_rsub(self):
        """Test reverse addition and subtraction operations."""
        m = Measurement(10, "meter")

        # 0 + measurement should work
        result = 0 + m
        self.assertEqual(str(result), "10 meter")

        # 0 - measurement should work
        result = 0 - m
        self.assertEqual(str(result), "-10 meter")

    def test_measurement_rmul_rtruediv(self):
        """Test reverse multiplication and division operations."""
        m = Measurement(10, "meter")

        # scalar * measurement
        result = 2 * m
        self.assertEqual(str(result), "20 meter")

        # scalar / measurement
        result = 100 / m
        self.assertEqual(str(result), "10 1 / meter")

    def test_measurement_zero_division(self):
        """Test that dividing by zero raises appropriate error."""
        m1 = Measurement(10, "meter")
        m2 = Measurement(0, "second")

        with self.assertRaises(ZeroDivisionError):
            _ = m1 / m2

    def test_measurement_negative_operations(self):
        """Test arithmetic with negative values."""
        m1 = Measurement(-10, "meter")
        m2 = Measurement(5, "meter")

        result_add = m1 + m2
        self.assertEqual(str(result_add), "-5 meter")

        result_sub = m1 - m2
        self.assertEqual(str(result_sub), "-15 meter")

        result_mul = m1 * -2
        self.assertEqual(str(result_mul), "20 meter")
