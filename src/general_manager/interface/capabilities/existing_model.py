"""Capabilities specialized for ExistingModelInterface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

from django.apps import apps
from django.db import models
from simple_history import register  # type: ignore

from general_manager.factory.auto_factory import AutoFactory
from general_manager.interface.utils.errors import (
    InvalidModelReferenceError,
    MissingModelConfigurationError,
)
from general_manager.interface.models import get_full_clean_methode

from .base import CapabilityName
from .builtin import BaseCapability
from .utils import with_observability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.backends.existing_model.existing_model_interface import (
        ExistingModelInterface,
    )


@dataclass
class ExistingModelResolutionCapability(BaseCapability):
    """Resolve and configure the Django model used by ExistingModelInterface."""

    name: ClassVar[CapabilityName] = "existing_model_resolution"
    required_attributes: ClassVar[tuple[str, ...]] = ("get_capability_handler",)

    def resolve_model(
        self, interface_cls: type["ExistingModelInterface"]
    ) -> type[models.Model]:
        payload_snapshot = {"interface": interface_cls.__name__}

        def _perform() -> type[models.Model]:
            model_reference = getattr(interface_cls, "model", None)
            if model_reference is None:
                raise MissingModelConfigurationError(interface_cls.__name__)
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
            interface_cls._model = model  # type: ignore[assignment]
            interface_cls.model = model
            interface_cls._use_soft_delete = hasattr(model, "is_active")
            return model

        return with_observability(
            interface_cls,
            operation="existing_model.resolve",
            payload=payload_snapshot,
            func=_perform,
        )

    def ensure_history(
        self,
        model: type[models.Model],
        interface_cls: type["ExistingModelInterface"] | None = None,
    ) -> None:
        payload_snapshot = {
            "interface": interface_cls.__name__ if interface_cls else None,
            "model": getattr(model, "__name__", str(model)),
        }

        def _perform() -> None:
            if hasattr(model._meta, "simple_history_manager_attribute"):
                return
            m2m_fields = [field.name for field in model._meta.local_many_to_many]
            register(model, m2m_fields=m2m_fields)

        target = interface_cls or model
        return with_observability(
            target,
            operation="existing_model.ensure_history",
            payload=payload_snapshot,
            func=_perform,
        )

    def apply_rules(
        self,
        interface_cls: type["ExistingModelInterface"],
        model: type[models.Model],
    ) -> None:
        payload_snapshot = {
            "interface": interface_cls.__name__,
            "model": getattr(model, "__name__", str(model)),
        }

        def _perform() -> None:
            meta_class = getattr(interface_cls, "Meta", None)
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

        return with_observability(
            interface_cls,
            operation="existing_model.apply_rules",
            payload=payload_snapshot,
            func=_perform,
        )

    def pre_create(
        self,
        *,
        name: str,
        attrs: dict[str, Any],
        interface: type["ExistingModelInterface"],
    ) -> tuple[dict[str, Any], type["ExistingModelInterface"], type[models.Model]]:
        payload_snapshot = {
            "interface": interface.__name__,
            "name": name,
        }

        def _perform() -> tuple[
            dict[str, Any], type["ExistingModelInterface"], type[models.Model]
        ]:
            interface_cls = cast(type["ExistingModelInterface"], interface)
            model = self.resolve_model(interface_cls)
            self.ensure_history(model, interface_cls)
            self.apply_rules(interface_cls, model)
            concrete_interface = cast(
                type["ExistingModelInterface"],
                type(interface.__name__, (interface,), {}),
            )
            concrete_interface._model = model  # type: ignore[attr-defined]
            concrete_interface.model = model
            concrete_interface._use_soft_delete = hasattr(model, "is_active")
            concrete_interface._field_descriptors = None  # type: ignore[attr-defined]
            attrs["_interface_type"] = interface_cls._interface_type
            attrs["Interface"] = concrete_interface
            manager_factory = cast(type | None, attrs.pop("Factory", None))
            factory_definition = manager_factory or getattr(
                interface_cls, "Factory", None
            )
            attrs["Factory"] = self.build_factory(
                name=name,
                interface_cls=concrete_interface,
                model=model,
                factory_definition=factory_definition,
            )
            return attrs, concrete_interface, model

        return with_observability(
            interface,
            operation="existing_model.pre_create",
            payload=payload_snapshot,
            func=_perform,
        )

    def post_create(
        self,
        *,
        new_class: type,
        interface_class: type["ExistingModelInterface"],
        model: type[models.Model] | None,
    ) -> None:
        payload_snapshot = {
            "interface": interface_class.__name__,
            "model": getattr(model, "__name__", None) if model else None,
        }

        def _perform() -> None:
            if model is None:
                return
            interface_class._parent_class = new_class  # type: ignore[attr-defined]
            model._general_manager_class = new_class  # type: ignore[attr-defined]
            try:
                new_class.objects = interface_class._get_manager()  # type: ignore[attr-defined]
            except AttributeError:
                pass
            if getattr(interface_class, "_use_soft_delete", False):
                if hasattr(model, "all_objects"):
                    new_class.all_objects = interface_class._get_manager(  # type: ignore[attr-defined]
                        only_active=False
                    )
                else:
                    model.all_objects = model._default_manager  # type: ignore[attr-defined]
                    new_class.all_objects = interface_class._get_manager(  # type: ignore[attr-defined]
                        only_active=False
                    )

        return with_observability(
            interface_class,
            operation="existing_model.post_create",
            payload=payload_snapshot,
            func=_perform,
        )

    def build_factory(
        self,
        *,
        name: str,
        interface_cls: type["ExistingModelInterface"],
        model: type[models.Model],
        factory_definition: type | None = None,
    ) -> type[AutoFactory]:
        factory_definition = factory_definition or getattr(
            interface_cls, "Factory", None
        )
        factory_attributes: dict[str, Any] = {}
        if factory_definition:
            for attr_name, attr_value in factory_definition.__dict__.items():
                if not attr_name.startswith("__"):
                    factory_attributes[attr_name] = attr_value
        factory_attributes["interface"] = interface_cls
        factory_attributes["Meta"] = type("Meta", (), {"model": model})
        return type(f"{name}Factory", (AutoFactory,), factory_attributes)
