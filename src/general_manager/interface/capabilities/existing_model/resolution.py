"""Capabilities specialized for ExistingModelInterface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, cast

from django.apps import apps
from django.db import models
from simple_history import register

from general_manager.factory.auto_factory import AutoFactory
from general_manager.interface.utils.errors import (
    InvalidModelReferenceError,
    MissingModelConfigurationError,
)
from general_manager.interface.utils.models import get_full_clean_methode

from ..base import CapabilityName
from ..builtin import BaseCapability
from ..orm import OrmPersistenceSupportCapability, SoftDeleteCapability
from ._compat import call_with_observability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.existing_model import (
        ExistingModelInterface,
    )

type ExistingInterfaceClass = type["ExistingModelInterface[models.Model]"]
type AttributeMap = dict[str, object]


@dataclass
class ExistingModelResolutionCapability(BaseCapability):
    """Resolve and configure the Django model used by ExistingModelInterface."""

    name: ClassVar[CapabilityName] = "existing_model_resolution"
    required_attributes: ClassVar[tuple[str, ...]] = ("get_capability_handler",)

    def resolve_model(
        self, interface_cls: ExistingInterfaceClass
    ) -> type[models.Model]:
        """
        Resolve and bind the Django model referenced by an ExistingModelInterface subclass.

        Parameters:
            interface_cls: The interface class whose `model` attribute should be
                resolved and attached. `model` must be either a Django model
                class or a string accepted by `django.apps.apps.get_model()`,
                such as `"app_label.ModelName"` or `settings.AUTH_USER_MODEL`.
                `None`, empty strings, unresolved lazy references, and non-model
                objects are invalid.

        Returns:
            type[models.Model]: The resolved Django model class.

        Raises:
            MissingModelConfigurationError: If the interface has no `model` attribute set.
            InvalidModelReferenceError: If the `model` attribute is neither a valid model class nor a resolvable app label string.
            Exception: Exceptions raised by the observability hook are not wrapped.
        """
        payload_snapshot = {"interface": interface_cls.__name__}

        def _perform() -> type[models.Model]:
            """
            Resolve the configured Django model for the enclosing interface class and configure the interface's soft-delete default.

            This sets the resolved model on the interface class (both `_model` and `model`) and either configures the soft-delete capability handler with the model's default active state or stores that default on the interface.

            Returns:
                type[models.Model]: The resolved Django model class.

            Raises:
                MissingModelConfigurationError: If the interface has no `model` attribute configured.
                InvalidModelReferenceError: If the `model` attribute is neither a valid app model string nor a Django model class.
            """
            model_reference = getattr(interface_cls, "model", None)
            if model_reference is None:
                raise MissingModelConfigurationError(interface_cls.__name__)
            if isinstance(model_reference, str):
                try:
                    model = apps.get_model(model_reference)
                except (LookupError, ValueError) as error:
                    raise InvalidModelReferenceError(model_reference) from error
            elif isinstance(model_reference, type) and issubclass(
                model_reference, models.Model
            ):
                model = model_reference
            else:
                raise InvalidModelReferenceError(model_reference)
            interface_cls._model = model
            interface_cls.model = model
            default_state = hasattr(model, "is_active")
            handler = interface_cls.get_capability_handler("soft_delete")
            if isinstance(handler, SoftDeleteCapability):
                handler.set_state(enabled=default_state)
            else:
                type.__setattr__(interface_cls, "_soft_delete_default", default_state)
            return model

        return call_with_observability(
            interface_cls,
            operation="existing_model.resolve",
            payload=payload_snapshot,
            func=_perform,
        )

    def ensure_history(
        self,
        model: type[models.Model],
        interface_cls: ExistingInterfaceClass | None = None,
    ) -> None:
        """
        Register simple history tracking for the given Django model if it is not already registered.

        Parameters:
            model (type[models.Model]): The Django model class to enable history tracking on.
            interface_cls (type["ExistingModelInterface"] | None): Optional interface class associated with the model; when provided, the registration is attributed to that interface.

        Raises:
            Exception: Registration and observability hook exceptions are not wrapped.
        """
        payload_snapshot = {
            "interface": interface_cls.__name__ if interface_cls else None,
            "model": getattr(model, "__name__", str(model)),
        }

        def _perform() -> None:
            """
            Register simple history for the captured Django model if history is not already present.

            If the model's _meta already exposes the simple_history manager attribute, no action is taken. Otherwise, collects the model's local many-to-many field names and registers the model with simple_history including those m2m fields.
            """
            if hasattr(model._meta, "simple_history_manager_attribute"):
                return
            m2m_fields = [field.name for field in model._meta.local_many_to_many]
            register(model, m2m_fields=m2m_fields)

        target = interface_cls or model
        return call_with_observability(
            target,
            operation="existing_model.ensure_history",
            payload=payload_snapshot,
            func=_perform,
        )

    def apply_rules(
        self,
        interface_cls: ExistingInterfaceClass,
        model: type[models.Model],
    ) -> None:
        """
        Apply the interface's Meta.rules to the Django model and wire a compatible full_clean method.

        If the interface defines a Meta.rules sequence, this function appends those rules to any existing rules on model._meta and assigns the combined list back to model._meta.rules. It then replaces model.full_clean with a full-clean helper appropriate for the model. If no rules are defined on the interface, the model is left unchanged. Repeated calls append the interface rules again and replace full_clean again; the method is not a deduplication boundary.

        Parameters:
            interface_cls (type[ExistingModelInterface]): The interface class whose Meta.rules will be applied.
            model (type[django.db.models.Model]): The Django model to receive combined rules and a patched full_clean.

        Raises:
            Exception: Invalid rule containers, full_clean patching errors, and
                observability hook exceptions are not wrapped.
        """
        payload_snapshot = {
            "interface": interface_cls.__name__,
            "model": getattr(model, "__name__", str(model)),
        }

        def _perform() -> None:
            """
            Merge rules from the interface Meta into the model's _meta.rules and replace the model's full_clean with a rules-aware implementation.

            If the interface defines no `Meta.rules`, the function returns without changes. When rules exist, it appends them to any existing model._meta.rules and sets model.full_clean to the helper produced by get_full_clean_methode(model).
            """
            meta_class = getattr(interface_cls, "Meta", None)
            rules = getattr(meta_class, "rules", None) if meta_class else None
            if not rules:
                return
            combined_rules: list[object] = []
            existing_rules = getattr(model._meta, "rules", None)
            if existing_rules:
                combined_rules.extend(existing_rules)
            combined_rules.extend(rules)
            object.__setattr__(model._meta, "rules", combined_rules)
            type.__setattr__(model, "full_clean", get_full_clean_methode(model))

        return call_with_observability(
            interface_cls,
            operation="existing_model.apply_rules",
            payload=payload_snapshot,
            func=_perform,
        )

    def pre_create(
        self,
        *,
        name: str,
        attrs: AttributeMap,
        interface: ExistingInterfaceClass,
    ) -> tuple[AttributeMap, ExistingInterfaceClass, type[models.Model]]:
        """
        Prepare and return attributes and types needed to create a concrete interface class tied to an existing Django model.

        Parameters:
            name (str): The name to use for the generated concrete interface and its factory.
            attrs: Attribute mapping for the class being created; this mapping will be updated with keys such as "_interface_type", "Interface", and "Factory".
            interface (type[ExistingModelInterface]): The ExistingModelInterface subclass that defines the model reference and optional Factory to base the concrete interface on.

        Returns:
            tuple[AttributeMap, ExistingInterfaceClass, type[models.Model]]: A tuple containing the (possibly mutated) attrs dict to use when creating the class, the generated concrete interface type (with its model wired), and the resolved Django model class.

        Raises:
            MissingModelConfigurationError: If model resolution finds no model configuration.
            InvalidModelReferenceError: If model resolution receives an invalid reference.
            Exception: History registration, rule application, factory
                construction, class creation, and observability hook exceptions
                are not wrapped. Repeated calls repeat history/rule/factory
                work according to the helper methods' contracts.
        """
        payload_snapshot = {
            "interface": interface.__name__,
            "name": name,
        }

        def _perform() -> tuple[
            AttributeMap, ExistingInterfaceClass, type[models.Model]
        ]:
            """
            Prepare and return updated attributes, a concrete interface class, and the resolved Django model for interface creation.

            Performs model resolution, ensures history and rules are applied, creates a concrete subclass of the provided interface with model wiring, sets the interface wiring values into the passed-in attrs (including a generated Factory), and returns the updated attrs along with the concrete interface type and the model.

            Returns:
                tuple: A 3-tuple (attrs, concrete_interface, model) where
                    - attrs is the updated attribute mapping to be used for class creation,
                    - concrete_interface (type[ExistingModelInterface]) is the new concrete interface subclass wired to the resolved model,
                    - model (type[django.db.models.Model]) is the resolved Django model class.
            """
            model = self.resolve_model(interface)
            self.ensure_history(model, interface)
            self.apply_rules(interface, model)
            concrete_interface: ExistingInterfaceClass = type(
                interface.__name__, (interface,), {}
            )
            concrete_interface._model = model
            concrete_interface.model = model
            type.__setattr__(
                concrete_interface, "_soft_delete_default", hasattr(model, "is_active")
            )
            type.__setattr__(concrete_interface, "_field_descriptors", None)
            attrs["_interface_type"] = interface._interface_type
            attrs["Interface"] = concrete_interface
            factory_candidate = attrs.pop("Factory", None)
            manager_factory = (
                factory_candidate if isinstance(factory_candidate, type) else None
            )
            factory_definition = manager_factory or getattr(
                interface, "Factory", None
            )
            attrs["Factory"] = self.build_factory(
                name=name,
                interface_cls=concrete_interface,
                model=model,
                factory_definition=factory_definition,
            )
            return attrs, concrete_interface, model

        return call_with_observability(
            interface,
            operation="existing_model.pre_create",
            payload=payload_snapshot,
            func=_perform,
        )

    def post_create(
        self,
        *,
        new_class: type,
        interface_class: ExistingInterfaceClass,
        model: type[models.Model] | None,
    ) -> None:
        """
        Finalize wiring between the newly created concrete class, its interface, and the Django model.

        Parameters:
            new_class (type): The newly created concrete manager/class that will be attached to the interface and model.
            interface_class (type[ExistingModelInterface]): The interface class from which the concrete class was created.
            model (type[models.Model] | None): The resolved Django model associated with the interface, or `None` if no model is configured.

        Description:
            If a model is provided, attaches `new_class` as the interface's `_parent_class` and the model's `_general_manager_class`,
            assigns `new_class.objects` using the ORM persistence capability for the interface, and if soft-delete support is enabled,
            ensures the model exposes `all_objects` (falling back to `_default_manager` if missing) and assigns `new_class.all_objects`
            from the ORM support capability with `only_active=False`. If the
            legacy model does not already expose an unfiltered `all_objects`,
            the fallback mirrors Django's `_default_manager`; GeneralManager
            cannot guarantee that fallback is unfiltered when the legacy model's
            default manager filters rows.

        Raises:
            Exception: ORM support lookup, manager construction, generated class
                wiring, and observability hook exceptions are not wrapped.
        """
        payload_snapshot = {
            "interface": interface_class.__name__,
            "model": getattr(model, "__name__", None) if model else None,
        }

        def _perform() -> None:
            """
            Finalize wiring after creating a concrete manager class for an existing-model-based interface.

            Sets the new concrete class as the interface's parent and the model's general manager, obtains and assigns an ORM manager to the new class, and—if soft-delete is enabled—ensures the model exposes an `all_objects` manager and assigns a non-filtered manager to the new class.
            """
            if model is None:
                return
            interface_class._parent_class = new_class
            type.__setattr__(model, "_general_manager_class", new_class)
            support = cast(
                OrmPersistenceSupportCapability,
                interface_class.require_capability(
                    "orm_support",
                    expected_type=OrmPersistenceSupportCapability,
                ),
            )
            type.__setattr__(new_class, "objects", support.get_manager(interface_class))
            soft_delete = interface_class.get_capability_handler("soft_delete")
            if (
                isinstance(soft_delete, SoftDeleteCapability)
                and soft_delete.is_enabled()
            ):
                if not hasattr(model, "all_objects"):
                    type.__setattr__(model, "all_objects", model._default_manager)
                type.__setattr__(
                    new_class,
                    "all_objects",
                    support.get_manager(
                        interface_class,
                        only_active=False,
                    ),
                )

        return call_with_observability(
            interface_class,
            operation="existing_model.post_create",
            payload=payload_snapshot,
            func=_perform,
        )

    def build_factory(
        self,
        *,
        name: str,
        interface_cls: ExistingInterfaceClass,
        model: type[models.Model],
        factory_definition: type[object] | None = None,
    ) -> type[AutoFactory[models.Model]]:
        """
        Create a concrete AutoFactory subclass configured for the given interface and Django model.

        Parameters:
            name (str): Base name used to derive the generated factory class name (result will be "{name}Factory").
            interface_cls (type[ExistingModelInterface]): The interface type the factory will produce instances for.
            model (type[models.Model]): The Django model that the factory's Meta.model should reference.
            factory_definition: Optional prototype class whose directly declared
                non-dunder attributes are copied into the generated factory,
                except `Meta`. Inherited attributes are not copied from the
                prototype `__dict__`; descriptors, callables, and annotations
                present in that dict are copied as ordinary values. Attributes
                generated by this method (`interface` and `Meta`) override any
                copied value with the same name.

        Returns:
            type[AutoFactory]: A newly created AutoFactory subclass named "{name}Factory" with its `interface` attribute set to the given interface and a Meta class pointing to the provided model.

        Raises:
            Exception: Invalid factory definitions, invalid nested Meta values,
                class creation errors, and AutoFactory metaclass errors are not
                wrapped.
        """
        factory_definition = factory_definition or getattr(
            interface_cls, "Factory", None
        )
        factory_attributes: AttributeMap = {}
        if factory_definition:
            for attr_name, attr_value in factory_definition.__dict__.items():
                if not attr_name.startswith("__") and attr_name != "Meta":
                    factory_attributes[attr_name] = attr_value
        factory_attributes["interface"] = interface_cls
        meta_attrs: AttributeMap = {}
        meta_cls = getattr(factory_definition, "Meta", None)
        if meta_cls is not None:
            for attr_name, attr_value in meta_cls.__dict__.items():
                if not attr_name.startswith("__"):
                    meta_attrs[attr_name] = attr_value
        meta_attrs["model"] = model
        factory_attributes["Meta"] = type("Meta", (), meta_attrs)
        return type(f"{name}Factory", (AutoFactory,), factory_attributes)
