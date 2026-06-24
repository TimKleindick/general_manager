"""Concrete capability implementations shared by interface types."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
from typing import ClassVar, TYPE_CHECKING

from .base import Capability, CapabilityName
from .exceptions import CapabilityBindingError

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.base_interface import InterfaceBase


def _missing_attributes_message(attrs: Iterable[str]) -> str:
    """
    Return a deterministic message for missing interface class attributes.

    Parameters:
        attrs: Missing attribute names. The iterable is consumed once.

    Returns:
        A message of the form
        ``"missing required attributes: a, b, c"``. Attribute names are sorted
        alphabetically so error text is stable regardless of declaration order.
        Empty input returns ``"missing required attributes: "``; capability
        setup calls this helper only after at least one missing attribute is
        found.
    """
    return f"missing required attributes: {', '.join(sorted(attrs))}"


@dataclass
class BaseCapability(Capability):
    """
    Base implementation for capability validation and handler registration.

    Subclasses publish a stable capability ``name`` and may list class
    attributes or methods required on the interface class in
    ``required_attributes``. The dataclass has no instance fields or generated
    constructor parameters of its own. ``setup()`` validates those requirements,
    converts the interface class's current ``_capability_handlers`` mapping to a
    plain ``dict`` or starts from an empty plain ``dict`` when that attribute is
    absent, registers this capability instance under ``name``, and writes the
    copied mapping back to the class. ``teardown()`` performs the inverse
    copy-and-remove operation with the same plain-``dict`` conversion and also
    starts from an empty mapping when no registry exists yet.

    The base implementation does not call the required attributes and does not
    validate their signatures; it only checks ``hasattr(interface_cls, attr)``.
    Repeated setup replaces the handler for the same name, and repeated teardown
    is a no-op once the handler is absent. Missing attributes raise
    :class:`CapabilityBindingError`; other attribute access or mapping-copy
    failures propagate unchanged.
    """

    name: ClassVar[CapabilityName]
    required_attributes: ClassVar[tuple[str, ...]] = ()

    def setup(self, interface_cls: type["InterfaceBase"]) -> None:
        """
        Validate and register this capability on an interface class.

        Parameters:
            interface_cls: The interface class to mutate.

        Returns:
            None. The supplied class is mutated in place.

        Raises:
            CapabilityBindingError: If one or more names in
                ``required_attributes`` are missing from ``interface_cls``.
                The reason lists missing names in alphabetical order.
            Exception: Exceptions raised by ``hasattr()`` while checking
                required attributes, reading the existing class registry,
                converting ``_capability_handlers`` with ``dict(...)``, or
                assigning the updated registry are propagated unchanged.
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
        Remove this capability from an interface class registry.

        Parameters:
            interface_cls: The interface class to mutate.

        Returns:
            None. The supplied class is mutated in place.

        Raises:
            Exception: Exceptions raised while reading, converting with
                ``dict(...)``, or assigning ``_capability_handlers`` are
                propagated unchanged.
        """
        handlers = dict(getattr(interface_cls, "_capability_handlers", {}))
        handlers.pop(self.name, None)
        interface_cls._capability_handlers = handlers


class ReadCapability(BaseCapability):
    """Register the ``read`` handler; requires ``get_data`` on the interface."""

    name: ClassVar[CapabilityName] = "read"
    required_attributes: ClassVar[tuple[str, ...]] = ("get_data",)


class CreateCapability(BaseCapability):
    """Register the ``create`` handler; requires ``create`` on the interface."""

    name: ClassVar[CapabilityName] = "create"
    required_attributes: ClassVar[tuple[str, ...]] = ("create",)


class UpdateCapability(BaseCapability):
    """Register the ``update`` handler; requires ``update`` on the interface."""

    name: ClassVar[CapabilityName] = "update"
    required_attributes: ClassVar[tuple[str, ...]] = ("update",)


class DeleteCapability(BaseCapability):
    """Register the ``delete`` handler; requires ``delete`` on the interface."""

    name: ClassVar[CapabilityName] = "delete"
    required_attributes: ClassVar[tuple[str, ...]] = ("delete",)


class HistoryCapability(BaseCapability):
    """Register the ``history`` handler; requires ``get_attribute_types``."""

    name: ClassVar[CapabilityName] = "history"
    required_attributes: ClassVar[tuple[str, ...]] = ("get_attribute_types",)


class ValidationCapability(BaseCapability):
    """Register the ``validation`` handler; requires ``get_attribute_types``."""

    name: ClassVar[CapabilityName] = "validation"
    required_attributes: ClassVar[tuple[str, ...]] = ("get_attribute_types",)


class NotificationCapability(BaseCapability):
    """Register a ``notification`` marker handler without extra requirements."""

    name: ClassVar[CapabilityName] = "notification"


class SchedulingCapability(BaseCapability):
    """Register a ``scheduling`` marker handler without extra requirements."""

    name: ClassVar[CapabilityName] = "scheduling"


class AccessControlCapability(BaseCapability):
    """Register an ``access_control`` marker handler without extra requirements."""

    name: ClassVar[CapabilityName] = "access_control"


class ObservabilityCapability(BaseCapability):
    """Register an ``observability`` marker handler without extra requirements."""

    name: ClassVar[CapabilityName] = "observability"
