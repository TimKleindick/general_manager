"""Concrete interface providing CRUD operations via Django ORM."""

from __future__ import annotations

from general_manager.interface.database_based_interface import (
    GeneralManagerModel,
    InvalidFieldTypeError,
    InvalidFieldValueError,
    UnknownFieldError,
    WritableDBBasedInterface,
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
