"""Interface for integrating existing Django models with GeneralManager."""

from __future__ import annotations

from typing import Any, ClassVar, TypeVar, cast

from django.apps import apps
from django.db import models
from general_manager.factory.auto_factory import AutoFactory
from general_manager.interface.base_interface import (
    attributes,
    classPostCreationMethod,
    classPreCreationMethod,
    generalManagerClassName,
    interfaceBaseClass,
    newlyCreatedGeneralManagerClass,
    newlyCreatedInterfaceClass,
    relatedClass,
)
from general_manager.interface.backends.database.database_based_interface import (
    OrmWritableInterface,
)
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.existing_model import (
    ExistingModelResolutionCapability,
)
from general_manager.interface.models import GeneralManagerBasisModel
from general_manager.interface.utils.errors import (
    InvalidModelReferenceError,
    MissingModelConfigurationError,
)

ExistingModelT = TypeVar("ExistingModelT", bound=models.Model)


class ExistingModelInterface(OrmWritableInterface[ExistingModelT]):
    """Interface that reuses an existing Django model instead of generating a new one."""

    _interface_type: ClassVar[str] = "existing"
    model: ClassVar[type[models.Model] | str | None] = None

    capability_overrides = OrmWritableInterface.capability_overrides.copy()
    capability_overrides.update(
        {"existing_model_resolution": ExistingModelResolutionCapability}
    )
    lifecycle_capability_name: ClassVar[CapabilityName | None] = None

    @classmethod
    def _build_factory(
        cls,
        name: generalManagerClassName,
        interface_cls: type["ExistingModelInterface"],
        model: type[ExistingModelT],
        factory_definition: type | None = None,
    ) -> type[AutoFactory]:
        """
        Create a new AutoFactory subclass configured to produce instances of the given Django model.

        Parameters:
            name (str): Base name used to name the generated factory class (the factory will be "<name>Factory").
            interface_cls (type[ExistingModelInterface]): Interface class that the factory will reference via its `interface` attribute.
            model (type[models.Model]): Django model class that the factory's inner `Meta.model` will point to.
            factory_definition (type | None): Optional existing Factory class whose non-dunder attributes will be copied into the generated factory.

        Returns:
            type[AutoFactory]: A dynamically created AutoFactory subclass bound to `model`, with copied attributes, an `interface` attribute set to `interface_cls`, and an inner `Meta` class referencing `model`.
        """
        capability = cls._existing_model_capability()
        return capability.build_factory(
            name=name,
            interface_cls=interface_cls,
            model=model,
            factory_definition=factory_definition,
        )

    @classmethod
    def _pre_create(
        cls,
        name: generalManagerClassName,
        attrs: attributes,
        interface: interfaceBaseClass,
        base_model_class: type[GeneralManagerBasisModel] = GeneralManagerBasisModel,
    ) -> tuple[attributes, interfaceBaseClass, relatedClass]:
        """
        Prepare and bind a concrete interface and Factory for creating a GeneralManager backed by the configured Django model.

        Parameters:
            name: Name to use when building the manager's Factory class.
            attrs: Attribute dictionary for the manager class; this dict is mutated and returned.
            interface: Interface class that declares `model` (class or app label); a concrete subclass bound to the resolved model is created.
            base_model_class: Compatibility hook (not used).

        Returns:
            tuple: (attrs, concrete_interface, model) where
                - attrs: the possibly-modified attribute dict to be used for class creation,
                - concrete_interface: the new interface subclass bound to the resolved Django model,
                - model: the resolved Django model class.
        """
        _ = base_model_class
        capability = cls._existing_model_capability()
        return capability.pre_create(
            name=name,
            attrs=attrs,
            interface=cast(type["ExistingModelInterface"], interface),
        )

    @staticmethod
    def _post_create(
        new_class: newlyCreatedGeneralManagerClass,
        interface_class: newlyCreatedInterfaceClass,
        model: relatedClass,
    ) -> None:
        """
        Link the created GeneralManager subclass with its interface and the resolved Django model.

        Sets the interface's parent reference to the newly created manager class and, when a model is provided, records the manager class on the model. Also attaches manager instances to the created class: assigns `objects` from the interface's manager and, if the interface indicates soft-delete support, provides `all_objects` (configured with `only_active=False`). If the model lacks an `all_objects` attribute but soft-delete is used, the model's `all_objects` is ensured by falling back to its default manager.

        Parameters:
            new_class: The newly created GeneralManager subclass to be linked as the parent.
            interface_class: The interface class that should reference `new_class` as its parent.
            model: The Django model class managed by the interface; if provided, its `_general_manager_class` may be set.
        """
        capability = interface_class._existing_model_capability()  # type: ignore[attr-defined]
        capability.post_create(
            new_class=new_class,
            interface_class=interface_class,
            model=model,
        )

    @classmethod
    def get_field_type(cls, field_name: str) -> type:
        """
        Get the Python type for a field on the wrapped model, resolving the configured model first if not already resolved.

        Parameters:
            field_name (str): Name of the field on the underlying Django model.

        Returns:
            type: The Python type corresponding to the specified model field.
        """
        if not hasattr(cls, "_model"):
            resolver = cls.get_capability_handler("existing_model_resolution")
            if resolver is None or not hasattr(resolver, "resolve_model"):
                cls._fallback_model_setup()
            else:
                resolver.resolve_model(cls)
        return super().get_field_type(field_name)

    @classmethod
    def _fallback_model_setup(cls) -> type[models.Model]:
        model_reference = getattr(cls, "model", None)
        if model_reference is None:
            raise MissingModelConfigurationError(cls.__name__)
        if isinstance(model_reference, str):
            try:
                model = apps.get_model(model_reference)
            except LookupError as error:
                raise InvalidModelReferenceError(model_reference) from error
        elif isinstance(model_reference, type) and issubclass(
            model_reference, models.Model
        ):
            model = model_reference
        else:
            raise InvalidModelReferenceError(model_reference)
        cls._model = model  # type: ignore[assignment]
        cls.model = model
        cls._use_soft_delete = hasattr(model, "is_active")
        return model

    @classmethod
    def _resolve_model_class(cls) -> type[models.Model]:
        resolver = cls.get_capability_handler("existing_model_resolution")
        if resolver is None or not hasattr(resolver, "resolve_model"):
            return cls._fallback_model_setup()
        return resolver.resolve_model(cls)

    @classmethod
    def _ensure_history(cls, model: type[models.Model]) -> None:
        capability = cls._existing_model_capability()
        capability.ensure_history(model, cls)

    @classmethod
    def _apply_rules_to_model(cls, model: type[models.Model]) -> None:
        capability = cls._existing_model_capability()
        capability.apply_rules(cls, model)

    @classmethod
    def handle_interface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        return cls._pre_create, cls._post_create

    @classmethod
    def _existing_model_capability(cls) -> ExistingModelResolutionCapability:
        handler = cls.get_capability_handler("existing_model_resolution")
        if isinstance(handler, ExistingModelResolutionCapability):
            return handler
        return ExistingModelResolutionCapability()
