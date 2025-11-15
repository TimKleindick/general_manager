"""Read-only interface that mirrors JSON datasets into Django models."""

from __future__ import annotations

from typing import ClassVar, Type, cast

from django.core.checks import Warning
from django.db import models

from general_manager.interface.backends.database.database_based_interface import (
    OrmPersistenceInterface,
)
from general_manager.interface.backends.database.capability_sets import (
    READ_ONLY_CAPABILITIES,
)
from general_manager.interface.capabilities.configuration import CapabilityConfigEntry
from general_manager.interface.capabilities.read_only import (
    ReadOnlyManagementCapability,
)
from general_manager.interface.models import GeneralManagerBasisModel

from general_manager.manager.general_manager import GeneralManager


class ReadOnlyInterface(OrmPersistenceInterface[GeneralManagerBasisModel]):
    """Interface that reads static JSON data into a managed read-only model."""

    _interface_type: ClassVar[str] = "readonly"
    _parent_class: ClassVar[Type[GeneralManager]]
    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        READ_ONLY_CAPABILITIES,
    )

    @classmethod
    def get_unique_fields(cls, model: Type[models.Model]) -> set[str]:
        """Delegate unique-field detection to the read-only capability."""
        capability = cast(
            ReadOnlyManagementCapability,
            cls.require_capability(
                "read_only_management",
                expected_type=ReadOnlyManagementCapability,
            ),
        )
        return capability.get_unique_fields(model)

    @classmethod
    def ensure_schema_is_up_to_date(
        cls,
        new_manager_class: Type[GeneralManager],
        model: Type[models.Model],
    ) -> list[Warning]:
        """Delegate schema verification to the read-only capability."""
        capability = cast(
            ReadOnlyManagementCapability,
            cls.require_capability(
                "read_only_management",
                expected_type=ReadOnlyManagementCapability,
            ),
        )
        return capability.ensure_schema_is_up_to_date(
            cls,
            new_manager_class,
            model,
        )

    @classmethod
    def sync_data(cls) -> None:
        """Synchronize data by delegating to the capability implementation."""
        capability = cast(
            ReadOnlyManagementCapability,
            cls.require_capability(
                "read_only_management",
                expected_type=ReadOnlyManagementCapability,
            ),
        )
        capability.sync_data(cls)
