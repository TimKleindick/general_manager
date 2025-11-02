# type: ignore
"""Unit tests for database_interface_protocols module."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db import connection, models
from django.test import TransactionTestCase
from django.apps import apps

from general_manager.interface.database_interface_protocols import (
    SupportsActivation,
    SupportsHistory,
    SupportsWrite,
)


class ProtocolTestModel(models.Model):
    """Test model for protocol validation."""

    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "general_manager"


class ProtocolsTestCase(TransactionTestCase):
    """Tests for protocol definitions."""

    @classmethod
    def setUpClass(cls):
        """
        Prepare database and app state for ProtocolTestModel tests.
        
        Registers ProtocolTestModel with simple_history, ensures the model is registered on the "general_manager"
        app, and creates database tables for both the model and its associated history model.
        """
        super().setUpClass()
        from simple_history import register

        register(ProtocolTestModel)

        if (
            ProtocolTestModel._meta.model_name
            not in apps.get_app_config("general_manager").models
        ):
            apps.register_model("general_manager", ProtocolTestModel)

        with connection.schema_editor() as schema:
            schema.create_model(ProtocolTestModel)
            schema.create_model(ProtocolTestModel.history.model)  # type: ignore[attr-defined]

    @classmethod
    def tearDownClass(cls):
        """
        Remove the test model and its history model from the database schema and application registries, then clear app caches and invoke the superclass teardown.
        
        Deletes the ProtocolTestModel and its associated history model from the database schema, removes their entries from the "general_manager" app's model registries and the global apps registry, clears Django's app cache, and calls the parent class's tearDownClass.
        """
        with connection.schema_editor() as schema:
            history_model = ProtocolTestModel.history.model  # type: ignore[attr-defined]
            schema.delete_model(history_model)
            schema.delete_model(ProtocolTestModel)

        app_config = apps.get_app_config("general_manager")
        model_key = ProtocolTestModel._meta.model_name
        history_key = ProtocolTestModel.history.model._meta.model_name  # type: ignore[attr-defined]
        apps.all_models["general_manager"].pop(model_key, None)
        apps.all_models["general_manager"].pop(history_key, None)
        app_config.models.pop(model_key, None)
        app_config.models.pop(history_key, None)
        apps.clear_cache()
        super().tearDownClass()

    def test_supports_history_protocol_recognizes_history_attribute(self):
        """
        Tests that SupportsHistory protocol recognizes models with history.
        """
        instance = ProtocolTestModel.objects.create(name="Test", is_active=True)

        # Model should support history protocol
        self.assertIsInstance(instance, SupportsHistory)
        self.assertTrue(hasattr(instance, "history"))

    def test_supports_history_protocol_rejects_without_history(self):
        """
        Tests that SupportsHistory protocol rejects models without history.
        """

        class NoHistoryModel(models.Model):
            name = models.CharField(max_length=50)

            class Meta:
                app_label = "test"

        instance = NoHistoryModel(name="No History")

        # Should not support history protocol
        self.assertNotIsInstance(instance, SupportsHistory)

    def test_supports_activation_protocol_recognizes_is_active(self):
        """
        Tests that SupportsActivation protocol recognizes models with is_active.
        """
        instance = ProtocolTestModel.objects.create(name="Active", is_active=True)

        # Model should support activation protocol
        self.assertIsInstance(instance, SupportsActivation)
        self.assertTrue(hasattr(instance, "is_active"))
        self.assertTrue(instance.is_active)

    def test_supports_activation_protocol_rejects_without_is_active(self):
        """
        Tests that SupportsActivation protocol rejects models without is_active.
        """

        class NoActivationModel(models.Model):
            name = models.CharField(max_length=50)

            class Meta:
                app_label = "test"

        instance = NoActivationModel(name="No Activation")

        # Should not support activation protocol
        self.assertNotIsInstance(instance, SupportsActivation)

    def test_supports_write_protocol_recognizes_full_methods(self):
        """
        Tests that SupportsWrite protocol recognizes models with full_clean and save.
        """
        instance = ProtocolTestModel.objects.create(name="Writable", is_active=True)

        # Model should support write protocol
        self.assertIsInstance(instance, SupportsWrite)
        self.assertTrue(hasattr(instance, "full_clean"))
        self.assertTrue(hasattr(instance, "save"))
        self.assertTrue(hasattr(instance, "pk"))
        self.assertTrue(hasattr(instance, "history"))

    def test_supports_write_protocol_methods_are_callable(self):
        """
        Tests that SupportsWrite protocol methods are actually callable.
        """
        instance = ProtocolTestModel.objects.create(name="Callable", is_active=True)

        # Should be able to call full_clean
        instance.full_clean()  # Should not raise

        # Should be able to call save
        instance.name = "Updated"
        instance.save()  # Should not raise

        # Verify the update
        instance.refresh_from_db()
        self.assertEqual(instance.name, "Updated")

    def test_history_query_supports_using(self):
        """
        Tests that history query supports using() method for database routing.
        """
        instance = ProtocolTestModel.objects.create(name="DB Routing", is_active=True)

        # Should be able to call using on history query
        history_query = instance.history.using("default")
        self.assertIsNotNone(history_query)

    def test_history_query_supports_filter(self):
        """
        Tests that history query supports filter() method.
        """
        instance = ProtocolTestModel.objects.create(name="Filterable", is_active=True)

        # Update to create history
        instance.name = "Updated"
        instance.save()

        # Should be able to filter history
        filtered = instance.history.filter(name="Filterable")
        self.assertIsNotNone(filtered)

    def test_history_query_supports_last(self):
        """
        Tests that history query supports last() method.
        """
        instance = ProtocolTestModel.objects.create(name="Historical", is_active=True)

        # Update to create history
        instance.name = "Modified"
        instance.save()

        # Should be able to get last history entry
        last_entry = instance.history.last()
        self.assertIsNotNone(last_entry)

    def test_history_query_chaining(self):
        """
        Verify that the model instance's history relation supports chaining queryset methods.
        
        Create an instance, perform multiple updates to generate history entries, then call using(...).filter(...).last() on the history relation and assert a non-None result.
        """
        instance = ProtocolTestModel.objects.create(name="Chainable", is_active=True)

        # Update multiple times
        instance.name = "First Update"
        instance.save()
        instance.name = "Second Update"
        instance.save()

        # Should be able to chain methods
        result = instance.history.using("default").filter(name="First Update").last()
        self.assertIsNotNone(result)

    def test_supports_activation_can_modify_is_active(self):
        """
        Tests that models supporting activation can modify is_active.
        """
        instance = ProtocolTestModel.objects.create(name="Activatable", is_active=True)

        self.assertTrue(instance.is_active)

        # Should be able to change is_active
        instance.is_active = False
        instance.save()

        instance.refresh_from_db()
        self.assertFalse(instance.is_active)

    def test_supports_write_pk_attribute_exists(self):
        """
        Tests that SupportsWrite protocol ensures pk attribute exists.
        """
        instance = ProtocolTestModel.objects.create(name="PK Test", is_active=True)

        self.assertIsNotNone(instance.pk)
        self.assertIsInstance(instance.pk, int)

    def test_protocol_runtime_checkable(self):
        """
        Tests that protocols are runtime_checkable via isinstance.
        """
        instance = ProtocolTestModel.objects.create(name="Runtime", is_active=True)

        # All protocols should be runtime checkable
        self.assertTrue(isinstance(instance, SupportsHistory))
        self.assertTrue(isinstance(instance, SupportsActivation))
        self.assertTrue(isinstance(instance, SupportsWrite))

    def test_multiple_protocol_satisfaction(self):
        """
        Tests that a single model can satisfy multiple protocols.
        """
        instance = ProtocolTestModel.objects.create(
            name="Multi-Protocol", is_active=True
        )

        # Should satisfy all three protocols
        protocols_satisfied = [
            isinstance(instance, SupportsHistory),
            isinstance(instance, SupportsActivation),
            isinstance(instance, SupportsWrite),
        ]

        self.assertTrue(all(protocols_satisfied))
        self.assertEqual(sum(protocols_satisfied), 3)