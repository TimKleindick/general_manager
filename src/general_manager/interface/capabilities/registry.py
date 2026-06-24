"""Registry for tracking capabilities attached to each interface class."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from .base import CapabilityName

if TYPE_CHECKING:  # pragma: no cover
    from .base import Capability
    from general_manager.interface.base_interface import InterfaceBase


class CapabilityRegistry:
    """
    In-memory registry mapping interface classes to capability declarations.

    The registry stores declared capability names separately from concrete
    capability instances. It is process-local, has no locking, does not validate
    runtime values beyond normal iterable consumption, and returns defensive
    immutable views from read methods.
    """

    def __init__(self) -> None:
        """
        Initialize empty declaration and instance registries.

        ``_bindings`` maps interface classes to mutable internal sets of
        declared capability names. ``_instances`` maps interface classes to
        immutable tuples of concrete capability objects.
        """
        self._bindings: dict[type["InterfaceBase"], set[CapabilityName]] = {}
        self._instances: dict[type["InterfaceBase"], tuple["Capability", ...]] = {}

    def register(
        self,
        interface_cls: type["InterfaceBase"],
        capabilities: Iterable[CapabilityName],
        *,
        replace: bool = False,
    ) -> None:
        """
        Record declared capability names for an interface class.

        The incoming iterable is consumed exactly once into a temporary set
        before internal state is changed. Duplicate names collapse. With
        ``replace=False`` the new names are merged into the existing binding;
        with ``replace=True`` the existing binding is replaced, including by an
        empty iterable. Runtime values are trusted if callers bypass static
        typing.

        Parameters:
            interface_cls: Interface receiving the capabilities.
            capabilities: Iterable of capability names to register. Iteration
                order is not preserved because declaration reads return sets.
            replace: Overwrite existing entries instead of merging when true.

        Raises:
            Exception: Exceptions raised while iterating ``capabilities``
                propagate unchanged before the registry is mutated.
        """
        incoming = set(capabilities)
        if replace or interface_cls not in self._bindings:
            self._bindings[interface_cls] = incoming
        else:
            self._bindings[interface_cls].update(incoming)

    def get(self, interface_cls: type["InterfaceBase"]) -> frozenset[CapabilityName]:
        """
        Retrieve the capability names registered for the given interface class.

        Parameters:
            interface_cls: Interface class to look up.

        Returns:
            A new ``frozenset`` of declared capability names, or an empty
            ``frozenset`` when the interface has no binding.
        """
        return frozenset(self._bindings.get(interface_cls, ()))

    def bind_instances(
        self,
        interface_cls: type["InterfaceBase"],
        capabilities: Iterable["Capability"],
    ) -> None:
        """
        Record concrete capability instances for the given interface class.

        The iterable is consumed once into a tuple before assignment. Instance
        bindings are independent of declared-name bindings: storing instances
        does not call ``register()``, and replacing declared names does not
        remove stored instances.

        Parameters:
            interface_cls: Interface class to bind instances to.
            capabilities: Concrete capability objects to store, preserving
                iteration order. The tuple replaces any previous instance
                binding for the same interface.

        Raises:
            Exception: Exceptions raised while iterating ``capabilities``
                propagate unchanged before the instance binding is replaced.
        """
        self._instances[interface_cls] = tuple(capabilities)

    def instances(
        self, interface_cls: type["InterfaceBase"]
    ) -> tuple["Capability", ...]:
        """
        Retrieve the concrete capability objects associated with the given interface.

        Parameters:
            interface_cls: Interface class to look up.

        Returns:
            The stored capability tuple, or an empty tuple when no instances are
            bound for the interface.
        """
        return self._instances.get(interface_cls, tuple())

    def snapshot(self) -> Mapping[type["InterfaceBase"], frozenset[CapabilityName]]:
        """
        Return a read-only snapshot of declared capability names.

        The returned ``MappingProxyType`` wraps a new dictionary, and each value
        is a ``frozenset``. Later registry mutations do not affect earlier
        snapshots. Concrete capability instances are not included.

        Returns:
            A read-only mapping of interface classes to immutable declared-name
            sets.
        """
        return MappingProxyType(
            {interface: frozenset(names) for interface, names in self._bindings.items()}
        )


__all__ = ["CapabilityRegistry"]
