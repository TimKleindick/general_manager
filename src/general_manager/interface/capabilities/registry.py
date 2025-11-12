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
        """Return the registered capabilities for an interface (empty when absent)."""
        return frozenset(self._bindings.get(interface_cls, set()))

    def bind_instances(
        self,
        interface_cls: type[InterfaceBase],
        capabilities: Iterable["Capability"],
    ) -> None:
        """Store the concrete capability objects attached to an interface."""
        self._instances[interface_cls] = tuple(capabilities)

    def instances(self, interface_cls: type[InterfaceBase]) -> tuple["Capability", ...]:
        """Return the capability objects registered for the interface."""
        return self._instances.get(interface_cls, tuple())

    def snapshot(self) -> Mapping[type[InterfaceBase], frozenset[CapabilityName]]:
        """Expose a read-only copy of the registry contents."""
        return MappingProxyType(
            {interface: frozenset(names) for interface, names in self._bindings.items()}
        )
