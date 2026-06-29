# tests/test_factory_helpers.py
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.db import models
from django.test import TestCase
from django.core.validators import RegexValidator

from factory.declarations import LazyFunction, LazyAttribute
from factory.faker import Faker
from general_manager.factory.factories import (
    get_field_value,
    get_many_to_many_field_value,
)
from general_manager.measurement.measurement_field import MeasurementField
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
                decl = get_field_value(field)

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
            decl = get_field_value(field)
            value = self._evaluate(decl)
            self.assertIsNone(value, msg="Nullable field should return None")

    def test_measurement_field(self):
        field = DummyModel.measurement_field
        decl = get_field_value(field)
        self.assertIsInstance(decl, LazyFunction)
        value = decl.evaluate(None, None, None)  # type: ignore
        self.assertIsInstance(value, Measurement)
        self.assertIsInstance(value.magnitude, Decimal)
        self.assertIsInstance(value.unit, str)


class TestRelationFieldValue(TestCase):
    def setUp(self):
        # Clean up: ensure no _general_manager_class attribute lingers
        for M in (DummyForeignKey, DummyForeignKey2):
            if hasattr(M, "_general_manager_class"):
                delattr(M, "_general_manager_class")

    def test_fk_with_factory_prefers_existing_instances(self):
        created = DummyForeignKey(name="created")
        existing1 = DummyForeignKey(name="a")
        existing2 = DummyForeignKey(name="b")

        class GMC:
            pass

        GMC.Factory = lambda **_kwargs: created  # type: ignore
        DummyForeignKey._general_manager_class = GMC  # type: ignore

        def deterministic_choice(values):
            values = list(values)
            if values == [True, True, False]:
                return True
            return values[0]

        with (
            patch(
                "general_manager.factory.factories._RNG.choice",
                side_effect=deterministic_choice,
            ),
            patch.object(
                DummyForeignKey.objects,
                "all",
                return_value=[existing1, existing2],
            ),
        ):
            field = DummyModel._meta.get_field("dummy_fk")
            decl = get_field_value(field)
        self.assertIsInstance(decl, LazyFunction)
        selected = decl.evaluate(None, None, None)  # type: ignore
        self.assertIn(selected, (existing1, existing2))
        self.assertIsNot(selected, created)

    def test_fk_with_factory_create_mode_creates_new_instance(self):
        created = DummyForeignKey(name="created")
        field = DummyModel._meta.get_field("dummy_fk")

        class GMC:
            pass

        GMC.Factory = lambda **_kwargs: created  # type: ignore
        DummyForeignKey._general_manager_class = GMC  # type: ignore

        self.assertIs(
            get_field_value(field, relation_generation="create"),
            created,
        )

    def test_fk_with_factory_random_mode_can_create_new_instance(self):
        created = DummyForeignKey(name="created")
        existing = DummyForeignKey(name="existing")
        field = DummyModel._meta.get_field("dummy_fk")

        class GMC:
            pass

        GMC.Factory = lambda **_kwargs: created  # type: ignore
        DummyForeignKey._general_manager_class = GMC  # type: ignore

        with (
            patch(
                "general_manager.factory.factories._RNG.choice",
                return_value=True,
            ),
            patch.object(DummyForeignKey.objects, "all", return_value=[existing]),
        ):
            result = get_field_value(field, relation_generation="random")

        self.assertIs(result, created)

    def test_fk_with_factory_random_mode_can_reuse_existing_instance(self):
        created = DummyForeignKey(name="created")
        existing = DummyForeignKey(name="existing")
        field = DummyModel._meta.get_field("dummy_fk")

        class GMC:
            pass

        GMC.Factory = lambda **_kwargs: created  # type: ignore
        DummyForeignKey._general_manager_class = GMC  # type: ignore

        def deterministic_choice(values):
            values = list(values)
            if values == [True, True, False]:
                return False
            return values[0]

        with (
            patch(
                "general_manager.factory.factories._RNG.choice",
                side_effect=deterministic_choice,
            ),
            patch.object(DummyForeignKey.objects, "all", return_value=[existing]),
        ):
            decl = get_field_value(field, relation_generation="random")

        self.assertIsInstance(decl, LazyFunction)
        self.assertIs(decl.evaluate(None, None, None), existing)  # type: ignore
        self.assertIsNot(existing, created)

    def test_nullable_fk_default_mode_can_return_none_with_related_available(self):
        created = DummyForeignKey(name="created")
        existing = DummyForeignKey(name="existing")
        field = DummyModel._meta.get_field("dummy_fk")
        original_null = field.null

        class GMC:
            pass

        def factory(**_kwargs):
            self.fail("default nullable relation generation should return None first")
            return created

        GMC.Factory = factory  # type: ignore
        DummyForeignKey._general_manager_class = GMC  # type: ignore
        field.null = True
        try:
            with (
                patch(
                    "general_manager.factory.factories._RNG.choice",
                    return_value=True,
                ),
                patch.object(
                    DummyForeignKey.objects,
                    "all",
                    return_value=[existing],
                ),
            ):
                self.assertIsNone(get_field_value(field))
        finally:
            field.null = original_null

    def test_nullable_one_to_one_default_mode_can_return_none_with_related_available(
        self,
    ):
        created = DummyForeignKey2(name="created")
        existing = DummyForeignKey2(id=1, name="existing")
        field = DummyModel._meta.get_field("dummy_one_to_one")
        original_null = field.null

        class GMC2:
            pass

        def factory(**_kwargs):
            self.fail("default nullable relation generation should return None first")
            return created

        GMC2.Factory = factory  # type: ignore
        DummyForeignKey2._general_manager_class = GMC2  # type: ignore
        field.null = True
        try:
            with (
                patch(
                    "general_manager.factory.factories._RNG.choice",
                    return_value=True,
                ),
                patch.object(
                    DummyForeignKey2.objects,
                    "all",
                    return_value=[existing],
                ),
            ):
                self.assertIsNone(get_field_value(field))
        finally:
            field.null = original_null

    def test_nullable_fk_default_none_create_mode_creates_new_instance(self):
        created = DummyForeignKey(name="created")
        field = DummyModel._meta.get_field("dummy_fk")
        original_null = field.null
        original_default = field.default

        class GMC:
            pass

        GMC.Factory = lambda **_kwargs: created  # type: ignore
        DummyForeignKey._general_manager_class = GMC  # type: ignore
        field.null = True
        field.default = None
        try:
            self.assertIs(
                get_field_value(field, relation_generation="create"),
                created,
            )
        finally:
            field.null = original_null
            field.default = original_default

    def test_one_to_one_with_factory_prefers_existing_instances(self):
        created = DummyForeignKey2(name="created")
        existing1 = DummyForeignKey2(id=1, name="x")
        existing2 = DummyForeignKey2(id=2, name="y")

        class GMC2:
            pass

        GMC2.Factory = lambda **_kwargs: created  # type: ignore
        DummyForeignKey2._general_manager_class = GMC2  # type: ignore

        field = DummyModel._meta.get_field("dummy_one_to_one")
        with patch.object(
            DummyForeignKey2.objects,
            "all",
            return_value=[existing1, existing2],
        ):
            decl = get_field_value(field)
        self.assertIsInstance(decl, LazyFunction)
        selected = decl.evaluate(None, None, None)  # type: ignore
        self.assertIn(selected, (existing1, existing2))
        self.assertIsNot(selected, created)

    def test_one_to_one_reuse_excludes_already_linked_instances(self):
        from general_manager.factory.factories import _existing_related_instances

        available = DummyForeignKey2(id=1, name="free")
        linked = DummyForeignKey2(id=2, name="used")
        also_available = DummyForeignKey2(id=3, name="free2")
        related_manager = Mock()
        related_manager.all.return_value = [available, linked, also_available]
        owner_manager = Mock()
        owner_rows = owner_manager.exclude.return_value
        owner_rows.values_list.return_value = [linked.pk]
        owner_manager.values_list.side_effect = AssertionError(
            "linked ids must come from the filtered owner queryset"
        )
        field = SimpleNamespace(
            name="dummy_one_to_one",
            attname="dummy_one_to_one_id",
            one_to_one=True,
            model=SimpleNamespace(objects=owner_manager),
            related_model=SimpleNamespace(objects=related_manager),
        )

        result = _existing_related_instances(field)

        self.assertEqual(result, [available, also_available])
        related_manager.all.assert_called_once_with()
        owner_manager.exclude.assert_called_once()
        owner_rows.values_list.assert_called_once_with("dummy_one_to_one_id", flat=True)

    def test_existing_related_instances_uses_default_manager_for_non_one_to_one(self):
        from general_manager.factory.factories import _existing_related_instances

        existing = [DummyForeignKey(id=1, name="available")]
        default_manager = Mock()
        default_manager.all.return_value = existing
        objects_manager = Mock()
        objects_manager.all.return_value = []
        field = SimpleNamespace(
            name="dummy_fk",
            one_to_one=False,
            related_model=SimpleNamespace(
                _default_manager=default_manager,
                objects=objects_manager,
            ),
        )

        self.assertEqual(_existing_related_instances(field), existing)
        default_manager.all.assert_called_once_with()
        objects_manager.all.assert_not_called()

    def test_existing_related_instances_uses_default_manager_for_one_to_one_owner(
        self,
    ):
        from general_manager.factory.factories import _existing_related_instances

        available = DummyForeignKey2(id=1, name="available")
        linked = DummyForeignKey2(id=2, name="linked")
        related_default_manager = Mock()
        related_default_manager.all.return_value = [available, linked]
        related_objects_manager = Mock()
        related_objects_manager.all.return_value = []
        owner_default_manager = Mock()
        owner_rows = owner_default_manager.exclude.return_value
        owner_rows.values_list.return_value = [linked.pk]
        owner_objects_manager = Mock()
        owner_objects_manager.exclude.return_value.values_list.return_value = []
        field = SimpleNamespace(
            name="dummy_one_to_one",
            attname="dummy_one_to_one_id",
            one_to_one=True,
            model=SimpleNamespace(
                _default_manager=owner_default_manager,
                objects=owner_objects_manager,
            ),
            related_model=SimpleNamespace(
                _default_manager=related_default_manager,
                objects=related_objects_manager,
            ),
        )

        self.assertEqual(_existing_related_instances(field), [available])
        related_default_manager.all.assert_called_once_with()
        related_objects_manager.all.assert_not_called()
        owner_default_manager.exclude.assert_called_once()
        owner_rows.values_list.assert_called_once_with("dummy_one_to_one_id", flat=True)
        owner_objects_manager.exclude.assert_not_called()

    def test_one_to_one_reuse_excludes_linked_to_field_value(self):
        from general_manager.factory.factories import _existing_related_instances

        available = SimpleNamespace(pk=1, code="free")
        linked = SimpleNamespace(pk=2, code="used")
        also_available = SimpleNamespace(pk=3, code="free2")
        related_manager = Mock()
        related_manager.all.return_value = [available, linked, also_available]
        owner_manager = Mock()
        owner_rows = owner_manager.exclude.return_value
        owner_rows.values_list.return_value = ["used"]
        target_field = SimpleNamespace(attname="code", name="code")
        field = SimpleNamespace(
            name="dummy_one_to_one",
            attname="dummy_one_to_one_code",
            one_to_one=True,
            target_field=target_field,
            model=SimpleNamespace(objects=owner_manager),
            related_model=SimpleNamespace(objects=related_manager),
        )

        self.assertEqual(
            _existing_related_instances(field),
            [available, also_available],
        )

    def test_one_to_one_owner_scan_prefers_base_manager_for_linked_values(self):
        from general_manager.factory.factories import _existing_related_instances

        available = DummyForeignKey2(id=1, name="available")
        linked = DummyForeignKey2(id=2, name="linked")
        related_default_manager = Mock()
        related_default_manager.all.return_value = [available, linked]
        owner_base_manager = Mock()
        owner_rows = owner_base_manager.exclude.return_value
        owner_rows.values_list.return_value = [linked.pk]
        owner_default_manager = Mock()
        owner_default_manager.exclude.return_value.values_list.return_value = []
        field = SimpleNamespace(
            name="dummy_one_to_one",
            attname="dummy_one_to_one_id",
            one_to_one=True,
            model=SimpleNamespace(
                _base_manager=owner_base_manager,
                _default_manager=owner_default_manager,
            ),
            related_model=SimpleNamespace(
                _default_manager=related_default_manager,
            ),
        )

        self.assertEqual(_existing_related_instances(field), [available])
        related_default_manager.all.assert_called_once_with()
        owner_base_manager.exclude.assert_called_once()
        owner_rows.values_list.assert_called_once_with("dummy_one_to_one_id", flat=True)
        owner_default_manager.exclude.assert_not_called()

    def test_fk_without_factory_with_existing_instances(self):
        # 2) No factory but existing objects → expect LazyFunction
        dummy1 = DummyForeignKey(name="a")
        dummy2 = DummyForeignKey(name="b")
        field = DummyModel._meta.get_field("dummy_fk")

        with patch.object(
            DummyForeignKey.objects, "all", return_value=[dummy1, dummy2]
        ):
            decl = get_field_value(field)
            self.assertIsInstance(decl, LazyFunction)
            # Evaluating the declaration should return one of the existing objects
            inst = decl.evaluate(None, None, None)  # type: ignore
            self.assertIn(inst, (dummy1, dummy2))

    def test_one_to_one_without_factory_with_existing_instances(self):
        dummy1 = DummyForeignKey2(name="x")
        dummy2 = DummyForeignKey2(name="y")
        field = DummyModel._meta.get_field("dummy_one_to_one")

        with patch.object(
            DummyForeignKey2.objects, "all", return_value=[dummy1, dummy2]
        ):
            decl = get_field_value(field)
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
            get_field_value(field)

    def test_one_to_one_without_factory_and_no_instances_raises(self):
        field = DummyModel._meta.get_field("dummy_one_to_one")
        with (
            patch.object(DummyForeignKey2.objects, "all", return_value=[]),
            self.assertRaisesMessage(
                ValueError, "No factory found for DummyForeignKey2"
            ),
        ):
            get_field_value(field)


class TestGetManyToManyFieldValue(TestCase):
    def setUp(self):
        # tidy up: no _general_manager_class assumed
        for M in (DummyManyToMany, DummyModel):
            if hasattr(M, "_general_manager_class"):
                delattr(M, "_general_manager_class")

    def test_m2m_with_factory_and_existing(self):
        """
        Verifies that get_many_to_many_field_value returns a list containing only existing related instances when a related-model factory is available.
        Sets up a factory that produces one instance and an existing queryset of two instances, then asserts the result is a list and every returned item is one of the existing instances.
        """
        dummy1 = DummyManyToMany(name="foo", id=1)
        dummy2 = DummyManyToMany(name="bar", id=2)
        created = DummyManyToMany(name="created", id=3)
        factory_calls = 0

        class GMC:
            pass

        def factory(**_kwargs):
            nonlocal factory_calls
            factory_calls += 1
            return created

        GMC.Factory = factory  # type: ignore
        DummyManyToMany._general_manager_class = GMC  # type: ignore

        field = DummyModel._meta.get_field("dummy_m2m")
        original_blank = field.blank
        field.blank = False
        try:
            with patch.object(
                field.related_model.objects,
                "all",
                return_value=[dummy1, dummy2],  # type: ignore
            ):
                result = get_many_to_many_field_value(field)  # type: ignore
        finally:
            field.blank = original_blank

        self.assertEqual(factory_calls, 0)
        self.assertIsInstance(result, list)
        self.assertTrue(
            set(result).issubset({dummy1, dummy2}),
            "Returned instances are not a subset of existing objects",
        )

    def test_m2m_create_mode_uses_factory_when_existing_rows_are_available(self):
        created = DummyManyToMany(name="created", id=2)
        factory_calls = 0

        class GMC:
            pass

        def factory(**_kwargs):
            nonlocal factory_calls
            factory_calls += 1
            return created

        GMC.Factory = factory  # type: ignore
        DummyManyToMany._general_manager_class = GMC  # type: ignore

        field = DummyModel._meta.get_field("dummy_m2m")
        with (
            patch(
                "general_manager.factory.factories._existing_related_instances",
                side_effect=AssertionError("create mode must not query existing rows"),
            ) as existing_related_instances,
            patch(
                "general_manager.factory.factories._RNG.randint",
                return_value=1,
            ),
        ):
            result = get_many_to_many_field_value(
                field,  # type: ignore[arg-type]
                relation_generation="create",
            )

        self.assertEqual(factory_calls, 1)
        existing_related_instances.assert_not_called()
        self.assertEqual(result, [created])

    def test_m2m_existing_rows_use_database_alias(self):
        dummy1 = DummyManyToMany(name="foo", id=1)
        dummy2 = DummyManyToMany(name="bar", id=2)
        alias = "factory_alias"
        alias_manager = Mock()
        alias_manager.all.return_value = [dummy1, dummy2]

        field = DummyModel._meta.get_field("dummy_m2m")
        with (
            patch.object(
                field.related_model._default_manager,
                "using",
                return_value=alias_manager,
            ) as using,
            patch(
                "general_manager.factory.factories._RNG.randint",
                return_value=1,
            ),
        ):
            result = get_many_to_many_field_value(
                field,  # type: ignore[arg-type]
                database_alias=alias,
            )

        using.assert_called_once_with(alias)
        alias_manager.all.assert_called_once_with()
        self.assertEqual(len(result), 1)
        self.assertIn(result[0], (dummy1, dummy2))

    def test_m2m_with_factory(self):
        dummy1 = DummyManyToMany(name="foo", id=1)

        class GMC:
            pass

        GMC.Factory = lambda **_kwargs: dummy1  # type: ignore
        DummyManyToMany._general_manager_class = GMC  # type: ignore

        field = DummyModel._meta.get_field("dummy_m2m")
        with patch.object(field.related_model.objects, "all", return_value=[]):  # type: ignore
            result = get_many_to_many_field_value(field)  # type: ignore
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
            result = get_many_to_many_field_value(field)  # type: ignore
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
            get_many_to_many_field_value(field)  # type: ignore

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
            get_field_value(field)

        self.assertIn("No factory found", str(ctx.exception))
        self.assertIn("OrphanModel", str(ctx.exception))
        self.assertIn("no instances found", str(ctx.exception))

    def test_missing_related_model_error(self):
        """Test that MissingRelatedModelError is raised when related model is None."""
        from general_manager.factory.factories import (
            MissingRelatedModelError,
            get_related_model,
        )

        field = Mock()
        field.related_model = None
        field.name = "test_field"

        with self.assertRaises(MissingRelatedModelError) as ctx:
            get_related_model(field)

        self.assertIn("test_field", str(ctx.exception))
        self.assertIn("does not have a related model", str(ctx.exception))

    def test_missing_related_model_error_when_field_lacks_related_model(self):
        """Non-relational fields should raise the documented custom error."""
        from general_manager.factory.factories import (
            MissingRelatedModelError,
            get_related_model,
        )

        field = SimpleNamespace(name="plain_field", model=DummyModel)

        with self.assertRaises(MissingRelatedModelError) as ctx:
            get_related_model(field)  # type: ignore[arg-type]

        self.assertIn("plain_field", str(ctx.exception))

    def test_invalid_related_model_type_error(self):
        """Test that InvalidRelatedModelTypeError is raised for non-model related types."""
        from general_manager.factory.factories import (
            InvalidRelatedModelTypeError,
            get_related_model,
        )

        field = Mock()
        field.related_model = "NotAModel"  # String instead of model class
        field.name = "test_field"

        with self.assertRaises(InvalidRelatedModelTypeError) as ctx:
            get_related_model(field)

        self.assertIn("test_field", str(ctx.exception))
        self.assertIn("must be a Django model class", str(ctx.exception))

    def test_get_related_model_resolves_dotted_string_reference(self):
        """String relation references should resolve through Django's app registry."""
        from general_manager.factory.factories import get_related_model

        field = Mock()
        field.related_model = "financials.AccountNumber"
        field.name = "project_number"

        with patch(
            "django.apps.apps.get_model", return_value=DummyForeignKey
        ) as get_model:
            self.assertIs(get_related_model(field), DummyForeignKey)

        get_model.assert_called_once_with("financials", "AccountNumber")

    def test_nullable_relation_with_default_none_returns_none_before_resolution(self):
        """Nullable relation fields with default=None should not require resolving a related model."""
        from general_manager.factory.factories import get_field_value

        field = Mock()
        field.related_model = "financials.AccountNumber"
        field.name = "project_number"
        field.null = True
        field.default = None

        def custom_isinstance(obj, cls):
            if cls == models.OneToOneField:
                return True
            return isinstance(obj, cls)

        with (
            patch("general_manager.factory.factories._RNG.choice", return_value=False),
            patch(
                "general_manager.factory.factories.isinstance",
                side_effect=custom_isinstance,
            ),
            patch("django.apps.apps.get_model") as get_model,
        ):
            self.assertIsNone(get_field_value(field))

        get_model.assert_not_called()

    def test_nullable_foreign_key_without_factory_returns_none(self):
        """Nullable foreign keys without factories or instances should fall back to None."""
        from general_manager.factory.factories import get_field_value
        from django.db import models

        class OptionalModel(models.Model):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "test_app"

        field = Mock()
        field.related_model = OptionalModel
        field.null = True
        field.name = "optional_fk"

        def custom_isinstance(obj, cls):
            if cls == models.ForeignKey:
                return True
            return isinstance(obj, cls)

        with (
            patch.object(OptionalModel.objects, "all", return_value=[]),
            patch(
                "general_manager.factory.factories.isinstance",
                side_effect=custom_isinstance,
            ),
        ):
            self.assertIsNone(get_field_value(field))

    def test_field_value_with_null_field_randomness(self):
        """Test that nullable fields sometimes return None with proper randomness."""
        from general_manager.factory.factories import get_field_value

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
                result = get_field_value(field)
                if result is None:
                    none_count += 1

        # Should get some None values but not all (roughly 10% based on the code)
        self.assertGreater(none_count, 0)
        self.assertLess(none_count, total_runs)

    def test_measurement_field_value_generation(self):
        """Test that MeasurementField generates valid Measurement values."""
        from general_manager.factory.factories import get_field_value
        from general_manager.measurement.measurement_field import MeasurementField
        from decimal import Decimal

        field = MeasurementField(base_unit="meter")
        field.null = False

        result = get_field_value(field)

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
        from general_manager.factory.factories import get_many_to_many_field_value

        # Create mock instances
        instances = [f"instance_{i}" for i in range(10)]

        field = Mock()
        field.blank = False

        from types import SimpleNamespace

        related_model = SimpleNamespace(__name__="DummyRelatedModel")
        related_model.objects = Mock()
        related_model.objects.all.return_value = instances  # type: ignore

        with patch(
            "general_manager.factory.factories.get_related_model",
            return_value=related_model,
        ):
            result = get_many_to_many_field_value(field)

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
        from general_manager.factory.factories import get_field_value
        from django.core.validators import RegexValidator

        # Create a CharField with RegexValidator
        validator = RegexValidator(regex=r"^\d{3}-\d{3}-\d{4}$")
        field = models.CharField(max_length=12, validators=[validator])
        field.null = False

        result = get_field_value(field)  # type: ignore[arg-type]

        # Should return a LazyFunction with Faker
        self.assertIsNotNone(result)

        # If it's a LazyFunction, it should generate a value matching the regex
        # The actual value generation happens at evaluation time
        if hasattr(result, "evaluate"):
            generated = result.evaluate(None, None, None)
            self.assertRegex(generated, validator.regex.pattern)

    def test_choice_field_value_generation(self):
        """Test that fields with choices generate valid choice values."""
        from general_manager.factory.factories import get_field_value

        choices = [("A", "Option A"), ("B", "Option B"), ("C", "Option C")]

        field = models.CharField(max_length=1, choices=choices)
        field.null = False

        result = get_field_value(field)  # type: ignore[arg-type]

        # Should return a value appropriate for CharField with choices
        self.assertIsNotNone(result)

        # Execute multiple times to check it picks from choices
        if hasattr(result, "evaluate"):
            for _ in range(10):
                value = result.evaluate(None, None, {"locale": "en_US"})
                self.assertIsInstance(value, str)
                self.assertIn(value, [choice[0] for choice in choices])

    def test_system_random_usage(self):
        """Test that SystemRandom is used instead of standard random module."""
        from general_manager.factory import factories
        from random import SystemRandom

        # Check that _RNG is a SystemRandom instance
        self.assertIsInstance(factories._RNG, SystemRandom)

    def test_decimal_field_value_generation(self):
        """Test that DecimalField generates valid Decimal values."""
        from general_manager.factory.factories import get_field_value
        from decimal import Decimal

        field = models.DecimalField(max_digits=10, decimal_places=2)
        field.null = False

        result = get_field_value(field)  # type: ignore[arg-type]

        # Should return a Faker instance
        self.assertIsNotNone(result)
        if hasattr(result, "evaluate"):
            value = result.evaluate(None, None, {"locale": "en_US"})
            self.assertIsInstance(value, Decimal)
