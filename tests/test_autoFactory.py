from django.test import TransactionTestCase
from django.db import models, connection
from general_manager.factory.autoFactory import AutoFactory
from typing import Any, Iterable


class DummyInterface:
    """
    A dummy interface for testing purposes.
    This should be replaced with an actual interface in real use cases.
    """

    @classmethod
    def handleCustomFields(cls, model):
        """
        Returns empty lists for custom field handling.
        
        This method serves as a placeholder for custom field processing logic and is intended to be overridden in a real interface implementation.
        
        Args:
            model: The model class for which custom fields would be handled.
        
        Returns:
            A tuple of two empty lists.
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
        with connection.schema_editor() as schema:
            schema.create_model(DummyModel)
            schema.create_model(DummyModel2)

    @classmethod
    def tearDownClass(cls):
        """
        Deletes the database tables for DummyModel and DummyModel2 after all tests in the class have run.
        """
        super().tearDownClass()
        with connection.schema_editor() as schema:
            schema.delete_model(DummyModel)
            schema.delete_model(DummyModel2)

    def setUp(self) -> None:
        """
        Sets up factory classes for dummy models before each test.
        
        Dynamically creates and assigns factory classes for `DummyModel` and `DummyModel2`, each using `DummyInterface`, to be used in test methods.
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
        Tests that the factory creates a batch of DummyModel instances with populated fields.
        
        Verifies that calling `create_batch(5)` produces five DummyModel instances, each with non-null `name` and `value` attributes.
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
        Tests that the factory's build method creates a DummyModel instance without saving it to the database.
        
        Verifies that the instance has non-null 'name' and 'value' fields, no primary key, and that no DummyModel records exist in the database after building.
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
        Tests that DummyFactory2 can create a DummyModel2 instance with a specified related DummyModel and custom description.
        
        Verifies that the related ForeignKey field is correctly assigned and the description is set as expected.
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
        Tests that the factory can generate multiple instances using a custom generate function that returns a list of dictionaries, verifying correct instance creation, attribute assignment, and database persistence.
        """

        def custom_generate_function(**kwargs: dict[str, Any]) -> list[dict[str, Any]]:
            """
            Generates a list of dictionaries with sequential squared values.
            
            Each dictionary contains a 'name' field set to "Generated Name" and a 'value' field set to the square of its index, for indices 0 through 100.
            
            Returns:
                A list of 101 dictionaries with 'name' and 'value' keys.
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
        Tests that the factory can generate a single instance using a custom generate function that returns a dictionary, verifying correct assignment of generated and provided field values for both create and build operations.
        """

        def custom_generate_function(**kwargs: dict[str, Any]) -> dict[str, Any]:
            """
            Generates a dictionary of model field values with a preset name.
            
            Merges provided keyword arguments with a fixed "name" field set to "Generated Name".
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
