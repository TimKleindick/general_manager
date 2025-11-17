"""Interface implementation for calculation-style GeneralManager classes."""

from __future__ import annotations

from typing import ClassVar

from general_manager.interface.base_interface import InterfaceBase
from general_manager.manager.input import Input
from general_manager.interface.bundles.calculation import CALCULATION_CORE_CAPABILITIES
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.configuration import CapabilityConfigEntry


class CalculationInterface(InterfaceBase):
    """Interface exposing calculation inputs without persisting data."""

    _interface_type: ClassVar[str] = "calculation"
    input_fields: ClassVar[dict[str, Input]]

    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        CALCULATION_CORE_CAPABILITIES,
    )
    lifecycle_capability_name: ClassVar[CapabilityName | None] = "calculation_lifecycle"
