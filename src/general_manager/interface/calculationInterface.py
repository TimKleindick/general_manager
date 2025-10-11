"""Interface implementation for calculation-style GeneralManager classes."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from general_manager.interface.baseInterface import (
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
from general_manager.bucket.calculationBucket import CalculationBucket


class CalculationInterface(InterfaceBase):
    """Interface exposing calculation inputs without persisting data."""
    _interface_type = "calculation"
    input_fields: dict[str, Input]

    def getData(self, search_date: datetime | None = None) -> Any:
        raise NotImplementedError("Calculations do not store data.")

    @classmethod
    def getAttributeTypes(cls) -> dict[str, AttributeTypedDict]:
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
    def getAttributes(cls) -> dict[str, Any]:
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
    def _preCreate(
        name: generalManagerClassName, attrs: attributes, interface: interfaceBaseClass
    ) -> tuple[attributes, interfaceBaseClass, None]:
        """
        Prepare interface attributes prior to GeneralManager class creation.

        Parameters:
            name (generalManagerClassName): Name of the new manager class.
            attrs (attributes): Attribute dictionary for the manager being created.
            interface (interfaceBaseClass): Base interface definition.

        Returns:
            tuple[attributes, interfaceBaseClass, None]: Updated attributes, interface class, and related model (None).
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
    def _postCreate(
        new_class: newlyCreatedGeneralManagerClass,
        interface_class: newlyCreatedInterfaceClass,
        model: relatedClass,
    ) -> None:
        """Link the generated interface to the manager class after creation."""
        interface_class._parent_class = new_class

    @classmethod
    def handleInterface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Return the pre- and post-creation hooks used by ``GeneralManagerMeta``.

        Returns:
            tuple[classPreCreationMethod, classPostCreationMethod]: Hook functions invoked around manager creation.
        """
        return cls._preCreate, cls._postCreate

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        """
        Return the Python type of the specified input field.

        Parameters:
            field_name (str): The name of the input field.

        Returns:
            type: The Python type associated with the input field.

        Raises:
            KeyError: If the specified field name does not exist in input_fields.
        """
        field = cls.input_fields.get(field_name)
        if field is None:
            raise KeyError(f"Field '{field_name}' not found in input fields.")
        return field.type
