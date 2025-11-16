"""Lifecycle capability for ORM-backed interfaces."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, ClassVar, Type, cast

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
        factory_attributes: dict[str, Any] = {}
        if factory_definition:
            for attr_name, attr_value in factory_definition.__dict__.items():
                if not attr_name.startswith("__"):
                    factory_attributes[attr_name] = attr_value
        factory_attributes["interface"] = interface_cls
        factory_attributes["Meta"] = type("Meta", (), {"model": model})
        return type(f"{name}Factory", (AutoFactory,), factory_attributes)
