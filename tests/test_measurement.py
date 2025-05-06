from django.test import TestCase
from general_manager.measurement.measurement import Measurement, ureg
from decimal import Decimal
import random


class MeasurementTestCase(TestCase):

    def test_initialization_with_physical_units(self):
        m = Measurement(5, "meter")
        self.assertEqual(str(m), "5 meter")

    def test_initialization_with_currency(self):
        m = Measurement(100, "USD")
        self.assertEqual(str(m), "100 USD")

    def test_invalid_value_type(self):
        with self.assertRaises(TypeError):
            Measurement("invalid", "meter")

    def test_currency_conversion(self):
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
        m1 = Measurement(1, "kilometer")  # 1000 meter
        m2 = Measurement(500, "meter")
        result = m1 + m2
        self.assertEqual(str(result), "1.5 kilometer")

    def test_subtraction_different_units_same_dimension(self):
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
        units = ["meter", "second", "kilogram", "liter", "EUR", "USD"]
        for _ in range(100):
            random_value_1 = Decimal(random.uniform(1, 1000))
            random_value_2 = Decimal(random.uniform(1, 1000))

            random_unit_1 = random.choice(units)
            random_unit_2 = random.choice(units)

            measurement_1 = Measurement(random_value_1, random_unit_1)
            measurement_2 = Measurement(random_value_2, random_unit_2)

            if random_unit_1 == random_unit_2:
                result_add = measurement_1 + measurement_2
                result_sub = measurement_1 - measurement_2
                self.assertEqual(result_add.quantity.units, ureg(random_unit_1))
                self.assertEqual(result_sub.quantity.units, ureg(random_unit_1))
            else:
                if (
                    measurement_1.is_currency()
                    and not measurement_2.is_currency()
                    or not measurement_1.is_currency()
                    and measurement_2.is_currency()
                ):
                    with self.assertRaises(TypeError):
                        result_add = measurement_1 + measurement_2

                    with self.assertRaises(TypeError):
                        result_sub = measurement_1 - measurement_2
                else:
                    with self.assertRaises(ValueError):
                        result_add = measurement_1 + measurement_2

                    with self.assertRaises(ValueError):
                        result_sub = measurement_1 - measurement_2
