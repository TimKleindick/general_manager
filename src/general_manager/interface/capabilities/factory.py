"""Factory helpers for instantiating capabilities by name."""

from __future__ import annotations

from collections.abc import Callable
from typing import Iterable, Mapping

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


CapabilityOverride = Callable[[], Capability] | type[Capability]


def build_capabilities(
    interface_cls: type,
    names: Iterable[CapabilityName],
    overrides: Mapping[CapabilityName, CapabilityOverride],
) -> list[Capability]:
    """
    Builds capability instances for the given capability names.

    Parameters:
        interface_cls (type): Interface class the capabilities will be associated with (may be unused by built-in factories).
        names (Iterable[CapabilityName]): Iterable of capability names to instantiate, in the desired order.
        overrides (Mapping[CapabilityName, CapabilityOverride]): Per-name overrides; each value is either a Capability class (instantiated) or a zero-argument callable that returns a Capability.

    Returns:
        list[Capability]: A list of instantiated Capability objects corresponding to `names`, in the same order.

    Raises:
        KeyError: If a name from `names` does not exist in CAPABILITY_CLASS_MAP and no override is provided.
    """
    instances: list[Capability] = []
    for name in names:
        override = overrides.get(name)
        if override is not None:
            if isinstance(override, type):
                instances.append(override())
            else:
                instances.append(override())
            continue
        capability_cls = CAPABILITY_CLASS_MAP.get(name)
        if capability_cls is None:
            message = f"Unknown capability '{name}'"
            raise KeyError(message)
        instances.append(capability_cls())
    return instances
