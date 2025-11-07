"""Concrete interface providing CRUD operations via Django ORM."""

from __future__ import annotations

from general_manager.interface.database_based_interface import (
    GeneralManagerModel,
    WritableDBBasedInterface,
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


class DatabaseInterface(WritableDBBasedInterface[GeneralManagerModel]):
    """CRUD-capable interface backed by a dynamically generated Django model."""

    pass
