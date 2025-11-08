"""Interface implementation for calculation-style GeneralManager classes."""

from __future__ import annotations
from datetime import datetime
from typing import Any, ClassVar
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


class CalculationInterface(InterfaceBase):
    """Interface exposing calculation inputs without persisting data."""

    _interface_type: ClassVar[str] = "calculation"
    input_fields: ClassVar[dict[str, Input]]

    def get_data(self) -> Any:
        """
        Indicates that calculation interfaces do not provide stored data and always raise a NotImplementedError.

        Raises:
            NotImplementedError: Always raised with the message "Calculations do not store data."
        """
        raise NotImplementedError("Calculations do not store data.")

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
        return CalculationBucket(cls._parent_class).filter(**kwargs)

    @classmethod
    def exclude(cls, **kwargs: Any) -> CalculationBucket:
        """Return a calculation bucket excluding items matching the parameters."""
        return CalculationBucket(cls._parent_class).exclude(**kwargs)

    @classmethod
    def all(cls) -> CalculationBucket:
        """Return a calculation bucket containing all combinations."""
        return CalculationBucket(cls._parent_class).all()

    @staticmethod
    def _pre_create(
        _name: generalManagerClassName, attrs: attributes, interface: interfaceBaseClass
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
        input_fields: dict[str, Input[Any]] = {}
        for key, value in vars(interface).items():
            if key.startswith("__"):
                continue
            if isinstance(value, Input):
                input_fields[key] = value

        attrs["_interface_type"] = interface._interface_type
        interface_cls = type(
            interface.__name__, (interface,), {"input_fields": input_fields}
        )
        attrs["Interface"] = interface_cls

        return attrs, interface_cls, None

    @staticmethod
    def _post_create(
        new_class: newlyCreatedGeneralManagerClass,
        interface_class: newlyCreatedInterfaceClass,
        _model: relatedClass,
    ) -> None:
        """
        Link the generated interface class to its manager class after creation.

        Parameters:
            new_class: The newly created GeneralManager class to attach.
            interface_class: The generated interface class that will reference the manager.
            _model: Unused placeholder for the related model class; ignored.

        Description:
            Sets `interface_class._parent_class` to `new_class` so the interface knows its owning manager.
        """
        interface_class._parent_class = new_class

    @classmethod
    def handle_interface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Return the pre- and post-creation hooks used by ``GeneralManagerMeta``.

        Returns:
            tuple[classPreCreationMethod, classPostCreationMethod]: Hook functions invoked around manager creation.
        """
        return cls._pre_create, cls._post_create

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
