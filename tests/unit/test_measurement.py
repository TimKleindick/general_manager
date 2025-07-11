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
        """
        Test addition of `Measurement` instances with different units of the same physical dimension.
        
        Verifies that addition correctly handles unit conversion and is commutative. Also checks that adding zero returns the original measurement and that adding a plain number raises a `TypeError`.
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
        Tests arithmetic operations between randomly generated Measurement instances with various units.
        
        Randomly generates pairs of Measurement objects using both physical and currency units, performing addition and subtraction. Verifies that operations succeed when units match, and that appropriate exceptions are raised for incompatible units or when mixing currency and physical units.
        """
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

    def test_pickleable(self):
        """
        Tests that a Measurement instance can be pickled and unpickled, preserving its value and units.
        """
        import pickle

        m = Measurement(10, "meter")
        m_pickled = pickle.dumps(m)
        m_unpickled = pickle.loads(m_pickled)
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
        Test inequality comparisons between Measurement instances.
        
        Ensures that measurements with the same value and unit are considered equal, while those with different values or incompatible units are not. Also verifies that comparing a Measurement to an incompatible type raises the appropriate exception.
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
        
        Verifies correct behavior of equality and ordering comparisons between Measurement objects with matching and differing values and units. Ensures that comparisons with incompatible types or units raise the appropriate exceptions.
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
        Test the handling of percentage units in Measurement instances.
        
        Verifies correct initialization, string representation, arithmetic operations (addition and subtraction), and conversion between percentage and unitless values.
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
        Tests handling of dimensionless values in Measurement instances.

        Verifies that dimensionless values are correctly initialized, converted, and compared, ensuring they behave as expected in arithmetic operations and comparisons.
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
