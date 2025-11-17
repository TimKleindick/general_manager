"""Registry for tracking capabilities attached to each interface class."""

from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Mapping, TYPE_CHECKING

from general_manager.interface.base_interface import InterfaceBase

from .base import CapabilityName

if TYPE_CHECKING:  # pragma: no cover
    from .base import Capability


class CapabilityRegistry:
    """In-memory registry mapping interface classes to their capabilities."""

    def __init__(self) -> None:
        """
        Initialize an in-memory registry for interface capability declarations and concrete instances.

        Creates two empty mappings:
        - _bindings: maps interface classes to a set of CapabilityName representing declared capabilities.
        - _instances: maps interface classes to a tuple of Capability objects representing bound concrete capabilities.
        """
        self._bindings: dict[type[InterfaceBase], set[CapabilityName]] = {}
        self._instances: dict[type[InterfaceBase], tuple["Capability", ...]] = {}

    def register(
        self,
        interface_cls: type[InterfaceBase],
        capabilities: Iterable[CapabilityName],
        *,
        replace: bool = False,
    ) -> None:
        """
        Record capabilities for an interface class.

        Parameters:
            interface_cls: Interface receiving the capabilities.
            capabilities: Iterable of capability names to register.
            replace: Overwrite existing entries instead of merging.
        """
        if replace or interface_cls not in self._bindings:
            self._bindings[interface_cls] = set(capabilities)
        else:
            self._bindings[interface_cls].update(capabilities)

    def get(self, interface_cls: type[InterfaceBase]) -> frozenset[CapabilityName]:
        """
        Retrieve the capability names registered for the given interface class.

        Parameters:
            interface_cls (type[InterfaceBase]): The interface class to look up.

        Returns:
            frozenset[CapabilityName]: A frozenset of capability names registered for the interface; empty frozenset if none are registered.
        """
        return frozenset(self._bindings.get(interface_cls, set()))

    def bind_instances(
        self,
        interface_cls: type[InterfaceBase],
        capabilities: Iterable["Capability"],
    ) -> None:
        """
        Record concrete Capability instances for the given interface class.

        Parameters:
            interface_cls (type[InterfaceBase]): Interface class to bind instances to.
            capabilities (Iterable[Capability]): Iterable of concrete capability objects to store; replaces any previously bound instances.
        """
        self._instances[interface_cls] = tuple(capabilities)

    def instances(self, interface_cls: type[InterfaceBase]) -> tuple["Capability", ...]:
        """
        Retrieve the concrete capability objects associated with the given interface.

        Returns:
            A tuple of `Capability` objects registered for `interface_cls`; an empty tuple if none are registered.
        """
        return self._instances.get(interface_cls, tuple())

    def snapshot(self) -> Mapping[type[InterfaceBase], frozenset[CapabilityName]]:
        """
        Return a read-only mapping of registered interface classes to their declared capability names.

        Returns:
            Mapping[type[InterfaceBase], frozenset[CapabilityName]]: A read-only MappingProxyType mapping each interface class to a frozenset of its registered capability names (empty frozenset if none).
        """
        return MappingProxyType(
            {interface: frozenset(names) for interface, names in self._bindings.items()}
        )
