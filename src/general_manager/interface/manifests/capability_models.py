"""Data models describing capability plans and selections."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from general_manager.interface.capabilities import CapabilityName


@dataclass(frozen=True, slots=True)
class CapabilityPlan:
    """Declarative plan describing required and optional capabilities."""

    required: frozenset[CapabilityName] = field(default_factory=frozenset)
    optional: frozenset[CapabilityName] = field(default_factory=frozenset)
    flags: Mapping[str, CapabilityName] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "required", frozenset(self.required))
        object.__setattr__(self, "optional", frozenset(self.optional))
        object.__setattr__(self, "flags", MappingProxyType(dict(self.flags)))


@dataclass(slots=True)
class CapabilityConfig:
    """Runtime configuration used to enable or disable optional capabilities."""

    enabled: set[CapabilityName] = field(default_factory=set)
    disabled: set[CapabilityName] = field(default_factory=set)
    flags: Mapping[str, bool] = field(default_factory=dict)

    def is_flag_enabled(self, flag_name: str) -> bool:
        """Return True when the supplied flag evaluates to truthy."""
        return bool(self.flags.get(flag_name, False))


@dataclass(frozen=True, slots=True)
class CapabilitySelection:
    """Result of resolving a plan against configuration toggles."""

    required: frozenset[CapabilityName]
    optional: frozenset[CapabilityName]
    activated_optional: frozenset[CapabilityName]

    def __post_init__(self) -> None:
        object.__setattr__(self, "required", frozenset(self.required))
        object.__setattr__(self, "optional", frozenset(self.optional))
        object.__setattr__(
            self, "activated_optional", frozenset(self.activated_optional)
        )

    @property
    def all(self) -> frozenset[CapabilityName]:
        """Return every capability that should be attached to the interface."""
        return frozenset((*self.required, *self.activated_optional))
