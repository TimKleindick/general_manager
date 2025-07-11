from __future__ import annotations
from datetime import datetime
from typing import Any, ClassVar

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
from general_manager.interface.excelDataField import ExcelDataField


class ExcelInterface(InterfaceBase):
    """Interface for importing and exporting data via Excel files."""

    _interface_type = "excel"
    _file_path: ClassVar[str | None] = None
    _file_mtime: ClassVar[float | None] = None
    input_fields: dict[str, Input]
    data_fields: dict[str, ExcelDataField]

    def getData(self, search_date: datetime | None = None) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    @classmethod
    def getAttributeTypes(cls) -> dict[str, AttributeTypedDict]:  # pragma: no cover - abstract
        raise NotImplementedError

    @classmethod
    def getAttributes(cls) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    @classmethod
    def filter(cls, **kwargs: Any):  # pragma: no cover - abstract
        raise NotImplementedError

    @classmethod
    def exclude(cls, **kwargs: Any):  # pragma: no cover - abstract
        raise NotImplementedError

    @classmethod
    def getFieldType(cls, field_name: str) -> type:  # pragma: no cover - abstract
        raise NotImplementedError

    @staticmethod
    def _preCreate(
        name: generalManagerClassName,
        attrs: attributes,
        interface: interfaceBaseClass,
    ) -> tuple[attributes, interfaceBaseClass, None]:
        input_fields: dict[str, Input[Any]] = {}
        data_fields: dict[str, ExcelDataField] = {}
        file_path: str | None = None

        for key, value in vars(interface).items():
            if key.startswith("__"):
                continue
            if isinstance(value, Input):
                input_fields[key] = value
            elif isinstance(value, ExcelDataField):
                data_fields[key] = value
            elif key == "Meta" and isinstance(value, type) and hasattr(value, "file_path"):
                file_path = getattr(value, "file_path")

        attrs["_interface_type"] = interface._interface_type
        interface_attrs = {
            "input_fields": input_fields,
            "data_fields": data_fields,
            "_file_mtime": None,
        }
        if file_path is not None:
            interface_attrs["_file_path"] = file_path
        interface_cls = type(interface.__name__, (interface,), interface_attrs)
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
        """Return handler functions used by the meta class during creation."""
        return cls._preCreate, cls._postCreate
