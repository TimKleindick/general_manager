"""Factory helpers for instantiating capabilities by name."""

from __future__ import annotations

from typing import Iterable

from typing import Mapping

from .base import Capability, CapabilityName
from .builtin import (
    AccessControlCapability,
    CreateCapability,
    DeleteCapability,
    HistoryCapability,
    NotificationCapability,
    ObservabilityCapability,
    ReadCapability,
    SchedulingCapability,
    UpdateCapability,
    ValidationCapability,
)


CAPABILITY_CLASS_MAP: dict[CapabilityName, type[Capability]] = {
    "read": ReadCapability,
    "create": CreateCapability,
    "update": UpdateCapability,
    "delete": DeleteCapability,
    "history": HistoryCapability,
    "validation": ValidationCapability,
    "notification": NotificationCapability,
    "scheduling": SchedulingCapability,
    "access_control": AccessControlCapability,
    "observability": ObservabilityCapability,
}


def build_capabilities(
    interface_cls: type,
    names: Iterable[CapabilityName],
    overrides: Mapping[CapabilityName, type[Capability]],
) -> list[Capability]:
    """Instantiate capability objects for the provided names."""
    instances: list[Capability] = []
    for name in names:
        capability_cls = overrides.get(name) or CAPABILITY_CLASS_MAP.get(name)
        if capability_cls is None:
            message = f"Unknown capability '{name}'"
            raise KeyError(message)
        instances.append(capability_cls())
    return instances
