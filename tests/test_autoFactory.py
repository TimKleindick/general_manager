from django.test import TransactionTestCase
from django.db import models, connection
from general_manager.factory.autoFactory import AutoFactory
from typing import Any


class DummyInterface:
    """
    A dummy interface for testing purposes.
    This should be replaced with an actual interface in real use cases.
    """

    @classmethod
    def handleCustomFields(cls, model):
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
        super().setUpClass()
        with connection.schema_editor() as schema:
            schema.create_model(DummyModel)
            schema.create_model(DummyModel2)

    def setUp(self) -> None:
        factory_attributes = {}
        factory_attributes["interface"] = DummyInterface
        factory_attributes["Meta"] = type("Meta", (), {"model": DummyModel})
        self.factory_class = type(f"DummyFactory", (AutoFactory,), factory_attributes)

        factory_attributes = {}
        factory_attributes["interface"] = DummyInterface
        factory_attributes["Meta"] = type("Meta", (), {"model": DummyModel2})
        self.factory_class2 = type("DummyFactory2", (AutoFactory,), factory_attributes)

    def test_generate_instance(self):
        """
        Test that the factory can generate an instance of DummyModel.
        """
        instance = self.factory_class.create()
        self.assertIsInstance(instance, DummyModel)
        self.assertIsNotNone(instance.name)
        self.assertIsNotNone(instance.value)

    def test_generate_multiple_instances(self):
        """
        Test that the factory can generate multiple instances of DummyModel.
        """
        instances = self.factory_class.create_batch(5)
        self.assertEqual(len(instances), 5)
        for instance in instances:
            self.assertIsInstance(instance, DummyModel)
            self.assertIsNotNone(instance.name)
            self.assertIsNotNone(instance.value)

    def test_generate_instance_with_custom_fields(self):
        """
        Test that the factory can generate an instance with custom fields.
        """
        custom_name = "Custom Name"
        custom_value = 42
        instance = self.factory_class.create(name=custom_name, value=custom_value)
        self.assertEqual(instance.name, custom_name)
        self.assertEqual(instance.value, custom_value)

    def test_build_instance(self):
        """
        Test that the factory can build an instance of DummyModel without saving it.
        """
        instance = self.factory_class.build()
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
        Test that the factory can generate an instance of DummyModel2 with related fields.
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
        Test that the factory can generate an instance of DummyModel2 with ManyToMany fields.
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
        Test that the factory can generate an instance using a custom generate function.
        """

        def custom_generate_function(**kwargs: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {
                    "name": "Generated Name",
                    "value": i * i,
                }
                for i in range(101)
            ]

        self.factory_class._adjustmentMethod = custom_generate_function
        instance = self.factory_class.build()
        self.assertEqual(len(instance), 101)
        self.assertIsInstance(instance[0], DummyModel)
        self.assertEqual(instance[0].name, "Generated Name")
        self.assertEqual(instance[0].value, 0)
        self.assertEqual(instance[1].name, "Generated Name")
        self.assertEqual(instance[100].value, 10_000)
        instance = self.factory_class.create()

    def test_generate_instance_with_generate_function_for_one_entry(self):
        """
        Test that the factory can generate an instance using a custom generate function.
        """

        def custom_generate_function(**kwargs: dict[str, Any]) -> dict[str, Any]:
            return {
                **kwargs,
                "name": "Generated Name",
            }

        self.factory_class._adjustmentMethod = custom_generate_function
        instance = self.factory_class.create(value=1)
        self.assertIsInstance(instance, DummyModel)
        self.assertEqual(instance.name, "Generated Name")
        self.assertEqual(instance.value, 1)

        instance = self.factory_class.build(value=2)
        self.assertIsInstance(instance, DummyModel)
        self.assertEqual(instance.name, "Generated Name")
        self.assertEqual(instance.value, 2)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        with connection.schema_editor() as schema:
            schema.delete_model(DummyModel)
