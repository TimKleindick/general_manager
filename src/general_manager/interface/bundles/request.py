"""Capability bundles for request-backed interfaces."""

from __future__ import annotations

from general_manager.interface.capabilities.configuration import (
    CapabilitySet,
    InterfaceCapabilityConfig,
)
from general_manager.interface.capabilities.core.observability import (
    LoggingObservabilityCapability,
)
from general_manager.interface.capabilities.request import (
    RequestLifecycleCapability,
    RequestQueryCapability,
    RequestReadCapability,
    RequestValidationCapability,
)


REQUEST_CORE_CAPABILITIES = CapabilitySet(
    label="request_core",
    entries=(
        InterfaceCapabilityConfig(RequestLifecycleCapability),
        InterfaceCapabilityConfig(RequestReadCapability),
        InterfaceCapabilityConfig(RequestValidationCapability),
        InterfaceCapabilityConfig(RequestQueryCapability),
        InterfaceCapabilityConfig(LoggingObservabilityCapability),
    ),
)

REQUEST_CAPABILITIES = REQUEST_CORE_CAPABILITIES

__all__ = ["REQUEST_CAPABILITIES", "REQUEST_CORE_CAPABILITIES"]
