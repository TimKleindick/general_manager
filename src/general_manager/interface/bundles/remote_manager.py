"""Capability bundles for RemoteManagerInterface."""

from __future__ import annotations

from general_manager.interface.capabilities.configuration import (
    CapabilitySet,
    InterfaceCapabilityConfig,
)
from general_manager.interface.capabilities.core.observability import (
    LoggingObservabilityCapability,
)
from general_manager.interface.capabilities.remote_manager import (
    RemoteManagerCreateCapability,
    RemoteManagerDeleteCapability,
    RemoteManagerQueryCapability,
    RemoteManagerUpdateCapability,
)
from general_manager.interface.capabilities.request import (
    RequestLifecycleCapability,
    RequestReadCapability,
    RequestValidationCapability,
)


REMOTE_MANAGER_CAPABILITIES: CapabilitySet = CapabilitySet(
    label="remote_manager",
    entries=(
        InterfaceCapabilityConfig(RequestLifecycleCapability),
        InterfaceCapabilityConfig(RequestReadCapability),
        InterfaceCapabilityConfig(RequestValidationCapability),
        InterfaceCapabilityConfig(RemoteManagerQueryCapability),
        InterfaceCapabilityConfig(RemoteManagerCreateCapability),
        InterfaceCapabilityConfig(RemoteManagerUpdateCapability),
        InterfaceCapabilityConfig(RemoteManagerDeleteCapability),
        InterfaceCapabilityConfig(LoggingObservabilityCapability),
    ),
)

__all__ = ["REMOTE_MANAGER_CAPABILITIES"]
