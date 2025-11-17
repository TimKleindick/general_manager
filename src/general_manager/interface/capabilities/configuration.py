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
        """
        Instantiate the configured capability using the stored handler and options.

        Returns:
            Capability: An instance produced by calling the configured handler. If `options` is falsy, the handler is called with no arguments; otherwise `options` are passed as keyword arguments.
        """
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
        """
        Ensure the `entries` field is stored as a tuple.

        Runs after object initialization to convert `entries` into a tuple, enforcing an immutable sequence of capability configurations.
        """
        object.__setattr__(self, "entries", tuple(self.entries))


CapabilityConfigEntry: TypeAlias = CapabilitySet | InterfaceCapabilityConfig


def flatten_capability_entries(
    entries: Sequence[CapabilityConfigEntry] | Iterable[CapabilityConfigEntry],
) -> tuple[InterfaceCapabilityConfig, ...]:
    """
    Expand capability bundles and return a flat tuple of capability configurations.

    Parameters:
        entries: An iterable of CapabilitySet or InterfaceCapabilityConfig; any CapabilitySet encountered is expanded into its contained InterfaceCapabilityConfig entries.

    Returns:
        A tuple of InterfaceCapabilityConfig containing all concrete capability configurations with bundles expanded.
    """
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
    """
    Yield capability configurations, expanding any CapabilitySet bundles.

    Parameters:
        entries: An iterable or sequence of CapabilityConfigEntry (either an InterfaceCapabilityConfig
            or a CapabilitySet). If an entry is a CapabilitySet, its contained InterfaceCapabilityConfig
            items are yielded individually.

    Returns:
        Iterator[InterfaceCapabilityConfig]: An iterator that yields InterfaceCapabilityConfig items,
        with CapabilitySet entries expanded into their contained configurations.
    """
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
