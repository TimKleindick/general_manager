"""Preconfigured capability bundles for calculation interfaces."""

from __future__ import annotations

from general_manager.interface.capabilities.calculation import (
    CalculationLifecycleCapability,
    CalculationQueryCapability,
    CalculationReadCapability,
)
from general_manager.interface.capabilities.configuration import (
    CapabilitySet,
    InterfaceCapabilityConfig,
)

CALCULATION_CORE_CAPABILITIES = CapabilitySet(
    label="calculation_core",
    entries=(
        InterfaceCapabilityConfig(CalculationLifecycleCapability),
        InterfaceCapabilityConfig(CalculationReadCapability),
        InterfaceCapabilityConfig(CalculationQueryCapability),
    ),
)

__all__ = ["CALCULATION_CORE_CAPABILITIES"]
