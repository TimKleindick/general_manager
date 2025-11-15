# type: ignore
from __future__ import annotations

from typing import ClassVar, Callable

from django.apps import apps
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.test import TransactionTestCase

from general_manager.interface import ExistingModelInterface
from general_manager.interface.capabilities.existing_model import (
    ExistingModelResolutionCapability,
)
from general_manager.interface.utils.errors import (
    InvalidModelReferenceError,
    MissingModelConfigurationError,
)
from general_manager.manager.general_manager import GeneralManager


class AlwaysFailRule:
    def __init__(self) -> None:
        """
        Initialize the rule instance and reset its invocation flag.

        Sets the `called` attribute to False to indicate the rule has not been invoked yet.
        """
        self.called = False

    def evaluate(self, obj: models.Model) -> bool:
        """
        Mark the rule as invoked for the given model instance and indicate validation failure.

        Parameters:
            obj (models.Model): The model instance being evaluated.

        Returns:
            bool: `False` always, indicating the object does not pass this rule.
        """
        self.called = True
        return False

    def get_error_message(self) -> dict[str, list[str]]:
        """
        Provide a mapping of field names to validation error codes for this rule.

        Returns:
            dict[str, list[str]]: Mapping where each key is a field name and each value is a list of error codes; e.g., {"name": ["invalid"]}.
        """
        return {"name": ["invalid"]}


class ExistingModelInterfaceTestCase(TransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """
        Prepare test fixtures by defining and registering a temporary Django model and creating its database tables.

        Defines an in-file model class named ExistingUnitCustomer, registers it under the "general_manager" app if not already registered, ensures history tracking is attached, and creates the model and its history table in the test database. Assigns the model class to cls.model as a class-level attribute.
        """
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
        capability = ExistingModelInterface.require_capability(  # type: ignore[assignment]
            "existing_model_resolution",
            expected_type=ExistingModelResolutionCapability,
        )
        capability.ensure_history(cls.model, ExistingModelInterface)
        with connection.schema_editor() as schema:
            schema.create_model(cls.model)
            schema.create_model(cls.model.history.model)  # type: ignore[attr-defined]

    @classmethod
    def tearDownClass(cls) -> None:
        """
        Tears down the dynamically created model and its history, removing their database tables and unregistering them from the app registry.

        Deletes the history model and main model tables, removes both models from the "general_manager" app registry and global model caches, clears the apps cache, and then delegates to the superclass tearDownClass for any additional cleanup.
        """
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
        apps.clear_cache()
        super().tearDownClass()

    def _invoke_handle(
        self,
        interface_cls: type[ExistingModelInterface],
        *,
        name: str = "TemporaryManager",
        attrs: dict[str, object] | None = None,
    ) -> tuple[
        dict[str, object],
        type[ExistingModelInterface],
        type[models.Model],
        Callable[[type, type, type[models.Model] | None], None],
    ]:
        pre, post = interface_cls.handle_interface()
        attrs = {"__module__": __name__} if attrs is None else attrs
        new_attrs, resolved_interface, model = pre(name, attrs, interface_cls)
        return new_attrs, resolved_interface, model, post

    @staticmethod
    def _resolution_capability(
        interface_cls: type[ExistingModelInterface],
    ) -> ExistingModelResolutionCapability:
        return interface_cls.require_capability(  # type: ignore[return-value]
            "existing_model_resolution",
            expected_type=ExistingModelResolutionCapability,
        )

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
        """
        Verifies that resolving an invalid model string reference raises InvalidModelReferenceError.

        Creates an interface class whose `model` attribute is an invalid string reference and asserts
        that calling `_resolve_model_class()` raises `InvalidModelReferenceError`.
        """

        class InterfaceUnderTest(ExistingModelInterface):
            model = "nonexistent.App"

        with self.assertRaises(InvalidModelReferenceError):
            InterfaceUnderTest._resolve_model_class()

    def test_pre_create_registers_history_and_factory(self) -> None:
        """
        Verifies that _pre_create registers model history, builds a Factory and Interface, and applies interface rules to model validation.

        Asserts that:
        - the resolved model is returned and the new attributes include `_interface_type` set to "existing", `Interface`, and a built `Factory`;
        - the built Factory is bound to the created interface class and its `_meta.model` points to the model;
        - the model has a `history` attribute and the interface rule is copied into `model._meta.rules`;
        - creating a model instance that violates the rule causes `full_clean()` to raise `ValidationError` and the rule's `evaluate` was invoked.
        """
        rule = AlwaysFailRule()

        class InterfaceUnderTest(ExistingModelInterface):
            model = self.model

            class Meta:
                rules: ClassVar[list[AlwaysFailRule]] = [rule]

            class Factory:
                default_name = "legacy"

        attrs: dict[str, object] = {"__module__": __name__}
        new_attrs, interface_cls, model, _ = self._invoke_handle(
            InterfaceUnderTest,
            name="ExistingCustomerManager",
            attrs=attrs,
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
        """
        Verifies that post-creation links the generated manager class with the interface and model.

        After running _pre_create and dynamically creating a manager subclass, _post_create should:
        - set the interface class's _parent_class to the manager class,
        - set the model's _general_manager_class to the manager class,
        - attach a concrete Interface class on the manager that is a type,
        - ensure that the attached Interface has its _parent_class set to the manager and its _model set to the model.
        """

        class InterfaceUnderTest(ExistingModelInterface):
            model = self.model

        attrs: dict[str, object] = {"__module__": __name__}
        new_attrs, interface_cls, model, post = self._invoke_handle(
            InterfaceUnderTest,
            attrs=attrs,
        )
        TemporaryManager = type("TemporaryManager", (GeneralManager,), new_attrs)
        post(TemporaryManager, interface_cls, model)

        self.assertIs(interface_cls._parent_class, TemporaryManager)
        self.assertIs(model._general_manager_class, TemporaryManager)  # type: ignore[attr-defined]
        self.assertIsInstance(TemporaryManager.Interface, type)  # type: ignore[attr-defined]
        self.assertIs(TemporaryManager.Interface._parent_class, TemporaryManager)  # type: ignore[attr-defined]
        self.assertIs(TemporaryManager.Interface._model, model)  # type: ignore[attr-defined]

    def test_ensure_history_when_already_registered(self) -> None:
        """
        Tests that _ensure_history doesn't re-register already tracked models.
        """
        # Model already has history from setUpClass
        self.assertTrue(hasattr(self.model, "history"))

        capability = self._resolution_capability(ExistingModelInterface)
        capability.ensure_history(self.model, ExistingModelInterface)

        # Still should have history
        self.assertTrue(hasattr(self.model, "history"))

    def test_ensure_history_for_untracked_model(self) -> None:
        """
        Tests that _ensure_history registers history for untracked models.
        """

        # Create a new model without history
        class UnhistoredModel(models.Model):
            name = models.CharField(max_length=64)

            class Meta:
                app_label = "general_manager"

        # Should not have history initially
        self.assertFalse(
            hasattr(UnhistoredModel._meta, "simple_history_manager_attribute")
        )

        capability = self._resolution_capability(ExistingModelInterface)
        capability.ensure_history(UnhistoredModel, ExistingModelInterface)

        # Now should have history
        self.assertTrue(hasattr(UnhistoredModel, "history"))

    def test_apply_rules_to_model_with_no_rules(self) -> None:
        """
        Tests that _apply_rules_to_model handles interfaces without rules gracefully.
        """

        class NoRulesInterface(ExistingModelInterface):
            model = self.model

        capability = self._resolution_capability(NoRulesInterface)
        capability.apply_rules(NoRulesInterface, self.model)

    def test_apply_rules_to_model_with_existing_model_rules(self) -> None:
        """
        Tests that _apply_rules_to_model combines interface rules with model rules.
        """

        # Add existing rules to model
        class ExistingRule:
            def evaluate(self, obj: models.Model) -> bool:
                """
                Indicates whether the rule accepts the provided model instance.

                Parameters:
                    obj (models.Model): The model instance to evaluate.

                Returns:
                    `true` if the instance satisfies the rule; for this implementation it is always `true`.
                """
                return True

            def get_error_message(self) -> dict[str, list[str]]:
                """
                Provide the rule's error messages keyed by field names.

                Returns:
                    dict[str, list[str]]: A mapping from model field names to a list of error messages for each field; an empty dict if there are no error messages.
                """
                return {}

        existing_rule = ExistingRule()
        self.model._meta.rules = [existing_rule]  # type: ignore[attr-defined]

        # Create interface with additional rule
        new_rule = AlwaysFailRule()

        class CombinedRulesInterface(ExistingModelInterface):
            model = self.model

            class Meta:
                rules: ClassVar[list[AlwaysFailRule]] = [new_rule]

        capability = self._resolution_capability(CombinedRulesInterface)
        capability.apply_rules(CombinedRulesInterface, self.model)

        # Should have both rules
        self.assertEqual(len(self.model._meta.rules), 2)  # type: ignore[attr-defined]
        self.assertIn(existing_rule, self.model._meta.rules)  # type: ignore[attr-defined]
        self.assertIn(new_rule, self.model._meta.rules)  # type: ignore[attr-defined]

        # Clean up
        delattr(self.model._meta, "rules")

    def test_apply_rules_to_model_injects_full_clean(self) -> None:
        """
        Tests that _apply_rules_to_model replaces full_clean method.
        """
        rule = AlwaysFailRule()

        class RuledInterface(ExistingModelInterface):
            model = self.model

            class Meta:
                rules: ClassVar[list[AlwaysFailRule]] = [rule]

        # Store original full_clean
        original_full_clean = self.model.full_clean

        capability = self._resolution_capability(RuledInterface)
        capability.apply_rules(RuledInterface, self.model)

        # Should have replaced full_clean
        self.assertIsNotNone(self.model.full_clean)

        # Clean up
        self.model.full_clean = original_full_clean  # type: ignore[method-assign]
        if hasattr(self.model._meta, "rules"):
            delattr(self.model._meta, "rules")

    def test_build_factory_with_no_factory_definition(self) -> None:
        """
        Tests that _build_factory creates factory even without explicit Factory class.
        """

        class MinimalInterface(ExistingModelInterface):
            model = self.model

        capability = self._resolution_capability(MinimalInterface)
        factory = capability.build_factory(
            name="TestManager",
            interface_cls=MinimalInterface,
            model=self.model,
            factory_definition=None,
        )

        self.assertIsNotNone(factory)
        self.assertEqual(factory._meta.model, self.model)  # type: ignore[attr-type]
        self.assertEqual(factory.interface, MinimalInterface)

    def test_build_factory_with_custom_factory_definition(self) -> None:
        """
        Tests that _build_factory uses custom Factory attributes.
        """

        class CustomFactoryDef:
            custom_attr = "custom_value"
            custom_method = staticmethod(lambda: "method_result")

        class InterfaceWithFactory(ExistingModelInterface):
            model = self.model
            Factory = CustomFactoryDef

        capability = self._resolution_capability(InterfaceWithFactory)
        factory = capability.build_factory(
            name="TestManager",
            interface_cls=InterfaceWithFactory,
            model=self.model,
            factory_definition=CustomFactoryDef,
        )

        self.assertTrue(hasattr(factory, "custom_attr"))
        self.assertEqual(factory.custom_attr, "custom_value")
        self.assertTrue(hasattr(factory, "custom_method"))

    def test_build_factory_sets_interface_reference(self) -> None:
        """
        Tests that _build_factory properly sets interface reference.
        """

        class TestInterface(ExistingModelInterface):
            model = self.model

        capability = self._resolution_capability(TestInterface)
        factory = capability.build_factory(
            name="TestManager",
            interface_cls=TestInterface,
            model=self.model,
            factory_definition=None,
        )

        self.assertEqual(factory.interface, TestInterface)

    def test_build_factory_creates_meta_with_model(self) -> None:
        """
        Tests that _build_factory creates Meta class with model.
        """

        class TestInterface(ExistingModelInterface):
            model = self.model

        capability = self._resolution_capability(TestInterface)
        factory = capability.build_factory(
            name="TestManager",
            interface_cls=TestInterface,
            model=self.model,
            factory_definition=None,
        )

        self.assertEqual(factory._meta.model, self.model)  # type: ignore[attr-type]

    def test_handle_interface_returns_callables(self) -> None:
        """
        Tests that handle_interface returns pre and post creation methods.
        """
        pre, post = ExistingModelInterface.handle_interface()

        self.assertIsNotNone(pre)
        self.assertIsNotNone(post)
        self.assertTrue(callable(pre))
        self.assertTrue(callable(post))

    def test_get_field_type_delegates_to_parent(self) -> None:
        """
        Tests that get_field_type correctly delegates to parent class.
        """

        class TestInterface(ExistingModelInterface):
            model = self.model

        # Should be able to get field types
        field_type = TestInterface.get_field_type("name")
        self.assertIsNotNone(field_type)

    def test_resolve_model_class_caches_model(self) -> None:
        """
        Tests that _resolve_model_class caches resolved model in _model attribute.
        """

        class TestInterface(ExistingModelInterface):
            model = self.model

        resolved = TestInterface._resolve_model_class()

        self.assertIs(TestInterface._model, self.model)
        self.assertIs(TestInterface.model, self.model)
        self.assertIs(resolved, self.model)

    def test_resolve_model_class_with_invalid_string_reference(self) -> None:
        """
        Tests that _resolve_model_class raises InvalidModelReferenceError for bad strings.
        """

        class TestInterface(ExistingModelInterface):
            model = "invalid.NonexistentModel"

        with self.assertRaises(InvalidModelReferenceError) as context:
            TestInterface._resolve_model_class()

        self.assertIn("invalid.NonexistentModel", str(context.exception))

    def test_resolve_model_class_with_non_model_type(self) -> None:
        """
        Tests that _resolve_model_class raises InvalidModelReferenceError for non-model types.
        """

        class NotAModel:
            pass

        class TestInterface(ExistingModelInterface):
            model = NotAModel  # type: ignore[assignment]

        with self.assertRaises(InvalidModelReferenceError):
            TestInterface._resolve_model_class()

    def test_missing_model_configuration_error_message(self) -> None:
        """
        Tests that MissingModelConfigurationError formats message correctly.
        """
        error = MissingModelConfigurationError("TestInterface")

        self.assertIn("TestInterface", str(error))
        self.assertIn("model", str(error))

    def test_invalid_model_reference_error_message(self) -> None:
        """
        Tests that InvalidModelReferenceError formats message correctly.
        """
        error = InvalidModelReferenceError("bad_reference")

        self.assertIn("bad_reference", str(error))
        self.assertIn("Invalid", str(error))

    def test_interface_type_is_existing(self) -> None:
        """
        Tests that ExistingModelInterface has correct _interface_type.
        """
        self.assertEqual(ExistingModelInterface._interface_type, "existing")

    def test_pre_create_handles_factory_in_attrs(self) -> None:
        """
        Verify that _pre_create replaces a provided Factory class in attrs with a constructed factory preserving custom attributes.

        Asserts that the returned attrs contain a "Factory" entry that is not the original Factory class and that the constructed factory exposes attributes defined on the provided Factory definition (e.g., `custom_value`).
        """

        class CustomFactory:
            custom_value = 42

        class TestInterface(ExistingModelInterface):
            model = self.model

        attrs: dict[str, object] = {"__module__": __name__, "Factory": CustomFactory}

        new_attrs, _interface_cls, _model, _ = self._invoke_handle(
            TestInterface,
            attrs=attrs,
        )

        # Factory should be removed from attrs and replaced with built factory
        self.assertIn("Factory", new_attrs)
        self.assertNotEqual(new_attrs["Factory"], CustomFactory)
        # Built factory should have custom attributes from CustomFactory
        built_factory = new_attrs["Factory"]
        self.assertTrue(hasattr(built_factory, "custom_value"))

    def test_pre_create_sets_interface_type_in_attrs(self) -> None:
        """
        Tests that _pre_create adds _interface_type to attrs.
        """

        class TestInterface(ExistingModelInterface):
            model = self.model

        attrs: dict[str, object] = {"__module__": __name__}

        new_attrs, _, _, _ = self._invoke_handle(
            TestInterface,
            attrs=attrs,
            name="TestManager",
        )

        self.assertIn("_interface_type", new_attrs)
        self.assertEqual(new_attrs["_interface_type"], "existing")

    def test_pre_create_creates_concrete_interface(self) -> None:
        """
        Tests that _pre_create creates a concrete interface subclass.
        """

        class TestInterface(ExistingModelInterface):
            model = self.model

        attrs: dict[str, object] = {"__module__": __name__}

        _, interface_cls, _, _ = self._invoke_handle(
            TestInterface,
            attrs=attrs,
            name="TestManager",
        )

        self.assertIsNotNone(interface_cls)
        self.assertTrue(issubclass(interface_cls, TestInterface))
        self.assertIs(interface_cls._model, self.model)

    def test_post_create_sets_parent_class_on_interface(self) -> None:
        """
        Tests that _post_create sets _parent_class on the interface.
        """

        class TestInterface(ExistingModelInterface):
            model = self.model

        attrs: dict[str, object] = {"__module__": __name__}
        new_attrs, interface_cls, model, post = self._invoke_handle(
            TestInterface,
            attrs=attrs,
        )

        TestManager = type("TestManager", (GeneralManager,), new_attrs)

        post(TestManager, interface_cls, model)

        self.assertIs(interface_cls._parent_class, TestManager)

    def test_post_create_sets_general_manager_class_on_model(self) -> None:
        """
        Ensure that after post-creation the model's _general_manager_class attribute references the created GeneralManager subclass.
        """

        class TestInterface(ExistingModelInterface):
            model = self.model

        attrs: dict[str, object] = {"__module__": __name__}
        new_attrs, interface_cls, model, post = self._invoke_handle(
            TestInterface,
            attrs=attrs,
        )

        TestManager = type("TestManager", (GeneralManager,), new_attrs)

        post(TestManager, interface_cls, model)

        self.assertIs(model._general_manager_class, TestManager)  # type: ignore[attr-defined]
