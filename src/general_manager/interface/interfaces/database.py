"""Concrete interface providing CRUD operations via Django ORM."""

from __future__ import annotations

from typing import ClassVar

from general_manager.interface.bundles.database import ORM_WRITABLE_CAPABILITIES
from general_manager.interface.capabilities.configuration import CapabilityConfigEntry
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.interface.utils.errors import (
    InvalidFieldTypeError,
    InvalidFieldValueError,
    UnknownFieldError,
)
from general_manager.interface.utils.models import GeneralManagerModel

__all__ = [
    "DatabaseInterface",
    "InvalidFieldTypeError",
    "InvalidFieldValueError",
    "UnknownFieldError",
]


class DatabaseInterface(OrmInterfaceBase[GeneralManagerModel]):
    """CRUD-capable interface backed by a generated GeneralManager Django model.

    Manager subclasses declare Django model fields on this interface. The ORM
    lifecycle capability turns those declarations into a concrete model and the
    configured writable ORM capabilities provide query, create, update, delete,
    history, validation, and observability behavior.

    Construction uses `OrmInterfaceBase` inputs: the default public input is
    `id`, and `search_date` performs inherited point-in-time row lookup. Missing
    live or historical rows propagate the generated Django model's `DoesNotExist`
    exception. Write payload normalization can raise `UnknownFieldError` for
    unknown fields, `InvalidFieldValueError` when assignment raises `ValueError`,
    and `InvalidFieldTypeError` when assignment raises `TypeError`.
    """

    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        ORM_WRITABLE_CAPABILITIES,
    )
