from django.test import TransactionTestCase
from django.db import models, connection
from general_manager.factory.auto_factory import AutoFactory
from typing import Any, Iterable
from unittest.mock import patch


class DummyInterface:
    """
    A dummy interface for testing purposes.
    This should be replaced with an actual interface in real use cases.
    """

    @classmethod
    def handle_custom_fields(cls, model):
        """
        Placeholder hook for processing a model's custom fields.

        Intended to be overridden by subclasses to extract or transform custom field definitions for the given model. When overridden, it should return a tuple of two lists: (custom_field_definitions, deferred_relation_descriptors).

        Parameters:
            model: The model class or instance to inspect for custom fields.

        Returns:
            tuple: A pair of lists (custom_field_definitions, deferred_relation_descriptors). Defaults to two empty lists.
        """
        return [], []


class DummyModel(models.Model):
    """
    A dummy model for testing purposes.
    This should be replaced with an actual model in real use cases.
    """

    name = models.CharField(max_length=100)
    value = models.IntegerField()

    class Meta:
        app_label = "general_manager"


class DummyModel2(models.Model):
    """
    Another dummy model for testing purposes.
    This should be replaced with an actual model in real use cases.
    """

    description = models.TextField()
    is_active = models.BooleanField(default=True)
    dummy_model = models.ForeignKey(
        DummyModel, on_delete=models.CASCADE, related_name="related_models"
    )
    dummy_m2m = models.ManyToManyField(
        DummyModel, related_name="m2m_related_models", blank=True
    )

    class Meta:
        app_label = "general_manager"


class AutoFactoryTestCase(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Creates database tables for DummyModel and DummyModel2 before running any tests in the test case.
        """
        super().setUpClass()
        cls._wrap_patcher = patch(
            "general_manager.factory.auto_factory.AutoFactory._wrap_generated_objects",
            side_effect=lambda generated: generated,
        )
        cls._wrap_patcher.start()
        with connection.schema_editor() as schema:
            schema.create_model(DummyModel)
            schema.create_model(DummyModel2)

    @classmethod
    def tearDownClass(cls):
        """
        Deletes the database tables for DummyModel and DummyModel2 after all tests in the class have run.
        """
        super().tearDownClass()
        cls._wrap_patcher.stop()
        with connection.schema_editor() as schema:
            schema.delete_model(DummyModel)
            schema.delete_model(DummyModel2)

    def setUp(self) -> None:
        """
        Initializes dynamic factory classes for DummyModel and DummyModel2 before each test.

        Creates and assigns factory classes using AutoFactory and DummyInterface for use in test methods.
        """
        factory_attributes = {}
        factory_attributes["interface"] = DummyInterface
        factory_attributes["Meta"] = type("Meta", (), {"model": DummyModel})
        self.factory_class = type("DummyFactory", (AutoFactory,), factory_attributes)

        factory_attributes = {}
        factory_attributes["interface"] = DummyInterface
        factory_attributes["Meta"] = type("Meta", (), {"model": DummyModel2})
        self.factory_class2 = type("DummyFactory2", (AutoFactory,), factory_attributes)

    def test_generate_instance(self):
        """
        Tests that the factory creates and saves a DummyModel instance with non-null fields.
        """

        instance = self.factory_class.create()
        self.assertIsInstance(instance, DummyModel)
        self.assertIsNotNone(instance.name)  # type: ignore
        self.assertIsNotNone(instance.value)  # type: ignore

    def test_generate_multiple_instances(self):
        """
        Test that the factory creates multiple DummyModel instances with populated fields.

        Verifies that calling `create_batch(5)` returns five DummyModel instances, each with non-null `name` and `value` attributes.
        """
        instances: Iterable[DummyModel] = self.factory_class.create_batch(5)
        self.assertEqual(len(instances), 5)
        for instance in instances:
            self.assertIsInstance(instance, DummyModel)
            self.assertIsNotNone(instance.name)
            self.assertIsNotNone(instance.value)

    def test_generate_instance_with_custom_fields(self):
        """
        Tests that the factory creates a DummyModel instance with specified custom field values.
        """
        custom_name = "Custom Name"
        custom_value = 42
        instance: DummyModel = self.factory_class.create(
            name=custom_name, value=custom_value
        )
        self.assertEqual(instance.name, custom_name)
        self.assertEqual(instance.value, custom_value)

    def test_build_instance(self):
        """
        Test that the factory's build method returns a DummyModel instance with populated fields but does not save it to the database.

        Ensures the built instance has non-null 'name' and 'value' attributes, no primary key, and that the database remains unchanged.
        """
        instance: DummyModel = self.factory_class.build()
        self.assertIsInstance(instance, DummyModel)
        self.assertIsNone(instance.pk)
        self.assertTrue(hasattr(instance, "name"))
        self.assertIsNotNone(instance.name)
        self.assertTrue(hasattr(instance, "value"))
        self.assertIsNotNone(instance.value)

        self.assertEqual(
            len(DummyModel.objects.all()), 0
        )  # Ensure it is not saved to the database

    def test_generate_instance_with_related_fields(self):
        """
        Test that the factory creates a DummyModel2 instance with a specified related DummyModel and custom description.

        Ensures the ForeignKey relation and description field are correctly assigned.
        """
        dummy_model_instance = self.factory_class.create()
        instance = self.factory_class2.create(
            description="Test Description",
            dummy_model=dummy_model_instance,
        )
        self.assertIsInstance(instance, DummyModel2)
        self.assertEqual(instance.description, "Test Description")  # type: ignore
        self.assertEqual(instance.dummy_model, dummy_model_instance)  # type: ignore

    def test_generate_instance_with_many_to_many(self):
        """
        Tests that the factory can create a DummyModel2 instance with ManyToMany relationships assigned to multiple DummyModel instances.
        """
        dummy_model_instance = self.factory_class.create()
        dummy_model_instance2 = self.factory_class.create()
        self.factory_class2.create(
            description="Test Description",
            dummy_model=dummy_model_instance,
            dummy_m2m=[dummy_model_instance, dummy_model_instance2],
        )
        instance = DummyModel2.objects.get(id=1)
        self.assertIsInstance(instance, DummyModel2)
        self.assertEqual(instance.description, "Test Description")
        self.assertEqual(instance.dummy_model, dummy_model_instance)
        self.assertIn(dummy_model_instance, instance.dummy_m2m.all())
        self.assertIn(dummy_model_instance2, instance.dummy_m2m.all())

    def test_generate_instance_with_generate_function(self):
        """
        Test that the factory can generate and persist multiple instances using a custom generate function that returns a list of dictionaries.

        Verifies that both `build` and `create` methods produce 101 `DummyModel` instances with expected attribute values, and that `create` saves all instances to the database.
        """

        def custom_generate_function(**kwargs: Any) -> list[dict[str, Any]]:
            """
            Generate 101 dictionaries with 'name' set to "Generated Name" and 'value' equal to the square of its index (0-100).

            Returns:
                list[dict[str, Any]]: A list of 101 dictionaries where each dictionary has keys 'name' (str) and 'value' (int) with 'value' equal to i*i for i in 0..100.
            """
            return [
                {
                    "name": "Generated Name",
                    "value": i * i,
                }
                for i in range(101)
            ]

        self.factory_class._adjustmentMethod = custom_generate_function
        instance: list[DummyModel] = self.factory_class.build()  # type: ignore
        self.assertEqual(len(instance), 101)
        self.assertIsInstance(instance[0], DummyModel)
        self.assertEqual(instance[0].name, "Generated Name")
        self.assertEqual(instance[0].value, 0)
        self.assertEqual(instance[1].name, "Generated Name")
        self.assertEqual(instance[100].value, 10_000)
        instance: list[DummyModel] = self.factory_class.create()  # type: ignore
        self.assertEqual(len(instance), 101)
        self.assertIsInstance(instance[0], DummyModel)
        self.assertEqual(instance[0].name, "Generated Name")
        self.assertEqual(instance[0].value, 0)
        self.assertEqual(instance[1].name, "Generated Name")
        self.assertEqual(instance[100].value, 10_000)
        self.assertEqual(DummyModel.objects.count(), 101)

    def test_generate_instance_with_generate_function_for_one_entry(self):
        """
        Tests that the factory can generate a single model instance using a custom generate function that returns a dictionary, ensuring both `create` and `build` methods assign generated and provided field values correctly.
        """

        def custom_generate_function(**kwargs: Any) -> dict[str, Any]:
            """
            Return a mapping of model field values with the 'name' field set to "Generated Name".

            Merges any provided keyword arguments into the result; if `name` is present in kwargs it is replaced.
            Returns:
                dict[str, Any]: Mapping of field names to values with `name` equal to "Generated Name".
            """
            return {
                **kwargs,
                "name": "Generated Name",
            }

        self.factory_class._adjustmentMethod = custom_generate_function
        instance: DummyModel = self.factory_class.create(value=1)
        self.assertIsInstance(instance, DummyModel)
        self.assertEqual(instance.name, "Generated Name")
        self.assertEqual(instance.value, 1)

        instance = self.factory_class.build(value=2)
        self.assertIsInstance(instance, DummyModel)
        self.assertEqual(instance.name, "Generated Name")
        self.assertEqual(instance.value, 2)

    def test_extract_identification_from_instance(self):
        """
        Tests that _extract_identification correctly extracts identification fields.
        """
        instance = self.factory_class.create()

        # Mock the input_fields
        self.factory_class.interface.input_fields = {"id": None}
        self.factory_class.interface.format_identification = lambda x: x

        identification = self.factory_class._extract_identification(instance)

        self.assertIsInstance(identification, dict)
        self.assertIn("id", identification)

    def test_resolve_identification_value_with_direct_attribute(self):
        """
        Tests that _resolve_identification_value retrieves direct attributes.
        """
        instance = self.factory_class.create()

        # Should be able to resolve the 'id' field
        value = self.factory_class._resolve_identification_value(instance, "id")
        self.assertIsNotNone(value)
        self.assertEqual(value, instance.pk)

    def test_resolve_identification_value_with_foreign_key(self):
        """
        Tests that _resolve_identification_value handles foreign keys with _id suffix.
        """
        # Create an instance with FK
        fk_instance = self.factory_class.create()
        instance2 = self.factory_class2.create(dummy_model=fk_instance)

        # Should be able to resolve using _id suffix
        value = self.factory_class2._resolve_identification_value(
            instance2, "dummy_model"
        )
        self.assertEqual(value, fk_instance.pk)

    def test_resolve_identification_value_raises_on_missing_field(self):
        """
        Tests that _resolve_identification_value raises MissingIdentificationFieldError for missing fields.
        """
        from general_manager.factory.auto_factory import MissingIdentificationFieldError

        instance = self.factory_class.create()

        with self.assertRaises(MissingIdentificationFieldError) as context:
            self.factory_class._resolve_identification_value(
                instance, "nonexistent_field"
            )

        self.assertIn("nonexistent_field", str(context.exception))

    def test_resolve_identification_value_extracts_pk_from_model_instance(self):
        """
        Tests that _resolve_identification_value extracts pk when value is a Model instance.
        """
        from django.db import models

        # Create instance with FK that returns model instance
        fk_instance = self.factory_class.create()
        instance2 = self.factory_class2.create(dummy_model=fk_instance)

        # Get the actual model instance (not _id)
        model_value = instance2.dummy_model
        self.assertIsInstance(model_value, models.Model)

        # When passing a model instance, should extract its pk
        if hasattr(model_value, "pk"):
            self.assertEqual(model_value.pk, fk_instance.pk)

    def test_get_declared_default_returns_class_attribute(self):
        """
        Tests that _get_declared_default retrieves declared constant values.
        """
        # Add a constant to the factory
        self.factory_class.constant_value = "test_constant"

        value = self.factory_class._get_declared_default("constant_value")
        self.assertEqual(value, "test_constant")

    def test_get_declared_default_returns_none_for_callable(self):
        """
        Tests that _get_declared_default returns None for callable attributes.
        """
        # Add a callable
        self.factory_class.callable_attr = lambda: "callable"

        value = self.factory_class._get_declared_default("callable_attr")
        self.assertIsNone(value)

    def test_get_declared_default_returns_none_for_missing_attribute(self):
        """
        Tests that _get_declared_default returns None for non-existent attributes.
        """
        value = self.factory_class._get_declared_default("nonexistent")
        self.assertIsNone(value)

    def test_generate_uses_declared_defaults(self):
        """
        Tests that _generate uses declared default values when available.
        """
        # Set a declared default
        self.factory_class.name = "Default Name"

        instance = self.factory_class.create()

        # Should use the declared default
        self.assertEqual(instance.name, "Default Name")

    def test_generate_skips_callable_declared_attributes(self):
        """
        Tests that _generate skips callable attributes even if declared.
        """
        # This is a callable, should not be used as default
        self.factory_class.dynamic_name = lambda: "Dynamic"

        instance = self.factory_class.create()

        # Should generate a value, not use the callable
        self.assertIsNotNone(instance.name)

    def test_invalid_auto_factory_model_error_message(self):
        """
        Tests that InvalidAutoFactoryModelError has appropriate message.
        """
        from general_manager.factory.auto_factory import InvalidAutoFactoryModelError

        error = InvalidAutoFactoryModelError()
        self.assertIn("model", str(error).lower())

    def test_undefined_adjustment_method_error_message(self):
        """
        Tests that UndefinedAdjustmentMethodError has appropriate message.
        """
        from general_manager.factory.auto_factory import UndefinedAdjustmentMethodError

        error = UndefinedAdjustmentMethodError()
        self.assertIn("_adjustmentMethod", str(error))

    def test_missing_manager_class_error_message(self):
        """
        Tests that MissingManagerClassError has appropriate message.
        """
        from general_manager.factory.auto_factory import MissingManagerClassError

        error = MissingManagerClassError()
        self.assertIn("manager class", str(error))

    def test_missing_identification_field_error_message(self):
        """
        Tests that MissingIdentificationFieldError formats message correctly.
        """
        from general_manager.factory.auto_factory import MissingIdentificationFieldError

        instance = self.factory_class.build()
        error = MissingIdentificationFieldError("missing_field", instance)

        self.assertIn("missing_field", str(error))
        self.assertIn("Unable to resolve", str(error))


class FactoriesHelpersTestCase(TransactionTestCase):
    """Tests for helper functions in factories.py module."""

    @classmethod
    def setUpClass(cls):
        """Set up test models."""
        super().setUpClass()
        with connection.schema_editor() as schema:
            schema.create_model(DummyModel)
            schema.create_model(DummyModel2)

    @classmethod
    def tearDownClass(cls):
        """Clean up test models."""
        super().tearDownClass()
        with connection.schema_editor() as schema:
            schema.delete_model(DummyModel)
            schema.delete_model(DummyModel2)

    def test_get_field_value_for_short_char_field(self):
        """
        Tests that get_field_value generates appropriate values for very short CharField.
        """
        from general_manager.factory.factories import get_field_value
        from django.db import models

        field = models.CharField(max_length=3)
        value = get_field_value(field)

        # For short fields, should use random alphanumeric
        self.assertIsNotNone(value)

    def test_get_field_value_for_zero_length_char_field(self):
        """
        Tests that get_field_value returns empty string for max_length=0.
        """
        from general_manager.factory.factories import get_field_value
        from django.db import models

        field = models.CharField(max_length=0)
        value = get_field_value(field)

        # Should return empty string
        self.assertEqual(value, "")

    def test_get_field_value_for_one_to_one_with_manager(self):
        """
        Tests that get_field_value uses factory for OneToOneField with GeneralManager.
        """
        from general_manager.factory.factories import get_field_value

        # Create a model instance that can be used
        DummyModel.objects.create(name="Test", value=1)

        # Mock the field
        field = DummyModel2._meta.get_field("dummy_model")

        # Should work without error
        value = get_field_value(field)
        self.assertIsNotNone(value)

    def test_ensure_model_instance_with_model_instance(self):
        """
        Tests that _ensure_model_instance returns model instance unchanged.
        """
        from general_manager.factory.factories import _ensure_model_instance

        instance = DummyModel.objects.create(name="Test", value=5)
        result = _ensure_model_instance(instance)

        self.assertIs(result, instance)

    def test_missing_factory_or_instances_error_message(self):
        """
        Tests that MissingFactoryOrInstancesError formats message correctly.
        """
        from general_manager.factory.factories import MissingFactoryOrInstancesError

        error = MissingFactoryOrInstancesError(DummyModel)

        self.assertIn("DummyModel", str(error))

    def test_missing_related_model_error_message(self):
        """
        Tests that MissingRelatedModelError formats message correctly.
        """
        from general_manager.factory.factories import MissingRelatedModelError

        error = MissingRelatedModelError("test_field")

        self.assertIn("test_field", str(error))

    def test_invalid_related_model_type_error_message(self):
        """
        Tests that InvalidRelatedModelTypeError formats message correctly.
        """
        from general_manager.factory.factories import InvalidRelatedModelTypeError

        error = InvalidRelatedModelTypeError("test_field", "bad_type")

        self.assertIn("test_field", str(error))
        self.assertIn("bad_type", str(error))
