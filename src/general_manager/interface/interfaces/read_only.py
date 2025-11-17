"""Read-only interface that mirrors JSON datasets into Django models."""

from __future__ import annotations

from typing import ClassVar, Type

from general_manager.interface.orm_interface import (
    OrmInterfaceBase,
)
from general_manager.interface.bundles.database import READ_ONLY_CAPABILITIES
from general_manager.interface.capabilities.configuration import CapabilityConfigEntry
from general_manager.interface.utils.models import GeneralManagerBasisModel

from general_manager.manager.general_manager import GeneralManager


class ReadOnlyInterface(OrmInterfaceBase[GeneralManagerBasisModel]):
    """Interface that reads static JSON data into a managed read-only model."""

    _interface_type: ClassVar[str] = "readonly"
    _parent_class: ClassVar[Type[GeneralManager]]
    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        READ_ONLY_CAPABILITIES,
    )
