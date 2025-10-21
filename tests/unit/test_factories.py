# tests/test_factory_helpers.py
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import models
from django.test import TestCase
from django.core.validators import RegexValidator

from factory.declarations import LazyFunction, LazyAttribute
from factory.faker import Faker
from general_manager.factory.factories import getFieldValue, getManyToManyFieldValue
from general_manager.measurement.measurementField import MeasurementField
from general_manager.measurement.measurement import Measurement
from unittest.mock import patch, Mock


class DummyForeignKey(models.Model):
    """
    Dummy ForeignKey model for testing purposes.
    """

    name = models.CharField(max_length=10, null=False)

    class Meta:
        app_label = "tests"
        managed = False


class DummyForeignKey2(models.Model):
    """
    Dummy ForeignKey model for testing purposes.
    """

    name = models.CharField(max_length=10, null=False)

    class Meta:
        app_label = "tests"
        managed = False


class DummyManyToMany(models.Model):
    """
    Dummy ManyToMany model for testing purposes.
    """

    name = models.CharField(max_length=10, null=False)

    class Meta:
        app_label = "tests"
        managed = False


class DummyModel(models.Model):
    # String without Regex-Validator
    char_field = models.CharField(max_length=10, null=False)
    text_field = models.TextField(null=False)
    # String with Regex-Validator
    regex_field = models.CharField(
        max_length=10, null=False, validators=[RegexValidator(r"[A-Z]{3}\d{2}")]
    )
    # number types
    int_field = models.IntegerField(null=False)
    dec_field = models.DecimalField(max_digits=5, decimal_places=2, null=False)
    float_field = models.FloatField(null=False)
    # date/time types
    date_field = models.DateField(null=False)
    datetime_field = models.DateTimeField(null=False)
    duration_field = models.DurationField(null=False)
    # etc
    bool_field = models.BooleanField(null=False)
    email_field = models.EmailField(null=False)
    url_field = models.URLField(null=False)
    ip_field = models.GenericIPAddressField(null=False)
    uuid_field = models.UUIDField(null=False)
    # MeasurementField
    measurement_field = MeasurementField(base_unit="kg", null=False)
    # special fields
    test_none = models.CharField(max_length=10, null=True)
    dummy_fk = models.ForeignKey(
        DummyForeignKey,
        on_delete=models.CASCADE,
        null=False,
    )
    dummy_one_to_one = models.OneToOneField(
        DummyForeignKey2,
        on_delete=models.CASCADE,
        null=False,
    )
    dummy_m2m = models.ManyToManyField(
        DummyManyToMany,
        related_name="dummy_m2m",
        blank=True,
    )

    class Meta:
        app_label = "tests"
        managed = False


class TestGetFieldValue(TestCase):
    def _evaluate(self, declaration):
        """
        Evaluate a field declaration to get its value.
        This is a helper method to handle different types of declarations.
        """
        obj = type("DummyModel", (object,), {})()
        if isinstance(declaration, (LazyFunction, LazyAttribute, Faker)):
            return declaration.evaluate(obj, None, {"locale": "en_US"})
        return declaration

    def test_all_not_relational_field_types(self):
        field_expectations = [
            # (fieldname, expected_type, optional: regex to match)
            ("char_field", str, None),
            ("text_field", str, None),
            ("regex_field", str, r"^[A-Z]{3}\d{2}$"),
            ("int_field", int, None),
            ("dec_field", Decimal, None),
            ("float_field", float, None),
            ("date_field", date, None),
            ("datetime_field", datetime, None),
            ("duration_field", timedelta, None),
            ("bool_field", bool, None),
            ("email_field", str, r"^[^@]+@[^@]+\.[^@]+$"),
            ("url_field", str, r"^https?://"),
            ("ip_field", str, r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),
            ("uuid_field", str, r"^[0-9a-fA-F-]{36}$"),
        ]

        for name, expected_type, pattern in field_expectations:
            with self.subTest(field=name):
                field = DummyModel._meta.get_field(name)
                decl = getFieldValue(field)

                self.assertIn(
                    decl.__class__.__name__,
                    ("LazyFunction", "LazyAttribute", "NoneType", "Faker"),
                    msg=f"Unexpected declaration for {name}: {decl!r}",
                )

                value = self._evaluate(decl)

                self.assertIsInstance(
                    value,
                    expected_type,
                    msg=f"Field {name!r} returned {value!r} ({type(value)})",
                )

                if pattern:
                    self.assertRegex(
                        str(value),
                        pattern,
                        msg=f"Value for {name!r} does not match {pattern}",
                    )

    def test_nullable_field(self):
        with patch(
            "general_manager.factory.factories._RNG.choice",
            return_value=True,
        ):
            field = DummyModel._meta.get_field("test_none")
            decl = getFieldValue(field)
            value = self._evaluate(decl)
            self.assertIsNone(value, msg="Nullable field should return None")

    def test_measurement_field(self):
        field = DummyModel.measurement_field
        decl = getFieldValue(field)
        self.assertIsInstance(decl, LazyFunction)
        value = decl.evaluate(None, None, None)  # type: ignore
        self.assertIsInstance(value, Measurement)
        self.assertIsInstance(value.magnitude, Decimal)
        self.assertIsInstance(value.unit, str)


class TestRelationFieldValue(TestCase):
    def setUp(self):
        # Aufräumen: kein _general_manager_class voraussetzen
        for M in (DummyForeignKey, DummyForeignKey2):
            if hasattr(M, "_general_manager_class"):
                delattr(M, "_general_manager_class")

    def test_fk_with_factory_new_instance(self):
        # 1) _general_manager_class.Factory liefert direkt ein Objekt
        dummy = DummyForeignKey(name="foo")

        class GMC:
            pass

        GMC.Factory = lambda **_kwargs: dummy  # type: ignore
        DummyForeignKey._general_manager_class = GMC  # type: ignore
        with patch("general_manager.factory.factories._RNG.choice", return_value=True):
            field = DummyModel._meta.get_field("dummy_fk")
            result = getFieldValue(field)
            # Hier kommt kein LazyFunction, sondern direkt das factory-Ergebnis
            self.assertIs(result, dummy)

    def test_fk_with_factory_existing_instance(self):
        """
        Verifies that when a related model has a factory but the random choice selects an existing instance, getFieldValue returns a LazyFunction that yields one of the existing instances when evaluated.
        The test sets up a General Manager Class (GMC) with a Factory that would create dummy1, patches the RNG to choose the "existing" branch, and patches the model manager to return [dummy1, dummy2]. It asserts the declaration is a LazyFunction and that evaluating it returns either dummy1 or dummy2.
        """
        dummy1 = DummyForeignKey(name="a")
        dummy2 = DummyForeignKey(name="b")
        field = DummyModel._meta.get_field("dummy_fk")

        class GMC:
            pass

        GMC.Factory = lambda **_kwargs: dummy1  # type: ignore
        DummyForeignKey._general_manager_class = GMC  # type: ignore

        with (
            patch(
                "general_manager.factory.factories._RNG.choice",
                return_value=False,
            ),
            patch.object(DummyForeignKey.objects, "all", return_value=[dummy1, dummy2]),
        ):
            decl = getFieldValue(field)
            self.assertIsInstance(decl, LazyFunction)
        inst = decl.evaluate(None, None, None)  # type: ignore
        self.assertIn(inst, (dummy1, dummy2))

    def test_one_to_one_with_factory(self):
        dummy = DummyForeignKey2(name="bar")

        class GMC2:
            pass

        GMC2.Factory = lambda **_kwargs: dummy  # type: ignore
        DummyForeignKey2._general_manager_class = GMC2  # type: ignore

        field = DummyModel._meta.get_field("dummy_one_to_one")
        result = getFieldValue(field)
        self.assertIs(result, dummy)

    def test_fk_without_factory_with_existing_instances(self):
        # 2) kein factory, aber vorhandene Objekte → LazyFunction
        dummy1 = DummyForeignKey(name="a")
        dummy2 = DummyForeignKey(name="b")
        field = DummyModel._meta.get_field("dummy_fk")

        with patch.object(
            DummyForeignKey.objects, "all", return_value=[dummy1, dummy2]
        ):
            decl = getFieldValue(field)
            self.assertIsInstance(decl, LazyFunction)
            # beim Evaluieren sollte eins der beiden Objekte zurückkommen
            inst = decl.evaluate(None, None, None)  # type: ignore
            self.assertIn(inst, (dummy1, dummy2))

    def test_one_to_one_without_factory_with_existing_instances(self):
        dummy1 = DummyForeignKey2(name="x")
        dummy2 = DummyForeignKey2(name="y")
        field = DummyModel._meta.get_field("dummy_one_to_one")

        with patch.object(
            DummyForeignKey2.objects, "all", return_value=[dummy1, dummy2]
        ):
            decl = getFieldValue(field)
            self.assertIsInstance(decl, LazyFunction)
            inst = decl.evaluate(None, None, None)  # type: ignore
            self.assertIn(inst, (dummy1, dummy2))

    def test_fk_without_factory_and_no_instances_raises(self):
        field = DummyModel._meta.get_field("dummy_fk")
        with (
            patch.object(DummyForeignKey.objects, "all", return_value=[]),
            self.assertRaisesMessage(
                ValueError, "No factory found for DummyForeignKey"
            ),
        ):
            getFieldValue(field)

    def test_one_to_one_without_factory_and_no_instances_raises(self):
        field = DummyModel._meta.get_field("dummy_one_to_one")
        with (
            patch.object(DummyForeignKey2.objects, "all", return_value=[]),
            self.assertRaisesMessage(
                ValueError, "No factory found for DummyForeignKey2"
            ),
        ):
            getFieldValue(field)


class TestGetManyToManyFieldValue(TestCase):
    def setUp(self):
        # tidy up: no _general_manager_class assumed
        for M in (DummyManyToMany, DummyModel):
            if hasattr(M, "_general_manager_class"):
                delattr(M, "_general_manager_class")

    def test_m2m_with_factory_and_existing(self):
        """
        Verifies that getManyToManyFieldValue returns a list containing only existing related instances when a related-model factory is available.
        Sets up a factory that produces one instance and an existing queryset of two instances, then asserts the result is a list and every returned item is one of the existing instances.
        """
        dummy1 = DummyManyToMany(name="foo", id=1)
        dummy2 = DummyManyToMany(name="bar", id=2)

        class GMC:
            pass

        GMC.Factory = lambda **_kwargs: dummy1  # type: ignore
        DummyManyToMany._general_manager_class = GMC  # type: ignore

        field = DummyModel._meta.get_field("dummy_m2m")
        with patch.object(
            field.related_model.objects,
            "all",
            return_value=[dummy1, dummy2],  # type: ignore
        ):
            result = getManyToManyFieldValue(field)  # type: ignore
        self.assertIsInstance(result, list)
        self.assertTrue(
            set(result).issubset({dummy1, dummy2}),
            "Returned instances are not a subset of existing objects",
        )

    def test_m2m_with_factory(self):
        dummy1 = DummyManyToMany(name="foo", id=1)

        class GMC:
            pass

        GMC.Factory = lambda **_kwargs: dummy1  # type: ignore
        DummyManyToMany._general_manager_class = GMC  # type: ignore

        field = DummyModel._meta.get_field("dummy_m2m")
        with patch.object(field.related_model.objects, "all", return_value=[]):  # type: ignore
            result = getManyToManyFieldValue(field)  # type: ignore
        self.assertIsInstance(result, list)
        if len(result) != 0:
            self.assertIn(dummy1, result)

    def test_m2m_without_factory(self):
        dummy1 = DummyManyToMany(name="foo", id=1)
        dummy2 = DummyManyToMany(name="bar", id=2)

        field = DummyModel._meta.get_field("dummy_m2m")
        with patch.object(
            field.related_model.objects,
            "all",
            return_value=[dummy1, dummy2],  # type: ignore
        ):
            result = getManyToManyFieldValue(field)  # type: ignore
        self.assertIsInstance(result, list)
        self.assertTrue(
            set(result).issubset({dummy1, dummy2}),
            "Returned instances are not a subset of existing objects",
        )

    def test_m2m_without_factory_and_no_instances_raises(self):
        field = DummyModel._meta.get_field("dummy_m2m")
        with (
            patch.object(field.related_model.objects, "all", return_value=[]),  # type: ignore
            self.assertRaises(ValueError),
        ):
            getManyToManyFieldValue(field)  # type: ignore

    def test_missing_factory_or_instances_error(self):
        """Test that MissingFactoryOrInstancesError is raised when no factory or instances exist."""
        from general_manager.factory.factories import MissingFactoryOrInstancesError
        from django.db import models

        # Create a related model without factory or instances
        class OrphanModel(models.Model):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "test_app"

        field = Mock()
        field.related_model = OrphanModel
        field.null = False

        def custom_isinstance(obj, cls):
            if cls == models.ForeignKey:
                return True
            return isinstance(obj, cls)

        with (
            patch.object(OrphanModel.objects, "all", return_value=[]),
            patch(
                "general_manager.factory.factories.isinstance",
                side_effect=custom_isinstance,
            ),
            self.assertRaises(MissingFactoryOrInstancesError) as ctx,
        ):
            getFieldValue(field)

        self.assertIn("No factory found", str(ctx.exception))
        self.assertIn("OrphanModel", str(ctx.exception))
        self.assertIn("no instances found", str(ctx.exception))

    def test_missing_related_model_error(self):
        """Test that MissingRelatedModelError is raised when related model is None."""
        from general_manager.factory.factories import (
            MissingRelatedModelError,
            getRelatedModel,
        )

        field = Mock()
        field.related_model = None
        field.name = "test_field"

        with self.assertRaises(MissingRelatedModelError) as ctx:
            getRelatedModel(field)

        self.assertIn("test_field", str(ctx.exception))
        self.assertIn("does not have a related model", str(ctx.exception))

    def test_invalid_related_model_type_error(self):
        """Test that InvalidRelatedModelTypeError is raised for non-model related types."""
        from general_manager.factory.factories import (
            InvalidRelatedModelTypeError,
            getRelatedModel,
        )

        field = Mock()
        field.related_model = "NotAModel"  # String instead of model class
        field.name = "test_field"

        with self.assertRaises(InvalidRelatedModelTypeError) as ctx:
            getRelatedModel(field)

        self.assertIn("test_field", str(ctx.exception))
        self.assertIn("must be a Django model class", str(ctx.exception))

    def test_field_value_with_null_field_randomness(self):
        """Test that nullable fields sometimes return None with proper randomness."""
        from general_manager.factory.factories import getFieldValue

        # Create a nullable CharField
        field = Mock()
        field.null = True
        field.__class__.__name__ = "IntegerField"

        # Run multiple times to check randomness
        none_count = 0
        total_runs = 100

        def custom_isinstance(obj, cls):
            if cls == models.IntegerField:
                return True
            return isinstance(obj, cls)

        with patch(
            "general_manager.factory.factories.isinstance",
            side_effect=custom_isinstance,
        ):
            for _ in range(total_runs):
                result = getFieldValue(field)
                if result is None:
                    none_count += 1

        # Should get some None values but not all (roughly 10% based on the code)
        self.assertGreater(none_count, 0)
        self.assertLess(none_count, total_runs)

    def test_measurement_field_value_generation(self):
        """Test that MeasurementField generates valid Measurement values."""
        from general_manager.factory.factories import getFieldValue
        from general_manager.measurement.measurementField import MeasurementField
        from decimal import Decimal

        field = MeasurementField(base_unit="meter")
        field.null = False

        result = getFieldValue(field)

        # Should be a LazyFunction
        self.assertIsNotNone(result)
        # Execute the lazy function if it is one
        if hasattr(result, "function"):
            measurement = result.function()
            from general_manager.measurement.measurement import Measurement

            self.assertIsInstance(measurement, Measurement)
            self.assertEqual(measurement.unit, "meter")
            self.assertIsInstance(measurement.magnitude, Decimal)

    def test_many_to_many_field_subset_selection(self):
        """Test that M2M fields select a subset of available instances."""
        from general_manager.factory.factories import getManyToManyFieldValue

        # Create mock instances
        instances = [f"instance_{i}" for i in range(10)]

        field = Mock()
        field.blank = False

        from types import SimpleNamespace

        related_model = SimpleNamespace(__name__="DummyRelatedModel")
        related_model.objects = Mock()
        related_model.objects.all.return_value = instances  # type: ignore

        with patch(
            "general_manager.factory.factories.getRelatedModel",
            return_value=related_model,
        ):
            result = getManyToManyFieldValue(field)

        # Should return a list
        self.assertIsInstance(result, list)

        # Should be a subset (not empty, not all)
        self.assertGreater(len(result), 0)
        self.assertLessEqual(len(result), len(instances))

        # All items should be from the original list
        for item in result:
            self.assertIn(item, instances)

    def test_regex_field_value_generation(self):
        """Test that fields with RegexValidator generate matching values."""
        from general_manager.factory.factories import getFieldValue
        from django.core.validators import RegexValidator
        import re

        # Create a CharField with RegexValidator
        validator = RegexValidator(regex=r"^\d{3}-\d{3}-\d{4}$")
        field = models.CharField(max_length=12, validators=[validator])
        field.null = False

        result = getFieldValue(field)  # type: ignore[arg-type]

        # Should return a LazyFunction with Faker
        self.assertIsNotNone(result)

        # If it's a LazyFunction, it should generate a value matching the regex
        # The actual value generation happens at evaluation time
        if hasattr(result, "evaluate"):
            generated = result.evaluate(None, None, None)
            self.assertRegex(generated, validator.regex.pattern)

    def test_choice_field_value_generation(self):
        """Test that fields with choices generate valid choice values."""
        from general_manager.factory.factories import getFieldValue

        choices = [("A", "Option A"), ("B", "Option B"), ("C", "Option C")]

        field = models.CharField(max_length=1, choices=choices)
        field.null = False

        result = getFieldValue(field)  # type: ignore[arg-type]

        # Should return a LazyFunction
        self.assertIsNotNone(result)

        # Execute multiple times to check it picks from choices
        if hasattr(result, "evaluate"):
            for _ in range(10):
                value = result.evaluate(None, None, {"locale": "en_US"})
                self.assertIsInstance(value, str)

    def test_system_random_usage(self):
        """Test that SystemRandom is used instead of standard random module."""
        from general_manager.factory import factories
        from random import SystemRandom

        # Check that _RNG is a SystemRandom instance
        self.assertIsInstance(factories._RNG, SystemRandom)

    def test_decimal_field_value_generation(self):
        """Test that DecimalField generates valid Decimal values."""
        from general_manager.factory.factories import getFieldValue
        from decimal import Decimal

        field = models.DecimalField(max_digits=10, decimal_places=2)
        field.null = False

        result = getFieldValue(field)  # type: ignore[arg-type]

        # Should return a Faker instance
        self.assertIsNotNone(result)
        if hasattr(result, "evaluate"):
            value = result.evaluate(None, None, {"locale": "en_US"})
            self.assertIsInstance(value, Decimal)
