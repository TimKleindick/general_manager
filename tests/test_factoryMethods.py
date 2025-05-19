from django.test import TestCase
from general_manager.factory.factoryMethods import (
    LazyMeasurement,
    LazyDeltaDate,
    LazyProjectName,
)
from general_manager.measurement.measurement import Measurement
from datetime import timedelta, date


class TestFactoryMethods(TestCase):
    def test_LazyMeasurement(self):
        min_value = 10.0
        max_value = 30.5
        unit = "kilogram"
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                measurement = LazyMeasurement(min_value, max_value, unit).evaluate(
                    obj, 1, None
                )
                self.assertIsInstance(measurement, Measurement)
                self.assertTrue(min_value <= float(measurement.magnitude) <= max_value)
                self.assertEqual(measurement.unit, unit)

    def test_LazyDeltaDate(self):
        avg_delta_days = 5  # -> 2.5 to 7.5 days
        base_attribute = "start_date"
        obj = type("TestObject", (object,), {base_attribute: date(2023, 1, 1)})()
        for i in range(100):
            with self.subTest(run=i):
                delta_date = LazyDeltaDate(avg_delta_days, base_attribute).evaluate(
                    obj, 1, None
                )
                self.assertIsInstance(delta_date, date)
                self.assertTrue(
                    date(2023, 1, 1) <= delta_date <= date(2023, 1, 8),
                    f"Run {i}: {delta_date} is not in the expected range.",
                )

    def test_LazyProjectName(self):
        obj = type("TestObject", (object,), {})()
        for i in range(100):
            with self.subTest(run=i):
                project_name = LazyProjectName().evaluate(obj, 1, None)
                self.assertIsInstance(project_name, str)
