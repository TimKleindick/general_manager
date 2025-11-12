"""Interface implementation for calculation-style GeneralManager classes."""

from __future__ import annotations
from datetime import datetime
from typing import Any, ClassVar, cast
from general_manager.interface.base_interface import (
    InterfaceBase,
    classPostCreationMethod,
    classPreCreationMethod,
    generalManagerClassName,
    attributes,
    interfaceBaseClass,
    newlyCreatedGeneralManagerClass,
    newlyCreatedInterfaceClass,
    relatedClass,
    AttributeTypedDict,
)
from general_manager.manager.input import Input
from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.interface.capabilities.base import CapabilityName, Capability
from general_manager.interface.capabilities.calculation import (
    CalculationLifecycleCapability,
    CalculationQueryCapability,
    CalculationReadCapability,
)


class CalculationInterface(InterfaceBase):
    """Interface exposing calculation inputs without persisting data."""

    _interface_type: ClassVar[str] = "calculation"
    input_fields: ClassVar[dict[str, Input]]

    capability_overrides: ClassVar[dict[CapabilityName, type["Capability"]]] = {
        "calculation_lifecycle": CalculationLifecycleCapability,
        "read": CalculationReadCapability,
        "query": CalculationQueryCapability,
    }
    lifecycle_capability_name: ClassVar[CapabilityName | None] = "calculation_lifecycle"

    def get_data(self) -> Any:
        """Delegate to the base implementation so capabilities can respond."""
        return super().get_data()

    @classmethod
    def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
        """
        Return a dictionary describing the type and metadata for each input field in the calculation interface.

        Each entry includes the field's type, default value (`None`), and flags indicating that the field is not editable, is required, and is not derived.
        """
        return {
            name: {
                "type": field.type,
                "default": None,
                "is_editable": False,
                "is_required": True,
                "is_derived": False,
            }
            for name, field in cls.input_fields.items()
        }

    @classmethod
    def get_attributes(cls) -> dict[str, Any]:
        """Return attribute accessors that cast values using the configured inputs."""
        return {
            name: lambda self, name=name: cls.input_fields[name].cast(
                self.identification.get(name)
            )
            for name in cls.input_fields.keys()
        }

    @classmethod
    def filter(cls, **kwargs: Any) -> CalculationBucket:
        """Return a calculation bucket filtered by the given parameters."""
        handler = cls.get_capability_handler("query")
        if handler is not None and hasattr(handler, "filter"):
            return handler.filter(cls, **kwargs)  # type: ignore[return-value]
        return CalculationBucket(cls._parent_class).filter(**kwargs)

    @classmethod
    def exclude(cls, **kwargs: Any) -> CalculationBucket:
        """Return a calculation bucket excluding items matching the parameters."""
        handler = cls.get_capability_handler("query")
        if handler is not None and hasattr(handler, "exclude"):
            return handler.exclude(cls, **kwargs)  # type: ignore[return-value]
        return CalculationBucket(cls._parent_class).exclude(**kwargs)

    @classmethod
    def all(cls) -> CalculationBucket:
        """Return a calculation bucket containing all combinations."""
        handler = cls.get_capability_handler("query")
        if handler is not None and hasattr(handler, "all"):
            return handler.all(cls)  # type: ignore[return-value]
        return CalculationBucket(cls._parent_class).all()

    @classmethod
    def _pre_create(
        cls,
        _name: generalManagerClassName,
        attrs: attributes,
        interface: interfaceBaseClass,
    ) -> tuple[attributes, interfaceBaseClass, None]:
        """
        Prepare and attach a generated Interface subclass into the attributes for a GeneralManager class before its creation.

        Parameters:
            _name (generalManagerClassName): Name of the manager class being created.
            attrs (attributes): Mutable attribute dictionary for the manager class under construction; will be modified to include the generated Interface and interface type.
            interface (interfaceBaseClass): Base interface class from which the generated Interface subclass is derived.

        Returns:
            tuple[attributes, interfaceBaseClass, None]: The updated attributes dict, the newly created Interface subclass, and None for the related model.
        """
        capability = cls._calculation_lifecycle_capability()
        typed_interface = cast(type["CalculationInterface"], interface)
        return capability.pre_create(
            name=_name,
            attrs=attrs,
            interface=typed_interface,
        )

    @classmethod
    def _post_create(
        cls,
        new_class: newlyCreatedGeneralManagerClass,
        interface_class: newlyCreatedInterfaceClass,
        _model: relatedClass,
    ) -> None:
        capability = cls._calculation_lifecycle_capability()
        typed_interface = cast(type["CalculationInterface"], interface_class)
        capability.post_create(
            new_class=new_class,
            interface_class=typed_interface,
            model=None,
        )

    @classmethod
    def get_field_type(cls, field_name: str) -> type:
        """
        Get the Python type for an input field.

        Returns:
            The Python type associated with the specified input field.

        Raises:
            KeyError: If `field_name` is not present in `cls.input_fields`.
        """
        field = cls.input_fields.get(field_name)
        if field is None:
            raise KeyError(field_name)
        return field.type

    @classmethod
    def _calculation_lifecycle_capability(cls) -> CalculationLifecycleCapability:
        handler = cls.get_capability_handler("calculation_lifecycle")
        if isinstance(handler, CalculationLifecycleCapability):
            return handler
        return CalculationLifecycleCapability()
