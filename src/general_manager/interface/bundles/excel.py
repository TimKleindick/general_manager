"""Capability bundle for Excel-backed interfaces."""

from __future__ import annotations

from general_manager.interface.capabilities.configuration import (
    CapabilitySet,
    InterfaceCapabilityConfig,
)
from general_manager.interface.capabilities.excel import (
    ExcelCreateCapability,
    ExcelDeleteCapability,
    ExcelLifecycleCapability,
    ExcelQueryCapability,
    ExcelReadCapability,
    ExcelSyncCapability,
    ExcelUpdateCapability,
)


EXCEL_CAPABILITIES = CapabilitySet(
    label="excel_core",
    entries=(
        InterfaceCapabilityConfig(ExcelLifecycleCapability),
        InterfaceCapabilityConfig(ExcelReadCapability),
        InterfaceCapabilityConfig(ExcelQueryCapability),
        InterfaceCapabilityConfig(ExcelSyncCapability),
        InterfaceCapabilityConfig(ExcelCreateCapability),
        InterfaceCapabilityConfig(ExcelUpdateCapability),
        InterfaceCapabilityConfig(ExcelDeleteCapability),
    ),
)

__all__ = ["EXCEL_CAPABILITIES"]
