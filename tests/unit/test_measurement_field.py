# tests.py

from django.test import TestCase
from django.test import TransactionTestCase
from django.test.utils import isolate_apps
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db.migrations.writer import MigrationWriter
from general_manager.measurement.measurement import (
    Measurement,
    ureg,
)
from general_manager.measurement.measurement_field import (
    InvalidMeasurementFieldBaseUnitError,
    MeasurementField,
    MeasurementFieldNotEditableError,
)
from django.db import connection, models
from unittest.mock import patch


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
            density = MeasurementField(base_unit="g/cm^3", null=True, blank=True)
            temperature = MeasurementField(base_unit="K", null=True, blank=True)

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

    def test_measurement_field_accepts_compound_unit_string(self):
        self.instance.density = "1 g / cm^3"
        self.instance.full_clean()

        self.assertEqual(self.instance.density_value, Decimal("1"))  # type: ignore
        self.assertEqual(ureg(self.instance.density.unit).units, ureg("g/cm^3").units)  # type: ignore

    def test_measurement_field_converts_compatible_compound_units(self):
        self.instance.density = Measurement(1000, "kg/m^3")
        self.instance.full_clean()

        self.assertEqual(self.instance.density_value, Decimal("1"))  # type: ignore
        self.assertEqual(ureg(self.instance.density_unit).units, ureg("kg/m^3").units)  # type: ignore
        self.assertAlmostEqual(self.instance.density.magnitude, Decimal("1000"))  # type: ignore
        self.assertEqual(ureg(self.instance.density.unit).units, ureg("kg/m^3").units)  # type: ignore

    @isolate_apps("tests")
    def test_compound_unit_round_trip(self):
        class CompoundUnitModel(models.Model):
            density = MeasurementField(base_unit="kg/m^3", null=True, blank=True)

            class Meta:
                app_label = "tests"

        instance = CompoundUnitModel()
        instance.density = "1 g / cm^3"
        instance.full_clean()

        self.assertEqual(instance.density_value, Decimal("1000"))  # type: ignore
        self.assertEqual(ureg(instance.density_unit).units, ureg("g/cm^3").units)  # type: ignore
        self.assertEqual(ureg(instance.density.unit).units, ureg("g/cm^3").units)  # type: ignore

    @isolate_apps("tests")
    def test_compound_unit_measurement_assignment_round_trip(self):
        class CompoundUnitModel(models.Model):
            density = MeasurementField(base_unit="kg/m^3", null=True, blank=True)

            class Meta:
                app_label = "tests"

        instance = CompoundUnitModel()
        instance.density = Measurement(1, "g/cm^3")
        instance.full_clean()

        self.assertEqual(instance.density_value, Decimal("1000"))  # type: ignore
        self.assertEqual(ureg(instance.density_unit).units, ureg("g/cm^3").units)  # type: ignore
        self.assertEqual(ureg(instance.density.unit).units, ureg("g/cm^3").units)  # type: ignore

    def test_edge_case_zero_value(self):
        self.instance.length = Measurement(0, "meter")
        self.instance.full_clean()
        self.assertEqual(self.instance.length.quantity.magnitude, Decimal("0"))
        self.assertEqual(self.instance.length.quantity.units, ureg("meter"))

    def test_offset_unit_assignment_to_kelvin_base_unit(self):
        self.instance.temperature = Measurement(25, "degC")
        self.instance.full_clean()
        self.assertEqual(self.instance.temperature_value, Decimal("298.15"))  # type: ignore
        self.assertEqual(self.instance.temperature_unit, "degree_Celsius")  # type: ignore
        self.assertEqual(self.instance.temperature.unit, "degree_Celsius")  # type: ignore
        self.assertAlmostEqual(float(self.instance.temperature.magnitude), 25.0)  # type: ignore

    def test_offset_unit_descriptor_reconstruction_from_kelvin_storage(self):
        self.instance.temperature_value = Decimal("298.15")  # type: ignore
        self.instance.temperature_unit = "degC"  # type: ignore
        temperature = self.instance.temperature
        self.assertIsNotNone(temperature)
        self.assertEqual(temperature.unit, "degree_Celsius")  # type: ignore[union-attr]
        self.assertAlmostEqual(float(temperature.magnitude), 25.0)  # type: ignore[union-attr]

    def test_from_stored_components_reconstructs_measurement(self):
        field = self.TestModel._meta.get_field("length")

        measurement = field._from_stored_components(Decimal("5"), "centimeter")

        self.assertIsNotNone(measurement)
        self.assertEqual(measurement.magnitude, Decimal("500"))  # type: ignore[union-attr]
        self.assertEqual(measurement.unit, "centimeter")  # type: ignore[union-attr]

    def test_from_stored_components_returns_none_for_missing_parts(self):
        field = self.TestModel._meta.get_field("length")

        self.assertIsNone(field._from_stored_components(None, "meter"))
        self.assertIsNone(field._from_stored_components(Decimal("5"), None))

    def test_from_stored_components_falls_back_for_invalid_or_incompatible_units(
        self,
    ):
        field = self.TestModel._meta.get_field("length")

        invalid = field._from_stored_components(Decimal("5"), "not_a_unit")
        incompatible = field._from_stored_components(Decimal("5"), "second")

        self.assertIsNotNone(invalid)
        self.assertEqual(invalid.magnitude, Decimal("5"))  # type: ignore[union-attr]
        self.assertEqual(invalid.unit, "meter")  # type: ignore[union-attr]
        self.assertIsNotNone(incompatible)
        self.assertEqual(incompatible.magnitude, Decimal("5"))  # type: ignore[union-attr]
        self.assertEqual(incompatible.unit, "meter")  # type: ignore[union-attr]

    @isolate_apps("tests")
    def test_count_measurement_field_preserves_count_after_scalar_arithmetic(self):
        class Inventory(models.Model):
            quantity = MeasurementField(base_unit="count", null=True, blank=True)

            class Meta:
                app_label = "tests"

        instance = Inventory()
        instance.quantity = Measurement(Decimal("6"), "count") / Decimal("2")
        instance.full_clean()

        self.assertEqual(instance.quantity_value, Decimal("3"))  # type: ignore
        self.assertEqual(instance.quantity_unit, "count")  # type: ignore
        self.assertIsNotNone(instance.quantity)
        self.assertEqual(instance.quantity.unit, "count")  # type: ignore[union-attr]
        self.assertEqual(instance.quantity.to("count").magnitude, Decimal("3"))  # type: ignore[union-attr]

    def test_descriptor_falls_back_to_base_unit_for_unknown_stored_unit(self):
        self.instance.length_value = Decimal("5")  # type: ignore
        self.instance.length_unit = "not_a_unit"  # type: ignore

        length = self.instance.length

        self.assertIsNotNone(length)
        self.assertEqual(length.magnitude, Decimal("5"))  # type: ignore[union-attr]
        self.assertEqual(length.unit, "meter")  # type: ignore[union-attr]
        self.assertEqual(self.instance.length_unit, "not_a_unit")  # type: ignore

    def test_offset_unit_base_unit_is_rejected(self):
        with self.assertRaises(InvalidMeasurementFieldBaseUnitError) as ctx:
            MeasurementField(base_unit="degC")
        self.assertIn("must be multiplicative", str(ctx.exception))
        self.assertIn("Use a unit like 'K'", str(ctx.exception))

    def test_get_prep_value_normalizes_offset_unit_storage_magnitude(self):
        field = self.TestModel._meta.get_field("temperature")
        prepared = field.get_prep_value(Measurement(70, "degF"))
        self.assertEqual(prepared, Decimal("294.2611111111"))

    def test_get_prep_value_wraps_invalid_string_as_validation_error(self):
        field = self.TestModel._meta.get_field("length")

        with self.assertRaises(ValidationError):
            field.get_prep_value("not_a_measurement")

    def test_clean_runs_validators_once_for_measurement(self):
        calls: list[Measurement] = []

        def validator(value: Measurement) -> None:
            calls.append(value)

        field = MeasurementField(base_unit="kg", null=True, blank=True)
        field.validators.append(validator)
        measurement = Measurement(1, "kg")

        cleaned = field.clean(measurement)

        self.assertIs(cleaned, measurement)
        self.assertEqual(calls, [measurement])

    def test_run_validators_aggregates_validation_errors(self):
        def first_validator(value: Measurement) -> None:
            raise ValidationError("first")

        def second_validator(value: Measurement) -> None:
            raise ValidationError("second")

        field = MeasurementField(base_unit="kg", null=True, blank=True)
        field.validators.extend([first_validator, second_validator])

        with self.assertRaises(ValidationError) as context:
            field.run_validators(Measurement(1, "kg"))

        self.assertEqual(context.exception.messages, ["first", "second"])

    def test_clean_skips_validators_for_blank_value(self):
        calls: list[object] = []

        def validator(value: object) -> None:
            calls.append(value)

        field = MeasurementField(base_unit="kg", null=True, blank=True)
        field.validators.append(validator)

        cleaned = field.clean("")

        self.assertEqual(cleaned, "")
        self.assertEqual(calls, [])

    def test_clean_returns_same_blank_container_when_blank_allowed(self):
        field = MeasurementField(base_unit="kg", null=True, blank=True)
        empty_list: list[object] = []
        empty_dict: dict[object, object] = {}

        self.assertIs(field.clean(empty_list), empty_list)
        self.assertIs(field.clean(empty_dict), empty_dict)

    def test_clean_rejects_non_empty_string_without_parsing(self):
        field = MeasurementField(base_unit="kg", null=True, blank=True)

        with self.assertRaises(ValidationError):
            field.clean("100 g")

    def test_to_python_is_passthrough(self):
        field = MeasurementField(base_unit="kg", null=True, blank=True)
        measurement = Measurement(1, "kg")
        empty_list: list[object] = []

        self.assertIs(field.to_python(measurement), measurement)
        self.assertEqual(field.to_python("1 meter"), "1 meter")
        self.assertEqual(field.to_python(""), "")
        self.assertIs(field.to_python(empty_list), empty_list)

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

    def test_migration_writer_serializes_scalar_measurement_default(self):
        """Scalar defaults round-trip through Django's migration serializer."""
        default = Measurement(
            Decimal("1.123456789012345678901234567890123"),
            "meter",
        )
        field = MeasurementField(
            base_unit="meter",
            default=default,
            blank=True,
        )

        serialized, imports = MigrationWriter.serialize(field)
        namespace: dict[str, object] = {}
        exec(  # noqa: S102 - execute Django's generated migration expression.
            "\n".join(imports) + f"\nrebuilt = {serialized}", namespace
        )
        rebuilt = namespace["rebuilt"]

        self.assertIsInstance(rebuilt, MeasurementField)
        assert isinstance(rebuilt, MeasurementField)
        self.assertEqual(rebuilt.base_unit, "meter")
        self.assertTrue(rebuilt.blank)
        self.assertEqual(rebuilt.default, default)
        self.assertIsInstance(rebuilt.default, Measurement)
        assert isinstance(rebuilt.default, Measurement)
        self.assertEqual(rebuilt.default.magnitude, default.magnitude)
        self.assertEqual(rebuilt.default.unit, default.unit)

    @isolate_apps("tests")
    def test_scalar_measurement_default_populates_paired_fields_once(self):
        class DefaultedModel(models.Model):
            length = MeasurementField(
                base_unit="meter",
                default=Measurement(Decimal("2"), "meter"),
            )

            class Meta:
                app_label = "tests"

        instance = DefaultedModel()

        self.assertEqual(instance.length, Measurement(Decimal("2"), "meter"))
        self.assertEqual(instance.length_value, Decimal("2"))
        self.assertEqual(instance.length_unit, "meter")

    @isolate_apps("tests")
    def test_callable_measurement_default_runs_once_per_instance(self):
        calls: list[Measurement] = []

        def default_length() -> Measurement:
            measurement = Measurement(Decimal("3"), "meter")
            calls.append(measurement)
            return measurement

        class DefaultedModel(models.Model):
            length = MeasurementField(base_unit="meter", default=default_length)

            class Meta:
                app_label = "tests"

        first = DefaultedModel()
        second = DefaultedModel()

        self.assertEqual(calls, [Measurement(Decimal("3"), "meter")] * 2)
        self.assertEqual(first.length_value, Decimal("3"))
        self.assertEqual(second.length_value, Decimal("3"))

    @isolate_apps("tests")
    def test_explicit_measurement_suppresses_callable_default(self):
        calls: list[Measurement] = []

        def default_length() -> Measurement:
            measurement = Measurement(Decimal("3"), "meter")
            calls.append(measurement)
            return measurement

        class DefaultedModel(models.Model):
            length = MeasurementField(base_unit="meter", default=default_length)

            class Meta:
                app_label = "tests"

        instance = DefaultedModel(length=Measurement(Decimal("7"), "meter"))

        self.assertEqual(calls, [])
        self.assertEqual(instance.length_value, Decimal("7"))
        self.assertEqual(instance.length_unit, "meter")

    @isolate_apps("tests")
    def test_from_db_deferred_measurement_does_not_materialize_default(self):
        class DefaultedModel(models.Model):
            length = MeasurementField(
                base_unit="meter",
                default=Measurement(Decimal("2"), "meter"),
                null=True,
            )

            class Meta:
                app_label = "tests"

        with patch.object(
            DefaultedModel,
            "refresh_from_db",
            side_effect=AssertionError("unexpected deferred fetch"),
        ):
            instance = DefaultedModel.from_db("default", ["id"], [1])

        self.assertNotIn("length_value", instance.__dict__)
        self.assertNotIn("length_unit", instance.__dict__)

    @isolate_apps("tests")
    def test_from_db_loaded_null_measurement_does_not_use_default(self):
        class DefaultedModel(models.Model):
            length = MeasurementField(
                base_unit="meter",
                default=Measurement(Decimal("2"), "meter"),
                null=True,
            )

            class Meta:
                app_label = "tests"

        instance = DefaultedModel.from_db(
            "default",
            ["id", "length_value", "length_unit"],
            [1, None, None],
        )

        self.assertIsNone(instance.length_value)
        self.assertIsNone(instance.length_unit)
        self.assertIsNone(instance.length)

    @isolate_apps("tests")
    def test_inherited_callable_default_runs_once_when_it_returns_none(self):
        calls: list[None] = []

        def default_length() -> None:
            calls.append(None)
            return None

        class Base(models.Model):
            length = MeasurementField(
                base_unit="meter",
                default=default_length,
                null=True,
            )

            class Meta:
                abstract = True
                app_label = "tests"

        class DefaultedModel(Base):
            class Meta:
                app_label = "tests"

        instance = DefaultedModel()

        self.assertEqual(calls, [None])
        self.assertIsNone(instance.length_value)
        self.assertIsNone(instance.length_unit)

    @isolate_apps("tests")
    def test_custom_init_observes_default_after_super(self):
        class DefaultedModel(models.Model):
            length = MeasurementField(
                base_unit="meter",
                default=Measurement(Decimal("2"), "meter"),
                null=True,
            )

            class Meta:
                app_label = "tests"

            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                self.observed_length = self.length

        instance = DefaultedModel()

        self.assertEqual(instance.observed_length, Measurement(Decimal("2"), "meter"))

    @isolate_apps("tests")
    def test_non_editable_measurement_defaults_bypass_only_internal_assignment(self):
        calls: list[Measurement] = []

        def default_length() -> Measurement:
            measurement = Measurement(Decimal("3"), "meter")
            calls.append(measurement)
            return measurement

        class DefaultedModel(models.Model):
            scalar = MeasurementField(
                base_unit="meter",
                default=Measurement(Decimal("2"), "meter"),
                editable=False,
            )
            callable = MeasurementField(
                base_unit="meter",
                default=default_length,
                editable=False,
            )

            class Meta:
                app_label = "tests"

        instance = DefaultedModel()

        self.assertEqual(instance.scalar_value, Decimal("2"))
        self.assertEqual(instance.callable_value, Decimal("3"))
        self.assertEqual(calls, [Measurement(Decimal("3"), "meter")])
        with self.assertRaises(ValidationError):
            instance.scalar = Measurement(Decimal("4"), "meter")

    @isolate_apps("tests")
    def test_failed_default_initialization_does_not_leak_into_following_instance(self):
        calls: list[Measurement] = []

        def default_length() -> Measurement:
            measurement = Measurement(Decimal("2"), "meter")
            calls.append(measurement)
            return measurement

        class DefaultedModel(models.Model):
            length = MeasurementField(base_unit="meter", default=default_length)

            class Meta:
                app_label = "tests"

        for _ in range(3):
            with self.assertRaises(TypeError):
                DefaultedModel(unexpected=True)

        instance = DefaultedModel()

        self.assertEqual(calls, [Measurement(Decimal("2"), "meter")])
        self.assertEqual(instance.length_value, Decimal("2"))

    @isolate_apps("tests")
    def test_default_storage_does_not_unlock_reentrant_read_only_assignment(self):
        class DefaultedModel(models.Model):
            primary = MeasurementField(
                base_unit="meter",
                default=Measurement(Decimal("2"), "meter"),
            )
            locked = MeasurementField(base_unit="meter", null=True, editable=False)

            class Meta:
                app_label = "tests"

            def __setattr__(self, name: str, value: object) -> None:
                if name == "primary_value":
                    try:
                        self.locked = Measurement(Decimal("4"), "meter")
                    except MeasurementFieldNotEditableError:
                        super().__setattr__("reentrant_assignment_blocked", True)
                super().__setattr__(name, value)

        instance = DefaultedModel()

        self.assertTrue(instance.reentrant_assignment_blocked)
        self.assertIsNone(instance.locked_value)


class MeasurementFieldConstraintTests(TransactionTestCase):
    @isolate_apps("tests")
    def test_unique_constraint_targets_value_column(self):
        """
        Verify that adding a UniqueConstraint on a MeasurementField uses the field's value column.

        Creates temporary Container and Size models where Size.volume is a MeasurementField, applies a UniqueConstraint on ("container", "volume"), inspects database constraints, and asserts the constraint exists and references the container foreign-key column and the measurement value column ("container_id", "volume_value"). Cleans up created tables afterward.
        """

        class Container(models.Model):
            name = models.CharField(max_length=20)

            class Meta:
                app_label = "tests"

        constraint = models.UniqueConstraint(
            fields=["container", "volume"],
            name="uniq_container_volume",
        )
        volume_field = MeasurementField(base_unit="liter")

        class Size(models.Model):
            container = models.ForeignKey(Container, on_delete=models.CASCADE)
            volume = volume_field

            class Meta:
                app_label = "tests"

        # Exercise the logical-field remapping before schema creation, then keep
        # the remapped constraint out of the initial CREATE TABLE statement.
        Size._meta.constraints.append(constraint)
        volume_field._remap_constraints_to_value_field(Size)
        remapped_constraint = Size._meta.constraints.pop()

        try:
            with connection.schema_editor() as editor:
                editor.create_model(Container)
                editor.create_model(Size)
                Size._meta.constraints.append(remapped_constraint)
                editor.add_constraint(Size, remapped_constraint)

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
        """
        Verifies that applying `unique=True` to a MeasurementField creates a unique constraint on its value column.

        Creates a temporary model with a MeasurementField configured as unique, creates the table, introspects database constraints, and asserts that a unique constraint exists for the underlying `volume_value` column. The temporary table is removed after inspection.
        """

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

    @isolate_apps("tests")
    def test_unique_together_remaps_value_column(self):
        """
        Ensure unique_together entries referencing MeasurementField names point to the backing value column.

        Creates a Product model with (name, weight) unique_together, builds the table, inspects constraints, and asserts the unique constraint includes `weight_value`. Cleans up the temporary table afterward.
        """

        class Product(models.Model):
            name = models.CharField(max_length=30)
            weight = MeasurementField(base_unit="kg")

            class Meta:
                app_label = "tests"
                unique_together = (("name", "weight"),)

        try:
            with connection.schema_editor() as editor:
                editor.create_model(Product)

            with connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(
                    cursor, Product._meta.db_table
                )
        finally:
            if Product._meta.db_table in connection.introspection.table_names():
                with connection.schema_editor() as editor:
                    editor.delete_model(Product)

        multi_unique_columns = [
            info["columns"]
            for info in constraints.values()
            if info["unique"] and len(info["columns"]) > 1
        ]
        self.assertIn(["name", "weight_value"], multi_unique_columns)
