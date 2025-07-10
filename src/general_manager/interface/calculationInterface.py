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
        return {
            name: lambda self, name=name: cls.input_fields[name].cast(
                self.identification.get(name)
            )
            for name in cls.input_fields.keys()
        }

    @classmethod
    def filter(cls, **kwargs: Any) -> CalculationBucket:
        return CalculationBucket(cls._parent_class).filter(**kwargs)

    @classmethod
    def exclude(cls, **kwargs: Any) -> CalculationBucket:
        return CalculationBucket(cls._parent_class).exclude(**kwargs)

    @classmethod
    def all(cls) -> CalculationBucket:
        return CalculationBucket(cls._parent_class).all()

    @staticmethod
    def _preCreate(
        name: generalManagerClassName, attrs: attributes, interface: interfaceBaseClass
    ) -> tuple[attributes, interfaceBaseClass, None]:
        """
        Prepare and return updated attributes and a new interface class for GeneralManager creation.

        Collects all `Input` instances from the provided interface class, sets the interface type in the attributes, dynamically creates a new interface class with an `input_fields` attribute, and adds this class to the attributes dictionary.

        Returns:
            tuple: A tuple containing the updated attributes dictionary, the new interface class, and None.
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
        interface_class._parent_class = new_class

    @classmethod
    def handleInterface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        This method returns a pre and a post GeneralManager creation method
        and is called inside the GeneralManagerMeta class to initialize the
        Interface.
        The pre creation method is called before the GeneralManager instance
        is created to modify the kwargs.
        The post creation method is called after the GeneralManager instance
        is created to modify the instance and add additional data.
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
