"""Data models describing capability plans and selections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from general_manager.interface.capabilities import CapabilityName

__all__ = ["CapabilityConfig", "CapabilityPlan", "CapabilitySelection"]


@dataclass(frozen=True, slots=True)
class CapabilityPlan:
    """Immutable manifest entry for one interface family.

    `CapabilityName` is the public string-literal capability identifier type
    exported by `general_manager.interface.capabilities`.
    `required` and `optional` are normalized to `frozenset` values and duplicate
    capability names collapse. A capability may appear in both sets; manifest
    resolution and builder validation decide how to interpret that combination.
    `flags` maps runtime flag names to optional capability names, is copied to a
    plain `dict`, and is exposed through a read-only mapping proxy.
    """

    required: frozenset[CapabilityName] = field(default_factory=frozenset)
    optional: frozenset[CapabilityName] = field(default_factory=frozenset)
    flags: Mapping[str, CapabilityName] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize required, optional, and flag mapping attributes.

        Raises:
            TypeError: If `required` or `optional` cannot be iterated, if any
                capability name is unhashable, or if `flags` cannot be
                converted with `dict(...)`.
        """
        object.__setattr__(self, "required", frozenset(self.required))
        object.__setattr__(self, "optional", frozenset(self.optional))
        object.__setattr__(self, "flags", MappingProxyType(dict(self.flags)))


@dataclass(slots=True)
class CapabilityConfig:
    """Mutable runtime toggles for optional capability activation.

    Capability names use the public `CapabilityName` string-literal type from
    `general_manager.interface.capabilities`.
    `enabled` and `disabled` are mutable sets consumed by
    `ManifestCapabilityBuilder`. `enabled` requests optional capabilities,
    while `disabled` removes optional capabilities after flag and manual enables
    have been validated. If the same optional name appears in both sets,
    disabled wins; manually enabling a non-optional name still raises in the
    builder even if that name is also disabled. `flags` stores arbitrary
    truth-tested values, not only booleans: `is_flag_enabled()` applies Python
    `bool(...)` coercion and treats missing flags as disabled.
    """

    enabled: set[CapabilityName] = field(default_factory=set)
    disabled: set[CapabilityName] = field(default_factory=set)
    flags: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize mutable containers owned by this config instance.

        The config remains mutable after construction, but external set or
        mapping objects passed to the constructor are copied so later caller-side
        mutation does not affect builder resolution.

        Raises:
            TypeError: If `enabled` or `disabled` cannot be iterated, if any
                capability name is unhashable, or if `flags` cannot be
                converted with `dict(...)`.
        """
        self.enabled = set(self.enabled)
        self.disabled = set(self.disabled)
        self.flags = dict(self.flags)

    def is_flag_enabled(self, flag_name: str) -> bool:
        """Return whether `flag_name` is present with a truthy value.

        Args:
            flag_name: Flag key to check.

        Returns:
            `True` when the stored value is truthy, otherwise `False`. Missing
            flags return `False`.
        """
        return bool(self.flags.get(flag_name, False))


@dataclass(frozen=True, slots=True)
class CapabilitySelection:
    """Immutable result of resolving a plan against runtime config.

    Capability names use the public `CapabilityName` string-literal type from
    `general_manager.interface.capabilities`.
    `required`, `optional`, and `activated_optional` are normalized to
    frozensets and duplicate capability names collapse. The model intentionally
    does not validate that activated optional names are present in `optional`;
    `ManifestCapabilityBuilder` owns that validation.
    """

    required: frozenset[CapabilityName]
    optional: frozenset[CapabilityName]
    activated_optional: frozenset[CapabilityName]

    def __post_init__(self) -> None:
        """Normalize all selection sets to immutable frozensets.

        Raises:
            TypeError: If any field cannot be iterated or contains unhashable
                values.
        """
        object.__setattr__(self, "required", frozenset(self.required))
        object.__setattr__(self, "optional", frozenset(self.optional))
        object.__setattr__(
            self, "activated_optional", frozenset(self.activated_optional)
        )

    @property
    def all(self) -> frozenset[CapabilityName]:
        """Return required plus activated optional capability names.

        Returns:
            Fresh `frozenset` containing every required capability and every
            activated optional capability. Inactive optional names are excluded.
        """
        return frozenset((*self.required, *self.activated_optional))
