"""Capability bundles for ORM-backed interfaces."""

from __future__ import annotations

from general_manager.interface.capabilities.configuration import (
    CapabilitySet,
    InterfaceCapabilityConfig,
)
from general_manager.interface.capabilities.existing_model import (
    ExistingModelResolutionCapability,
)
from general_manager.interface.capabilities.core.observability import (
    LoggingObservabilityCapability,
)
from general_manager.interface.capabilities.orm import (
    OrmCreateCapability,
    OrmDeleteCapability,
    OrmHistoryCapability,
    OrmLifecycleCapability,
    OrmMutationCapability,
    OrmPersistenceSupportCapability,
    OrmQueryCapability,
    OrmReadCapability,
    SoftDeleteCapability,
    OrmUpdateCapability,
    OrmValidationCapability,
)
from general_manager.interface.capabilities.read_only import (
    ReadOnlyLifecycleCapability,
    ReadOnlyManagementCapability,
)


ORM_PERSISTENCE_CAPABILITIES = CapabilitySet(
    label="orm_persistence_core",
    entries=(
        InterfaceCapabilityConfig(OrmPersistenceSupportCapability),
        InterfaceCapabilityConfig(OrmLifecycleCapability),
        InterfaceCapabilityConfig(SoftDeleteCapability),
        InterfaceCapabilityConfig(OrmReadCapability),
        InterfaceCapabilityConfig(OrmValidationCapability),
        InterfaceCapabilityConfig(OrmHistoryCapability),
        InterfaceCapabilityConfig(OrmQueryCapability),
        InterfaceCapabilityConfig(LoggingObservabilityCapability),
    ),
)

ORM_WRITABLE_CAPABILITIES = CapabilitySet(
    label="orm_writable_core",
    entries=(
        *ORM_PERSISTENCE_CAPABILITIES.entries,
        InterfaceCapabilityConfig(OrmMutationCapability),
        InterfaceCapabilityConfig(OrmCreateCapability),
        InterfaceCapabilityConfig(OrmUpdateCapability),
        InterfaceCapabilityConfig(OrmDeleteCapability),
    ),
)

EXISTING_MODEL_CAPABILITIES = CapabilitySet(
    label="existing_model_core",
    entries=(
        *ORM_WRITABLE_CAPABILITIES.entries,
        InterfaceCapabilityConfig(ExistingModelResolutionCapability),
    ),
)

READ_ONLY_CAPABILITIES = CapabilitySet(
    label="read_only_core",
    entries=(
        InterfaceCapabilityConfig(OrmPersistenceSupportCapability),
        InterfaceCapabilityConfig(ReadOnlyLifecycleCapability),
        InterfaceCapabilityConfig(SoftDeleteCapability),
        InterfaceCapabilityConfig(OrmReadCapability),
        InterfaceCapabilityConfig(OrmValidationCapability),
        InterfaceCapabilityConfig(OrmHistoryCapability),
        InterfaceCapabilityConfig(OrmQueryCapability),
        InterfaceCapabilityConfig(LoggingObservabilityCapability),
        InterfaceCapabilityConfig(ReadOnlyManagementCapability),
    ),
)

__all__ = [
    "EXISTING_MODEL_CAPABILITIES",
    "ORM_PERSISTENCE_CAPABILITIES",
    "ORM_WRITABLE_CAPABILITIES",
    "READ_ONLY_CAPABILITIES",
]
