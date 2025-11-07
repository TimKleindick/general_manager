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
from general_manager.interface.utils.errors import (
    InvalidModelReferenceError,
    MissingModelConfigurationError,
)

ExistingModelT = TypeVar("ExistingModelT", bound=models.Model)


class ExistingModelInterface(WritableDBBasedInterface[ExistingModelT]):
    """Interface that reuses an existing Django model instead of generating a new one."""

    _interface_type: ClassVar[str] = "existing"
    model: ClassVar[type[models.Model] | str | None] = None

    @classmethod
    def _resolve_model_class(cls) -> type[models.Model]:
        """
        Resolve the configured `model` attribute to a concrete Django model class.

        If `cls.model` is a string, attempt to resolve it via Django's app registry; if it is already a Django model class, use it directly. The resolved class is cached on `cls._model` and `cls.model`.

        Returns:
            type[~django.db.models.Model]: The resolved Django model class.

        Raises:
            MissingModelConfigurationError: If the interface did not define a `model` attribute.
            InvalidModelReferenceError: If `model` is neither a Django model class nor a resolvable app label.
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
        Attach django-simple-history tracking to the given Django model if it is not already registered.

        This registers the model with simple-history and includes the model's local many-to-many fields for history tracking.
        If the model is already registered (indicated by a `simple_history_manager_attribute` on its `_meta`), the function does nothing.

        Parameters:
            model (type[models.Model]): The Django model class to enable history tracking for.
        """
        if hasattr(model._meta, "simple_history_manager_attribute"):
            return
        m2m_fields = [field.name for field in model._meta.local_many_to_many]
        register(model, m2m_fields=m2m_fields)

    @classmethod
    def _apply_rules_to_model(cls, model: type[models.Model]) -> None:
        """
        Attach validation rules defined on the interface's Meta to the given Django model and replace its `full_clean` with a validating implementation.

        If `Meta.rules` exists on the interface class, its entries are appended to `model._meta.rules` (preserving any existing rules) and `model.full_clean` is replaced with a generated validating method. If no rules are defined on the interface, the model is left unchanged.

        Parameters:
            model (type[models.Model]): The Django model class to modify.
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
        Create a new AutoFactory subclass configured to produce instances of the given Django model.

        Parameters:
            name (str): Base name used to name the generated factory class (the factory will be "<name>Factory").
            interface_cls (type[ExistingModelInterface]): Interface class that the factory will reference via its `interface` attribute.
            model (type[models.Model]): Django model class that the factory's inner `Meta.model` will point to.
            factory_definition (type | None): Optional existing Factory class whose non-dunder attributes will be copied into the generated factory.

        Returns:
            type[AutoFactory]: A dynamically created AutoFactory subclass bound to `model`, with copied attributes, an `interface` attribute set to `interface_cls`, and an inner `Meta` class referencing `model`.
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
        concrete_interface._use_soft_delete = hasattr(model, "is_active")

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
        Link the created GeneralManager subclass with its interface and the resolved Django model.

        Sets the interface's parent reference to the newly created manager class and, when a model is provided, records the manager class on the model. Also attaches manager instances to the created class: assigns `objects` from the interface's manager and, if the interface indicates soft-delete support, provides `all_objects` (configured with `only_active=False`). If the model lacks an `all_objects` attribute but soft-delete is used, the model's `all_objects` is ensured by falling back to its default manager.

        Parameters:
            new_class: The newly created GeneralManager subclass to be linked as the parent.
            interface_class: The interface class that should reference `new_class` as its parent.
            model: The Django model class managed by the interface; if provided, its `_general_manager_class` may be set.
        """
        interface_class._parent_class = new_class
        if model is not None:
            model._general_manager_class = new_class  # type: ignore[attr-defined]
        try:
            new_class.objects = interface_class._get_manager()  # type: ignore[attr-defined]
        except AttributeError:
            pass
        if (
            getattr(interface_class, "_use_soft_delete", False)
            and model is not None
            and hasattr(model, "all_objects")
        ):
            new_class.all_objects = interface_class._get_manager(  # type: ignore[attr-defined]
                only_active=False
            )
        elif getattr(interface_class, "_use_soft_delete", False) and model is not None:
            if not hasattr(model, "all_objects"):
                model.all_objects = model._default_manager  # type: ignore[attr-defined]
            new_class.all_objects = interface_class._get_manager(  # type: ignore[attr-defined]
                only_active=False
            )

    @classmethod
    def handle_interface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Get the pre- and post-creation hooks used by GeneralManagerMeta.

        Returns:
            tuple[classPreCreationMethod, classPostCreationMethod]: A tuple whose first element is the pre-creation hook (called before class creation) and whose second element is the post-creation hook (called after class creation).
        """
        return cls._pre_create, cls._post_create

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
            cls._resolve_model_class()
        return super().get_field_type(field_name)
