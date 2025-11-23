"""Lifecycle capability for ORM-backed interfaces."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, ClassVar, cast

from django.db import models
from general_manager.factory.auto_factory import AutoFactory
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.utils.models import (
    GeneralManagerBasisModel,
    GeneralManagerModel,
    SoftDeleteGeneralManagerModel,
    SoftDeleteMixin,
    get_full_clean_methode,
)
from general_manager.rule import Rule

from .support import get_support_capability, is_soft_delete_enabled

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.orm_interface import OrmInterfaceBase


class OrmLifecycleCapability(BaseCapability):
    """Handle creation and configuration of ORM-backed interfaces."""

    name: ClassVar[CapabilityName] = "orm_lifecycle"

    def pre_create(
        self,
        *,
        name: str,
        attrs: dict[str, Any],
        interface: type["OrmInterfaceBase"],
        base_model_class: type[GeneralManagerBasisModel],
    ) -> tuple[
        dict[str, Any], type["OrmInterfaceBase"], type[GeneralManagerBasisModel]
    ]:
        """
        Prepare ORM model, concrete interface class, and factory before the parent class is created.

        Creates a Django model class from fields discovered on the provided interface (applying any Meta configuration such as soft-delete and rules), finalizes its metadata, builds a concrete interface subclass bound to that model, and installs a Factory class and Interface class into the provided attrs mapping.

        Parameters:
            name (str): The name to use for the generated model and factory.
            attrs (dict[str, Any]): The attribute dict for the class being created; will be updated with "Interface", "Factory", and "_interface_type".
            interface (type[OrmInterfaceBase]): The interface class that defines model fields and optional Meta/Factory configuration.
            base_model_class (type[GeneralManagerBasisModel]): Base model class to derive the generated model from (may be adjusted for soft-delete support).

        Returns:
            tuple[dict[str, Any], type[OrmInterfaceBase], type[GeneralManagerBasisModel]]:
                - The possibly-modified attrs mapping.
                - The concrete interface subclass bound to the generated model.
                - The generated Django model class.
        """
        model_fields, meta_class = self._collect_model_fields(interface)
        model_fields["__module__"] = attrs.get("__module__")
        meta_class, use_soft_delete, rules = self._apply_meta_configuration(meta_class)
        if meta_class:
            model_fields["Meta"] = meta_class
        base_classes = self._determine_model_bases(base_model_class, use_soft_delete)
        model = cast(
            type[GeneralManagerBasisModel],
            type(name, base_classes, model_fields),
        )
        self._finalize_model_class(
            model,
            meta_class=meta_class,
            use_soft_delete=use_soft_delete,
            rules=rules,
        )
        attrs["_interface_type"] = interface._interface_type
        interface_cls = self._build_interface_class(interface, model, use_soft_delete)
        attrs["Interface"] = interface_cls

        manager_factory = cast(type | None, attrs.pop("Factory", None))
        factory_definition = manager_factory or getattr(interface, "Factory", None)
        attrs["Factory"] = self._build_factory_class(
            name=name,
            factory_definition=factory_definition,
            interface_cls=interface_cls,
            model=model,
        )

        return attrs, interface_cls, model

    def post_create(
        self,
        *,
        new_class: type,
        interface_class: type["OrmInterfaceBase"],
        model: type[GeneralManagerBasisModel] | None,
    ) -> None:
        """
        Attach the created interface class and model to each other and assign ORM managers to the new class.

        This function links the generated interface and model to the newly created class by setting the interface's parent class and the model's general manager class. It also obtains and assigns the default manager to new_class.objects, and if soft-delete is enabled for the interface, assigns an all_objects manager that includes inactive/soft-deleted records.

        Parameters:
            new_class (type): The class that was just created for the interface (the concrete manager/interface class).
            interface_class (type[OrmInterfaceBase]): The concrete interface subclass bound to the model.
            model (type[GeneralManagerBasisModel] | None): The ORM model class created for the interface, or None if no model was generated.
        """
        if model is None:
            return
        interface_class._parent_class = new_class  # type: ignore[attr-defined]
        model._general_manager_class = new_class  # type: ignore[attr-defined]
        support = get_support_capability(interface_class)
        new_class.objects = support.get_manager(interface_class)  # type: ignore[attr-defined]
        if is_soft_delete_enabled(interface_class):
            new_class.all_objects = support.get_manager(  # type: ignore[attr-defined]
                interface_class,
                only_active=False,
            )

    def _collect_model_fields(
        self,
        interface: type["OrmInterfaceBase"],
    ) -> tuple[dict[str, Any], type | None]:
        """
        Collect model field definitions and an optional Meta class from the given interface.

        Parameters:
            interface (type[OrmInterfaceBase]): Interface class to inspect for model field definitions and an optional nested `Meta` class.

        Returns:
            model_fields (dict[str, Any]): Mapping of attribute names to values to be used as model fields; excludes dunder attributes, a nested `Factory`, and any names returned by custom field handling, and includes custom fields discovered on the interface.
            meta_class (type | None): The nested `Meta` class found on the interface, or `None` if none is present.
        """
        custom_fields, ignore_fields = self._handle_custom_fields(interface)
        model_fields: dict[str, Any] = {}
        meta_class: type | None = None
        for attr_name, attr_value in interface.__dict__.items():
            if attr_name.startswith("__"):
                continue
            if attr_name == "Meta" and isinstance(attr_value, type):
                meta_class = attr_value
            elif attr_name == "Factory":
                continue
            elif attr_name in ignore_fields:
                continue
            else:
                model_fields[attr_name] = attr_value
        model_fields.update(custom_fields)
        return model_fields, meta_class

    def _handle_custom_fields(
        self,
        interface: type["OrmInterfaceBase"],
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Collect custom Django model Field attributes defined on an interface (or its attached model) and produce a mapping of those fields plus related ignore markers.

        Parameters:
            interface (type): The interface class to inspect. If the interface defines an attribute `_model`, that model is inspected instead.

        Returns:
            tuple[dict[str, django.db.models.Field], list[str]]: A 2-tuple where the first element is a mapping from attribute name to the discovered `Field` instance, and the second element is a list of generated ignore names in the form `<field_name>_value` and `<field_name>_unit`.
        """
        model = getattr(interface, "_model", None) or interface
        field_names: dict[str, models.Field] = {}
        ignore: list[str] = []
        for attr_name, attr_value in model.__dict__.items():
            if isinstance(attr_value, models.Field):
                ignore.append(f"{attr_value.name}_value")
                ignore.append(f"{attr_value.name}_unit")
                field_names[attr_name] = attr_value
        return field_names, ignore

    def describe_custom_fields(
        self,
        model: type[models.Model] | models.Model,
    ) -> tuple[list[str], list[str]]:
        """
        List Django Field names declared on the given model and generate ignore markers for each field's value and unit.

        Parameters:
            model (type[models.Model] | models.Model): Model class or instance to inspect for attributes that are instances of Django `models.Field`.

        Returns:
            field_names (list[str]): Names of discovered model fields (uses the field's `name` attribute if present, otherwise the attribute name).
            ignore (list[str]): Generated ignore markers in the form `<field_name>_value` and `<field_name>_unit` for each discovered field.
        """
        field_names: list[str] = []
        ignore: list[str] = []
        for attr_name, attr_value in model.__dict__.items():
            if isinstance(attr_value, models.Field):
                recorded_name = getattr(attr_value, "name", attr_name)
                field_names.append(recorded_name)
                ignore.append(f"{recorded_name}_value")
                ignore.append(f"{recorded_name}_unit")
        return field_names, ignore

    def _apply_meta_configuration(
        self,
        meta_class: type | None,
    ) -> tuple[type | None, bool, list[Any] | None]:
        """
        Extract soft-delete and rules configuration from a Meta class and remove those attributes from it.

        Parameters:
            meta_class (type | None): Optional Meta class provided on an interface; may contain `use_soft_delete` and/or `rules` attributes.

        Returns:
            tuple[type | None, bool, list[Any] | None]: A tuple of (meta_class, use_soft_delete, rules) where `meta_class` is the original Meta class with any extracted attributes removed, `use_soft_delete` is `true` if the Meta specified soft-delete, `false` otherwise, and `rules` is the list of rules from Meta or `None` if not present.
        """
        use_soft_delete = False
        rules: list[Any] | None = None
        if meta_class is None:
            return None, use_soft_delete, rules
        if hasattr(meta_class, "use_soft_delete"):
            use_soft_delete = meta_class.use_soft_delete
            delattr(meta_class, "use_soft_delete")
        if hasattr(meta_class, "rules"):
            rules = cast(list[Rule], meta_class.rules)
            delattr(meta_class, "rules")
        return meta_class, use_soft_delete, rules

    def _determine_model_bases(
        self,
        base_model_class: type[GeneralManagerBasisModel],
        use_soft_delete: bool,
    ) -> tuple[type[models.Model], ...]:
        """
        Selects the appropriate base model class tuple for a generated model, taking optional soft-delete support into account.

        Parameters:
            base_model_class (type[GeneralManagerBasisModel]): The default base model class to use.
            use_soft_delete (bool): Whether the generated model should support soft-delete.

        Returns:
            tuple[type[models.Model], ...]: A tuple of model base classes to use.
                - If `use_soft_delete` is False, returns `(base_model_class,)`.
                - If `use_soft_delete` is True and a soft-delete-aware substitute exists, returns that single substitute base.
                - If `use_soft_delete` is True and `base_model_class` already supports soft-delete, returns `(base_model_class,)`.
                - Otherwise returns `(SoftDeleteMixin, base_model_class)`.
        """
        if not use_soft_delete:
            return (base_model_class,)
        if (
            base_model_class is GeneralManagerModel
            or base_model_class is GeneralManagerBasisModel
        ) and issubclass(SoftDeleteGeneralManagerModel, base_model_class):
            return (SoftDeleteGeneralManagerModel,)
        if issubclass(base_model_class, SoftDeleteMixin):
            return (base_model_class,)
        return (cast(type[models.Model], SoftDeleteMixin), base_model_class)

    def _finalize_model_class(
        self,
        model: type[GeneralManagerBasisModel],
        *,
        meta_class: type | None,
        use_soft_delete: bool,
        rules: list[Any] | None,
    ) -> None:
        """
        Apply final metadata and soft-delete configuration to a generated model class.

        When a `meta_class` and `rules` are provided, attach the `rules` to `model._meta` and set a model-specific `full_clean` method. When `meta_class` is provided and `use_soft_delete` is true, mark the model's metadata to indicate soft-delete should be used.

        Parameters:
            model (type[GeneralManagerBasisModel]): The generated ORM model class to modify.
            meta_class (type | None): The Meta class discovered during model construction; if None no metadata is applied.
            use_soft_delete (bool): Whether soft-delete semantics should be enabled on the model.
            rules (list[Any] | None): Validation or business rules to attach to the model metadata; ignored if None.
        """
        if meta_class and rules:
            model._meta.rules = rules  # type: ignore[attr-defined]
            model.full_clean = get_full_clean_methode(model)  # type: ignore[assignment]
        if meta_class and use_soft_delete:
            model._meta.use_soft_delete = use_soft_delete  # type: ignore[attr-defined]

    def _build_interface_class(
        self,
        interface: type["OrmInterfaceBase"],
        model: type[GeneralManagerBasisModel],
        use_soft_delete: bool,
    ) -> type["OrmInterfaceBase"]:
        """
        Create a concrete interface subclass bound to a generated ORM model.

        The returned class is a new type that inherits from the provided interface and has its `_model` attribute set to the given model, `_soft_delete_default` set according to the `use_soft_delete` flag, and `_field_descriptors` initialized to None.

        Parameters:
            interface (type): The abstract interface class to subclass.
            model (type): The generated ORM model class to attach to the interface.
            use_soft_delete (bool): If True, the interface's `_soft_delete_default` is set to True.

        Returns:
            type: A new interface subclass bound to `model`.
        """
        interface_cls = type(interface.__name__, (interface,), {})
        interface_cls._model = model  # type: ignore[attr-defined]
        interface_cls._soft_delete_default = use_soft_delete  # type: ignore[attr-defined]
        interface_cls._field_descriptors = None  # type: ignore[attr-defined]
        return interface_cls

    def _build_factory_class(
        self,
        *,
        name: str,
        factory_definition: type | None,
        interface_cls: type["OrmInterfaceBase"],
        model: type[GeneralManagerBasisModel],
    ) -> type[AutoFactory]:
        """
        Create a Factory subclass bound to the given interface and model.

        Parameters:
            name (str): Base name used to construct the factory class name (produces "<name>Factory").
            factory_definition (type | None): Optional existing factory class whose non-dunder attributes will be copied into the new factory; if None, no attributes are copied.
            interface_cls (type[OrmInterfaceBase]): Interface class to attach to the factory as the `interface` attribute.
            model (type[GeneralManagerBasisModel]): Django model class to bind to the factory via an inner `Meta.model`.

        Returns:
            type[AutoFactory]: A new Factory class (subclass of `AutoFactory`) named "<name>Factory" with the prepared attributes and Meta.model set to `model`.
        """
        factory_attributes: dict[str, Any] = {}
        if factory_definition:
            for attr_name, attr_value in factory_definition.__dict__.items():
                if not attr_name.startswith("__"):
                    factory_attributes[attr_name] = attr_value
        factory_attributes["interface"] = interface_cls
        factory_attributes["Meta"] = type("Meta", (), {"model": model})
        return type(f"{name}Factory", (AutoFactory,), factory_attributes)
