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
from unittest.mock import patch


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
    # String without Regex‐Validator
    char_field = models.CharField(max_length=10, null=False)
    text_field = models.TextField(null=False)
    # String with Regex‐Validator
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
            "general_manager.factory.factories.random.choice",
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

        GMC.Factory = lambda **kwargs: dummy  # type: ignore
        DummyForeignKey._general_manager_class = GMC  # type: ignore
        with patch(
            "general_manager.factory.factoryMethods.random.choice", return_value=True
        ):

            field = DummyModel._meta.get_field("dummy_fk")
            result = getFieldValue(field)
            # Hier kommt kein LazyFunction, sondern direkt das factory-Ergebnis
            self.assertIs(result, dummy)

    def test_fk_with_factory_existing_instance(self):
        dummy1 = DummyForeignKey(name="a")
        dummy2 = DummyForeignKey(name="b")
        field = DummyModel._meta.get_field("dummy_fk")

        class GMC:
            pass

        GMC.Factory = lambda **kwargs: dummy1  # type: ignore
        DummyForeignKey._general_manager_class = GMC  # type: ignore

        with (
            patch(
                "general_manager.factory.factories.random.choice",
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

        GMC2.Factory = lambda **kwargs: dummy  # type: ignore
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
        dummy1 = DummyManyToMany(name="foo", id=1)
        dummy2 = DummyManyToMany(name="bar", id=2)

        class GMC:
            pass

        GMC.Factory = lambda **kwargs: dummy1  # type: ignore
        DummyManyToMany._general_manager_class = GMC  # type: ignore

        field = DummyModel._meta.get_field("dummy_m2m")
        with patch.object(
            field.related_model.objects, "all", return_value=[dummy1, dummy2]  # type: ignore
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

        GMC.Factory = lambda **kwargs: dummy1  # type: ignore
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
            field.related_model.objects, "all", return_value=[dummy1, dummy2]  # type: ignore
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
