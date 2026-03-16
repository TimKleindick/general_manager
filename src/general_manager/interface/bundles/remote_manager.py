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
    RemoteManagerQueryCapability,
)
from general_manager.interface.capabilities.request import (
    RequestCreateCapability,
    RequestDeleteCapability,
    RequestLifecycleCapability,
    RequestReadCapability,
    RequestUpdateCapability,
    RequestValidationCapability,
)


REMOTE_MANAGER_CAPABILITIES = CapabilitySet(
    label="remote_manager",
    entries=(
        InterfaceCapabilityConfig(RequestLifecycleCapability),
        InterfaceCapabilityConfig(RequestReadCapability),
        InterfaceCapabilityConfig(RequestValidationCapability),
        InterfaceCapabilityConfig(RemoteManagerQueryCapability),
        InterfaceCapabilityConfig(RequestCreateCapability),
        InterfaceCapabilityConfig(RequestUpdateCapability),
        InterfaceCapabilityConfig(RequestDeleteCapability),
        InterfaceCapabilityConfig(LoggingObservabilityCapability),
    ),
)

__all__ = ["REMOTE_MANAGER_CAPABILITIES"]
