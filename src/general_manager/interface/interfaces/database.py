"""Concrete interface providing CRUD operations via Django ORM."""

from __future__ import annotations

from typing import ClassVar

from general_manager.interface.bundles.database import ORM_WRITABLE_CAPABILITIES
from general_manager.interface.orm_interface import (
    GeneralManagerModel,
    OrmInterfaceBase,
)
from general_manager.interface.utils.errors import (
    InvalidFieldTypeError,
    InvalidFieldValueError,
    UnknownFieldError,
)

__all__ = [
    "DatabaseInterface",
    "InvalidFieldTypeError",
    "InvalidFieldValueError",
    "UnknownFieldError",
]


class DatabaseInterface(OrmInterfaceBase[GeneralManagerModel]):
    """CRUD-capable interface backed by a dynamically generated Django model."""

    configured_capabilities: ClassVar[tuple] = (ORM_WRITABLE_CAPABILITIES,)
