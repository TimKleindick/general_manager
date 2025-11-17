"""Concrete capability implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Iterable, TYPE_CHECKING

from .base import Capability, CapabilityName
from .exceptions import CapabilityBindingError

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.base_interface import InterfaceBase


def _missing_attributes_message(attrs: Iterable[str]) -> str:
    """
    Create a standardized error message listing missing required attributes.

    Parameters:
        attrs (Iterable[str]): Iterable of missing attribute names.

    Returns:
        message (str): Formatted message of the form "missing required attributes: a, b, c"
    """
    return f"missing required attributes: {', '.join(sorted(attrs))}"


@dataclass
class BaseCapability(Capability):
    """Common validation/registration logic shared by concrete capabilities."""

    name: ClassVar[CapabilityName]
    required_attributes: ClassVar[tuple[str, ...]] = ()

    def setup(self, interface_cls: type["InterfaceBase"]) -> None:
        """
        Bind this capability to an InterfaceBase-derived class by validating required attributes and registering the capability on the class.

        Parameters:
            interface_cls (type[InterfaceBase]): The interface class to bind the capability to.

        Raises:
            CapabilityBindingError: If the interface class does not define one or more required attributes; the error message lists the missing attributes and the capability name.
        """
        missing = tuple(
            attr
            for attr in self.required_attributes
            if not hasattr(interface_cls, attr)
        )
        if missing:
            raise CapabilityBindingError(
                self.name, _missing_attributes_message(missing)
            )
        registry = dict(getattr(interface_cls, "_capability_handlers", {}))
        registry[self.name] = self
        interface_cls._capability_handlers = registry

    def teardown(self, interface_cls: type["InterfaceBase"]) -> None:
        """
        Unregisters this capability from the given interface class's capability registry.

        Removes the capability entry keyed by this capability's name from the interface class's
        _internal_ `_capability_handlers` mapping and writes the updated mapping back to the class.

        Parameters:
            interface_cls (type[InterfaceBase]): The interface class from which to remove this capability.
        """
        handlers = dict(getattr(interface_cls, "_capability_handlers", {}))
        handlers.pop(self.name, None)
        interface_cls._capability_handlers = handlers


class ReadCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "read"
    required_attributes = ("get_data",)


class CreateCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "create"
    required_attributes = ("create",)


class UpdateCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "update"
    required_attributes = ("update",)


class DeleteCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "delete"
    required_attributes = ("delete",)


class HistoryCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "history"
    required_attributes = ("get_attribute_types",)


class ValidationCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "validation"
    required_attributes = ("get_attribute_types",)


class NotificationCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "notification"


class SchedulingCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "scheduling"


class AccessControlCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "access_control"


class ObservabilityCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "observability"
