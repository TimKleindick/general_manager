"""Capabilities tailored for calculation interfaces."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, ClassVar

from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.manager.input import Input

from ..base import CapabilityName
from ..builtin import BaseCapability
from ._compat import call_with_observability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.calculation import (
        CalculationInterface,
    )


class CalculationReadCapability(BaseCapability):
    """Calculations expose inputs only and never persist data."""

    name: ClassVar[CapabilityName] = "read"

    def get_data(self, interface_instance: "CalculationInterface") -> Any:
        raise NotImplementedError("Calculations do not store data.")

    def get_attribute_types(
        self,
        interface_cls: type["CalculationInterface"],
    ) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "type": field.type,
                "default": None,
                "is_editable": False,
                "is_required": True,
                "is_derived": False,
            }
            for name, field in interface_cls.input_fields.items()
        }

    def get_attributes(
        self,
        interface_cls: type["CalculationInterface"],
    ) -> dict[str, Any]:
        return {
            name: lambda self, name=name: interface_cls.input_fields[name].cast(
                self.identification.get(name)
            )
            for name in interface_cls.input_fields.keys()
        }

    def get_field_type(
        self,
        interface_cls: type["CalculationInterface"],
        field_name: str,
    ) -> type:
        field = interface_cls.input_fields.get(field_name)
        if field is None:
            raise KeyError(field_name)
        return field.type


class CalculationQueryCapability(BaseCapability):
    """Expose CalculationBucket helpers via the generic query capability."""

    name: ClassVar[CapabilityName] = "query"

    def filter(
        self,
        interface_cls: type["CalculationInterface"],
        **kwargs: Any,
    ) -> CalculationBucket:
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> CalculationBucket:
            return CalculationBucket(interface_cls._parent_class).filter(**kwargs)

        return call_with_observability(
            interface_cls,
            operation="calculation.query.filter",
            payload=payload_snapshot,
            func=_perform,
        )

    def exclude(
        self,
        interface_cls: type["CalculationInterface"],
        **kwargs: Any,
    ) -> CalculationBucket:
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> CalculationBucket:
            return CalculationBucket(interface_cls._parent_class).exclude(**kwargs)

        return call_with_observability(
            interface_cls,
            operation="calculation.query.exclude",
            payload=payload_snapshot,
            func=_perform,
        )

    def all(self, interface_cls: type["CalculationInterface"]) -> CalculationBucket:
        payload_snapshot: dict[str, Any] = {}

        def _perform() -> CalculationBucket:
            return CalculationBucket(interface_cls._parent_class).all()

        return call_with_observability(
            interface_cls,
            operation="calculation.query.all",
            payload=payload_snapshot,
            func=_perform,
        )


class CalculationLifecycleCapability(BaseCapability):
    """Manage calculation interface pre/post creation hooks."""

    name: ClassVar[CapabilityName] = "calculation_lifecycle"

    def pre_create(
        self,
        *,
        name: str,
        attrs: dict[str, Any],
        interface: type["CalculationInterface"],
    ) -> tuple[dict[str, Any], type["CalculationInterface"], None]:
        payload_snapshot = {
            "interface": interface.__name__,
            "name": name,
        }

        def _perform() -> tuple[dict[str, Any], type["CalculationInterface"], None]:
            input_fields: dict[str, Input[Any]] = {}
            for key, value in vars(interface).items():
                if key.startswith("__"):
                    continue
                if isinstance(value, Input):
                    input_fields[key] = value

            attrs["_interface_type"] = interface._interface_type
            interface_cls = type(
                interface.__name__,
                (interface,),
                {"input_fields": input_fields},
            )
            attrs["Interface"] = interface_cls
            return attrs, interface_cls, None

        return call_with_observability(
            interface,
            operation="calculation.pre_create",
            payload=payload_snapshot,
            func=_perform,
        )

    def post_create(
        self,
        *,
        new_class: type,
        interface_class: type["CalculationInterface"],
        model: None = None,
    ) -> None:
        payload_snapshot = {"interface": interface_class.__name__}

        def _perform() -> None:
            interface_class._parent_class = new_class  # type: ignore[attr-defined]

        return call_with_observability(
            interface_class,
            operation="calculation.post_create",
            payload=payload_snapshot,
            func=_perform,
        )
