"""Declarative helpers for composing interface capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Sequence, Tuple, TypeAlias

from general_manager.interface.capabilities.base import Capability


@dataclass(frozen=True, slots=True)
class InterfaceCapabilityConfig:
    """Configuration describing how to instantiate a specific capability."""

    handler: type[Capability]
    options: Mapping[str, Any] | None = None

    def instantiate(self) -> Capability:
        """Create the configured capability instance."""
        if not self.options:
            return self.handler()
        # Copy options into a mutable dict to avoid mutating caller state.
        return self.handler(**dict(self.options))


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    """Named bundle of capability configurations."""

    label: str
    entries: Tuple[InterfaceCapabilityConfig, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))


CapabilityConfigEntry: TypeAlias = CapabilitySet | InterfaceCapabilityConfig


def flatten_capability_entries(
    entries: Sequence[CapabilityConfigEntry] | Iterable[CapabilityConfigEntry],
) -> tuple[InterfaceCapabilityConfig, ...]:
    """Return a tuple of concrete capability configs, expanding any bundles."""
    flattened: list[InterfaceCapabilityConfig] = []
    for entry in entries:
        if isinstance(entry, CapabilitySet):
            flattened.extend(entry.entries)
        else:
            flattened.append(entry)
    return tuple(flattened)


def iter_capability_entries(
    entries: Sequence[CapabilityConfigEntry] | Iterable[CapabilityConfigEntry],
) -> Iterator[InterfaceCapabilityConfig]:
    """Yield capability configs, expanding bundles on the fly."""
    for entry in entries:
        if isinstance(entry, CapabilitySet):
            yield from entry.entries
        else:
            yield entry


__all__ = [
    "CapabilityConfigEntry",
    "CapabilitySet",
    "InterfaceCapabilityConfig",
    "flatten_capability_entries",
    "iter_capability_entries",
]
