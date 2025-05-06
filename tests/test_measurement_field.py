# tests.py

from django.test import TestCase
from decimal import Decimal
from django.core.exceptions import ValidationError
from general_manager.measurement.measurement import (
    Measurement,
    ureg,
    currency_units,
)
from general_manager.measurement.measurementField import MeasurementField
import pint
from django.db import models


class MeasurementFieldTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        class TestModel(models.Model):
            length = MeasurementField(base_unit="meter", null=True, blank=True)
            price = MeasurementField(base_unit="USD", null=True, blank=True)

            class Meta:
                app_label = "my_app"

        cls.TestModel = TestModel

    def setUp(self):
        self.instance = self.TestModel()

    def test_valid_measurement_creation(self):
        measurement = Measurement(5, "meter")
        self.instance.length = measurement
        self.instance.full_clean()  # Überprüft Validierung
        self.assertEqual(self.instance.length.quantity.magnitude, Decimal("5"))
        self.assertEqual(self.instance.length.quantity.units, ureg("meter"))

    def test_conversion_to_base_unit(self):
        measurement = Measurement(
            500, "centimeter"
        )  # Sollte als 5 Meter gespeichert werden
        self.instance.length = measurement
        self.instance.full_clean()
        self.assertEqual(
            self.instance.length_value, Decimal("5")  # type: ignore
        )  # In Basis-Einheit Meter
        self.assertEqual(self.instance.length_unit, "centimeter")  # type: ignore

    def test_setting_none(self):
        self.instance.length = None
        self.instance.full_clean()
        self.assertIsNone(self.instance.length)

    def test_invalid_unit_for_base_dimension(self):
        with self.assertRaises(ValidationError):
            self.instance.length = Measurement(
                1, "second"
            )  # Sekunde ist inkompatibel mit Meter
            self.instance.full_clean()

    def test_currency_unit_for_physical_field(self):
        with self.assertRaises(ValidationError):
            self.instance.length = Measurement(
                100, "USD"
            )  # USD ist keine physische Maßeinheit
            self.instance.full_clean()

    def test_valid_currency_for_currency_field(self):
        self.instance.price = Measurement(
            100, "USD"
        )  # Angenommen, price ist ein Währungsfeld
        self.instance.full_clean()
        self.assertEqual(self.instance.price.quantity.magnitude, Decimal("100"))
        self.assertEqual(self.instance.price.quantity.units, ureg("USD"))

    def test_invalid_currency_for_currency_field(self):
        with self.assertRaises(ValidationError):
            self.instance.price = Measurement(1, "meter")  # Meter ist keine Währung
            self.instance.full_clean()

    def test_invalid_value_type(self):
        with self.assertRaises(ValidationError):
            self.instance.length = "not_a_measurement"
            self.instance.full_clean()

    def test_measurement_from_string(self):
        self.instance.length = "5 meter"
        self.instance.full_clean()
        self.assertEqual(self.instance.length.quantity.magnitude, Decimal("5"))  # type: ignore
        self.assertEqual(self.instance.length.quantity.units, ureg("meter"))  # type: ignore

    def test_edge_case_zero_value(self):
        self.instance.length = Measurement(0, "meter")
        self.instance.full_clean()
        self.assertEqual(self.instance.length.quantity.magnitude, Decimal("0"))
        self.assertEqual(self.instance.length.quantity.units, ureg("meter"))

    def test_edge_case_very_large_value1(self):
        """
        The Value is bigger than the maximum total digits allowed in this field
        """
        large_value = Decimal("1e30")
        self.instance.length = Measurement(large_value, "meter")
        with self.assertRaises(ValidationError):
            self.instance.full_clean()

    def test_edge_case_very_large_value2(self):
        """
        The Value is bigger than the maximum digits before the decimal point allowed in this field
        """
        large_value = Decimal("1e25")  # Sehr großer Wert
        self.instance.length = Measurement(large_value, "meter")
        with self.assertRaises(ValidationError):
            self.instance.full_clean()

    def test_invalid_dimensionality(self):
        with self.assertRaises(ValidationError):
            self.instance.length = Measurement(
                1, "liter"
            )  # Liter passt nicht zur Basis Meter
            self.instance.full_clean()
