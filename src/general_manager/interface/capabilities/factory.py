"""Factory helpers for instantiating capabilities by name."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

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
"""Default capability handler classes keyed by public capability name."""


CapabilityOverride = Callable[[], Capability] | type[Capability]
"""Per-name override accepted by :func:`build_capabilities`."""


def build_capabilities(
    interface_cls: type[object],
    names: Iterable[CapabilityName],
    overrides: Mapping[CapabilityName, CapabilityOverride],
) -> list[Capability]:
    """
    Instantiate capability handlers for the supplied names.

    Parameters:
        interface_cls: Interface class the capabilities will be associated with.
            This compatibility context is accepted for callers that already have
            an interface class available; the current implementation does not
            inspect or pass it to handlers.
        names: Capability names to instantiate. The iterable is consumed once
            and order is preserved. Duplicate names create duplicate handler
            instances or call the override once per occurrence.
        overrides: Per-name overrides. When a name exists in this mapping and
            the value is not ``None`` at runtime, the override takes precedence
            over :data:`CAPABILITY_CLASS_MAP`. Override values are called with no
            arguments; class overrides are instantiated the same way as other
            zero-argument callables.

    Returns:
        A mutable list of capability instances in the same order as ``names``.

    Raises:
        KeyError: If a requested name has no non-``None`` override and is absent
            from :data:`CAPABILITY_CLASS_MAP`.
        Exception: Exceptions raised while iterating ``names``, reading
            ``overrides``, or calling the selected handler propagate unchanged.
    """
    del interface_cls
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
