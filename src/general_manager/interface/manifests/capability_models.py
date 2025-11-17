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
        """
        Normalize and freeze the dataclass fields for immutability.

        Converts `required` and `optional` to `frozenset` and wraps `flags` in a read-only mapping so that all exposed attributes are immutable after initialization.
        """
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
        """
        Determine if a named flag is enabled.

        Parameters:
            flag_name (str): Name of the flag to check.

        Returns:
            True if the named flag evaluates to truthy, False otherwise.
        """
        return bool(self.flags.get(flag_name, False))


@dataclass(frozen=True, slots=True)
class CapabilitySelection:
    """Result of resolving a plan against configuration toggles."""

    required: frozenset[CapabilityName]
    optional: frozenset[CapabilityName]
    activated_optional: frozenset[CapabilityName]

    def __post_init__(self) -> None:
        """
        Normalize the dataclass fields to immutable frozenset instances after initialization.

        Converts `required`, `optional`, and `activated_optional` attributes to `frozenset` to enforce immutability and consistent types for downstream consumers.
        """
        object.__setattr__(self, "required", frozenset(self.required))
        object.__setattr__(self, "optional", frozenset(self.optional))
        object.__setattr__(
            self, "activated_optional", frozenset(self.activated_optional)
        )

    @property
    def all(self) -> frozenset[CapabilityName]:
        """
        Combined set of capabilities to attach to the interface.

        Returns:
            frozenset[CapabilityName]: The union of `required` and `activated_optional` capabilities.
        """
        return frozenset((*self.required, *self.activated_optional))
