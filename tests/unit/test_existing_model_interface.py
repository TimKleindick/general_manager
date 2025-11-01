# type: ignore
from __future__ import annotations

from typing import ClassVar

from django.apps import apps
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.test import TransactionTestCase

from general_manager.interface.existing_model_interface import (
    ExistingModelInterface,
    InvalidModelReferenceError,
    MissingModelConfigurationError,
)
from general_manager.manager.general_manager import GeneralManager


class AlwaysFailRule:
    def __init__(self) -> None:
        self.called = False

    def evaluate(self, obj: models.Model) -> bool:
        self.called = True
        return False

    def get_error_message(self) -> dict[str, list[str]]:
        return {"name": ["invalid"]}


class ExistingModelInterfaceTestCase(TransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

        class ExistingUnitCustomer(models.Model):
            name = models.CharField(max_length=64)
            notes = models.TextField(blank=True)
            is_active = models.BooleanField(default=True)
            changed_by = models.ForeignKey(
                User, on_delete=models.PROTECT, null=True, blank=True
            )

            class Meta:
                app_label = "general_manager"

        cls.model = ExistingUnitCustomer
        model_key = cls.model._meta.model_name
        if model_key not in apps.all_models["general_manager"]:
            apps.register_model("general_manager", ExistingUnitCustomer)
        ExistingModelInterface._ensure_history(cls.model)
        with connection.schema_editor() as schema:
            schema.create_model(cls.model)
            schema.create_model(cls.model.history.model)  # type: ignore[attr-defined]

    @classmethod
    def tearDownClass(cls) -> None:
        with connection.schema_editor() as schema:
            history_model = cls.model.history.model  # type: ignore[attr-defined]
            schema.delete_model(history_model)
            schema.delete_model(cls.model)
        app_config = apps.get_app_config("general_manager")
        model_key = cls.model._meta.model_name
        history_key = cls.model.history.model._meta.model_name  # type: ignore[attr-defined]
        apps.all_models["general_manager"].pop(model_key, None)
        apps.all_models["general_manager"].pop(history_key, None)
        app_config.models.pop(model_key, None)
        app_config.models.pop(history_key, None)
        super().tearDownClass()

    def test_resolve_model_class_from_class_reference(self) -> None:
        class InterfaceUnderTest(ExistingModelInterface):
            model = self.model

        resolved = InterfaceUnderTest._resolve_model_class()
        self.assertIs(resolved, self.model)

    def test_resolve_model_class_from_string_reference(self) -> None:
        class InterfaceUnderTest(ExistingModelInterface):
            model = f"general_manager.{self.model.__name__}"

        resolved = InterfaceUnderTest._resolve_model_class()
        self.assertIs(resolved, self.model)

    def test_resolve_model_class_missing_model_raises(self) -> None:
        class InterfaceUnderTest(ExistingModelInterface):
            model = None  # type: ignore[assignment]

        with self.assertRaises(MissingModelConfigurationError):
            InterfaceUnderTest._resolve_model_class()

    def test_resolve_model_class_invalid_reference_raises(self) -> None:
        class InterfaceUnderTest(ExistingModelInterface):
            model = "nonexistent.App"

        with self.assertRaises(InvalidModelReferenceError):
            InterfaceUnderTest._resolve_model_class()

    def test_pre_create_registers_history_and_factory(self) -> None:
        rule = AlwaysFailRule()

        class InterfaceUnderTest(ExistingModelInterface):
            model = self.model

            class Meta:
                rules: ClassVar[list[AlwaysFailRule]] = [rule]

            class Factory:
                default_name = "legacy"

        attrs: dict[str, object] = {"__module__": __name__}
        new_attrs, interface_cls, model = InterfaceUnderTest._pre_create(
            "ExistingCustomerManager", attrs, InterfaceUnderTest
        )

        self.assertIs(model, self.model)
        self.assertEqual(new_attrs["_interface_type"], "existing")
        self.assertIn("Interface", new_attrs)
        self.assertIn("Factory", new_attrs)
        factory_cls = new_attrs["Factory"]
        self.assertTrue(hasattr(factory_cls, "interface"))
        self.assertIs(factory_cls.interface, interface_cls)
        self.assertEqual(factory_cls._meta.model, self.model)  # type: ignore[arg-type]
        self.assertTrue(hasattr(self.model, "history"))
        self.assertEqual(self.model._meta.rules[0], rule)  # type: ignore[attr-defined]

        instance = self.model(name="Fails rule")
        with self.assertRaises(ValidationError):
            instance.full_clean()
        self.assertTrue(rule.called)

    def test_post_create_links_manager_and_model(self) -> None:
        class InterfaceUnderTest(ExistingModelInterface):
            model = self.model

        attrs: dict[str, object] = {"__module__": __name__}
        new_attrs, interface_cls, model = InterfaceUnderTest._pre_create(
            "TemporaryManager", attrs, InterfaceUnderTest
        )
        TemporaryManager = type("TemporaryManager", (GeneralManager,), new_attrs)
        InterfaceUnderTest._post_create(TemporaryManager, interface_cls, model)

        self.assertIs(interface_cls._parent_class, TemporaryManager)
        self.assertIs(model._general_manager_class, TemporaryManager)  # type: ignore[attr-defined]
        self.assertIsInstance(TemporaryManager.Interface, type)  # type: ignore[attr-defined]
        self.assertIs(TemporaryManager.Interface._parent_class, TemporaryManager)  # type: ignore[attr-defined]
        self.assertIs(TemporaryManager.Interface._model, model)  # type: ignore[attr-defined]
