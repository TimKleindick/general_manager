"""
Declarative helpers for composing interface capabilities.

The public exports are ``CapabilityConfigEntry``, ``CapabilitySet``,
``InterfaceCapabilityConfig``, ``flatten_capability_entries``, and
``iter_capability_entries``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeAlias

from general_manager.interface.capabilities.base import Capability

CapabilityOptions: TypeAlias = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class InterfaceCapabilityConfig:
    """
    Declarative entry describing one capability handler construction.

    ``handler`` is the capability class to instantiate. ``options=None`` means
    no keyword arguments are supplied; a mapping value is converted to a plain
    ``dict`` immediately before construction and expanded as keyword arguments.
    The config object is immutable, but the original mapping is not copied until
    :meth:`instantiate` is called.
    """

    handler: type[Capability]
    options: CapabilityOptions | None = None

    def instantiate(self) -> Capability:
        """Instantiate the configured capability handler.

        Returns:
            Capability instance produced by calling ``handler``. ``options=None``
            calls the handler with no keyword arguments; any supplied mapping,
            including an empty or otherwise falsey mapping, is copied into a
            mutable ``dict`` and expanded as keyword arguments.

        Raises:
            TypeError: If ``handler`` rejects the supplied keyword arguments or
                is otherwise not constructible. Exceptions from mapping
                iteration while converting ``options`` with ``dict(...)``
                propagate unchanged.
        """
        if self.options is None:
            return self.handler()
        # Copy options into a mutable dict to avoid mutating caller state.
        return self.handler(**dict(self.options))


@dataclass(frozen=True, slots=True, init=False)
class CapabilitySet:
    """
    Named immutable bundle of concrete capability configuration entries.

    The constructor accepts any iterable of :class:`InterfaceCapabilityConfig`
    entries and stores it as a tuple. Entries are not copied deeply and are not
    validated beyond normal iteration; invalid values supplied at runtime remain
    invalid values in the tuple.
    """

    label: str
    entries: tuple[InterfaceCapabilityConfig, ...]

    def __init__(
        self, label: str, entries: Iterable[InterfaceCapabilityConfig]
    ) -> None:
        """Store bundle entries as an immutable tuple.

        Args:
            label: Human-readable bundle label used for documentation and
                debugging.
            entries: Iterable of concrete capability configuration entries.

        Raises:
            TypeError: If ``entries`` cannot be iterated.
        """
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "entries", tuple(entries))


CapabilityConfigEntry: TypeAlias = CapabilitySet | InterfaceCapabilityConfig


def flatten_capability_entries(
    entries: Sequence[CapabilityConfigEntry] | Iterable[CapabilityConfigEntry],
) -> tuple[InterfaceCapabilityConfig, ...]:
    """Expand capability bundles into a flat immutable tuple.

    Args:
        entries: Iterable of ``InterfaceCapabilityConfig`` values and
            ``CapabilitySet`` bundles. Bundles are expanded one level into their
            configured entries. The iterable is consumed once.

    Returns:
        Tuple of concrete capability configurations in input order. Entries are
        not deduplicated or validated; repeated capability handlers remain
        repeated entries. Invalid non-``CapabilitySet`` runtime values are
        appended unchanged if callers bypass static typing.

    Raises:
        TypeError: If ``entries`` cannot be iterated.
        Exception: Exceptions raised while iterating ``entries`` propagate
            unchanged.
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
    """Yield concrete capability configurations in order.

    Args:
        entries: Iterable of ``InterfaceCapabilityConfig`` values and
            ``CapabilitySet`` bundles. Bundles are expanded one level into their
            configured entries. Iteration stays lazy for the outer iterable.

    Returns:
        Iterator over concrete capability configuration entries. Entries are not
        deduplicated or validated. Invalid non-``CapabilitySet`` runtime values
        are yielded unchanged if callers bypass static typing.

    Raises:
        TypeError: If ``entries`` cannot be iterated.
        Exception: Exceptions raised while iterating ``entries`` propagate
            unchanged.
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
