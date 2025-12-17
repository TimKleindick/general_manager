# tests.py

from django.test import TestCase
from django.test import TransactionTestCase
from django.test.utils import isolate_apps
from decimal import Decimal
from django.core.exceptions import ValidationError
from general_manager.measurement.measurement import (
    Measurement,
    ureg,
)
from general_manager.measurement.measurement_field import MeasurementField
from django.db import connection, models


class MeasurementFieldTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        """
        Create and attach a dynamic TestModel used by the test suite.

        Defines a Django model named TestModel with two MeasurementField fields:
        - length: base_unit "meter", nullable and blankable.
        - price: base_unit "USD", nullable and blankable.
        Sets the model's Meta.app_label to "my_app" and assigns the class to cls.TestModel for use in tests.
        """

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
        self.instance.full_clean()  # Validate field values
        self.assertEqual(self.instance.length.quantity.magnitude, Decimal("5"))
        self.assertEqual(self.instance.length.quantity.units, ureg("meter"))

    def test_conversion_to_base_unit(self):
        measurement = Measurement(500, "centimeter")  # Should be stored as 5 meters
        self.instance.length = measurement
        self.instance.full_clean()
        self.assertEqual(
            self.instance.length_value,
            Decimal("5"),  # type: ignore
        )  # In base unit (meters)
        self.assertEqual(self.instance.length_unit, "centimeter")  # type: ignore

    def test_setting_none(self):
        self.instance.length = None
        self.instance.full_clean()
        self.assertIsNone(self.instance.length)

    def test_invalid_unit_for_base_dimension(self):
        with self.assertRaises(ValidationError):
            self.instance.length = Measurement(
                1, "second"
            )  # Seconds are incompatible with meters
            self.instance.full_clean()

    def test_currency_unit_for_physical_field(self):
        with self.assertRaises(ValidationError):
            self.instance.length = Measurement(100, "USD")  # USD is not a physical unit
            self.instance.full_clean()

    def test_valid_currency_for_currency_field(self):
        self.instance.price = Measurement(
            100, "USD"
        )  # The price field expects a currency unit
        self.instance.full_clean()
        self.assertEqual(self.instance.price.quantity.magnitude, Decimal("100"))
        self.assertEqual(self.instance.price.quantity.units, ureg("USD"))

    def test_invalid_currency_for_currency_field(self):
        with self.assertRaises(ValidationError):
            self.instance.price = Measurement(1, "meter")  # Meter is not a currency
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
        large_value = Decimal("1e25")  # Extremely large value
        self.instance.length = Measurement(large_value, "meter")
        with self.assertRaises(ValidationError):
            self.instance.full_clean()

    def test_invalid_dimensionality(self):
        with self.assertRaises(ValidationError):
            self.instance.length = Measurement(
                1, "liter"
            )  # Liters are incompatible with the meter dimension
            self.instance.full_clean()

    def test_deconstruct_preserves_base_unit_and_options(self):
        """
        Ensure deconstruct serializes base_unit and options so the field can be reconstructed.
        """
        field = MeasurementField(base_unit="kg", null=True, blank=True, editable=False)
        _name, _path, args, kwargs = field.deconstruct()

        self.assertIsInstance(args, list)
        self.assertEqual(kwargs["base_unit"], "kg")
        self.assertTrue(kwargs["null"])
        self.assertTrue(kwargs["blank"])
        self.assertFalse(kwargs["editable"])

        rebuilt = MeasurementField(*args, **kwargs)
        self.assertEqual(rebuilt.base_unit, "kg")
        self.assertTrue(rebuilt.null)
        self.assertTrue(rebuilt.blank)
        self.assertFalse(rebuilt.editable)


class MeasurementFieldConstraintTests(TransactionTestCase):
    @isolate_apps("tests")
    def test_unique_constraint_targets_value_column(self):
        class Container(models.Model):
            name = models.CharField(max_length=20)

            class Meta:
                app_label = "tests"

        constraint = models.UniqueConstraint(
            fields=["container", "volume"],
            name="uniq_container_volume",
        )

        class Size(models.Model):
            container = models.ForeignKey(Container, on_delete=models.CASCADE)
            volume = MeasurementField(base_unit="liter")

            class Meta:
                app_label = "tests"
                constraints = (constraint,)

        constraint = Size._meta.constraints[0]

        try:
            with connection.schema_editor() as editor:
                editor.create_model(Container)
                editor.create_model(Size)
                editor.add_constraint(Size, constraint)

            with connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(
                    cursor, Size._meta.db_table
                )
        finally:
            if Size._meta.db_table in connection.introspection.table_names():
                with connection.schema_editor() as editor:
                    editor.delete_model(Size)
            if Container._meta.db_table in connection.introspection.table_names():
                with connection.schema_editor() as editor:
                    editor.delete_model(Container)

        self.assertIn("uniq_container_volume", constraints)
        self.assertEqual(
            constraints["uniq_container_volume"]["columns"],
            ["container_id", "volume_value"],
        )

    @isolate_apps("tests")
    def test_unique_kwarg_enforces_uniqueness_on_value_column(self):
        class VolumeHolder(models.Model):
            volume = MeasurementField(base_unit="liter", unique=True)

            class Meta:
                app_label = "tests"

        try:
            with connection.schema_editor() as editor:
                editor.create_model(VolumeHolder)

            with connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(
                    cursor, VolumeHolder._meta.db_table
                )
        finally:
            if VolumeHolder._meta.db_table in connection.introspection.table_names():
                with connection.schema_editor() as editor:
                    editor.delete_model(VolumeHolder)

        unique_columns = [
            info["columns"] for info in constraints.values() if info["unique"]
        ]
        self.assertIn(["volume_value"], unique_columns)
