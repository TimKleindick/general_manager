"""Interface for integrating existing Django models with GeneralManager."""

from __future__ import annotations

from typing import Any, ClassVar, TypeVar, cast

from django.apps import apps
from django.db import models

from simple_history import register  # type: ignore

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
from general_manager.interface.database_based_interface import (
    WritableDBBasedInterface,
)
from general_manager.interface.models import (
    GeneralManagerBasisModel,
    get_full_clean_methode,
)

ExistingModelT = TypeVar("ExistingModelT", bound=models.Model)


class MissingModelConfigurationError(ValueError):
    """Raised when an ExistingModelInterface does not define a model to manage."""

    def __init__(self, interface_name: str) -> None:
        super().__init__(f"{interface_name} must define a 'model' attribute.")


class InvalidModelReferenceError(TypeError):
    """Raised when the model attribute is neither a Django model class nor a resolvable label."""

    def __init__(self, reference: object) -> None:
        super().__init__(f"Invalid model reference '{reference}'.")


class ExistingModelInterface(WritableDBBasedInterface[ExistingModelT]):
    """Interface that reuses an existing Django model instead of generating a new one."""

    _interface_type: ClassVar[str] = "existing"
    model: ClassVar[type[models.Model] | str | None] = None

    @classmethod
    def _resolve_model_class(cls) -> type[models.Model]:
        """
        Resolve the configured model attribute to an actual Django model class.
        """
        model_reference = getattr(cls, "model", None)
        # if model_reference is None:
        #     model_reference = getattr(cls, "_model", None)
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
        cls._model = cast(type[ExistingModelT], model)
        cls.model = model
        return cast(type[models.Model], model)

    @staticmethod
    def _ensure_history(model: type[models.Model]) -> None:
        """
        Attach django-simple-history tracking to the model when not already registered.
        """
        if hasattr(model._meta, "simple_history_manager_attribute"):
            return
        register(model)

    @classmethod
    def _apply_rules_to_model(cls, model: type[models.Model]) -> None:
        """
        Attach interface-defined rules to the managed model and inject a validating full_clean.
        """
        meta_class = getattr(cls, "Meta", None)
        rules = getattr(meta_class, "rules", None) if meta_class else None
        if not rules:
            return
        combined_rules: list[Any] = []
        existing_rules = getattr(model._meta, "rules", None)
        if existing_rules:
            combined_rules.extend(existing_rules)
        combined_rules.extend(rules)
        model._meta.rules = combined_rules  # type: ignore[attr-defined]
        model.full_clean = get_full_clean_methode(model)  # type: ignore[assignment]

    @classmethod
    def _build_factory(
        cls,
        name: generalManagerClassName,
        interface_cls: type["ExistingModelInterface"],
        model: type[ExistingModelT],
        factory_definition: type | None = None,
    ) -> type[AutoFactory]:
        """
        Build an AutoFactory subclass bound to the existing Django model.
        """
        factory_definition = factory_definition or getattr(cls, "Factory", None)
        factory_attributes: dict[str, Any] = {}
        if factory_definition:
            for attr_name, attr_value in factory_definition.__dict__.items():
                if not attr_name.startswith("__"):
                    factory_attributes[attr_name] = attr_value
        factory_attributes["interface"] = interface_cls
        factory_attributes["Meta"] = type("Meta", (), {"model": model})
        return type(f"{name}Factory", (AutoFactory,), factory_attributes)

    @staticmethod
    def _pre_create(
        name: generalManagerClassName,
        attrs: attributes,
        interface: interfaceBaseClass,
        base_model_class: type[GeneralManagerBasisModel] = GeneralManagerBasisModel,
    ) -> tuple[attributes, interfaceBaseClass, relatedClass]:
        """
        Prepare the interface by resolving the model, registering history, and wiring the factory.
        """
        _ = base_model_class
        interface_cls = cast(type["ExistingModelInterface"], interface)
        model = interface_cls._resolve_model_class()
        interface_cls._ensure_history(model)
        interface_cls._apply_rules_to_model(model)

        concrete_interface = cast(
            type["ExistingModelInterface"],
            type(interface.__name__, (interface,), {}),
        )
        concrete_interface._model = cast(type[ExistingModelT], model)
        concrete_interface.model = model

        manager_factory = cast(type | None, attrs.pop("Factory", None))
        attrs["_interface_type"] = interface_cls._interface_type
        attrs["Interface"] = concrete_interface
        attrs["Factory"] = interface_cls._build_factory(
            name, concrete_interface, model, manager_factory
        )

        return attrs, concrete_interface, model

    @staticmethod
    def _post_create(
        new_class: newlyCreatedGeneralManagerClass,
        interface_class: newlyCreatedInterfaceClass,
        model: relatedClass,
    ) -> None:
        """
        Finalize the integration by linking the interface and the managed Django model.
        """
        interface_class._parent_class = new_class
        if model is not None:
            model._general_manager_class = new_class  # type: ignore[attr-defined]

    @classmethod
    def handle_interface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Provide hooks for GeneralManagerMeta to process the interface.
        """
        return cls._pre_create, cls._post_create

    @classmethod
    def get_field_type(cls, field_name: str) -> type:
        """
        Return the Python type for a field on the existing model.
        """
        if not hasattr(cls, "_model"):
            cls._resolve_model_class()
        return super().get_field_type(field_name)
