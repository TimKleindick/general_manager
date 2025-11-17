"""Base capability protocol and shared type aliases."""

from __future__ import annotations

from typing import ClassVar, Literal, Protocol, TYPE_CHECKING, runtime_checkable

CapabilityName = Literal[
    "read",
    "create",
    "update",
    "delete",
    "history",
    "validation",
    "query",
    "orm_support",
    "orm_mutation",
    "orm_lifecycle",
    "calculation_lifecycle",
    "notification",
    "scheduling",
    "access_control",
    "observability",
    "existing_model_resolution",
    "read_only_management",
    "soft_delete",
]
"""Enumeration of supported capability identifiers."""

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.base_interface import InterfaceBase


@runtime_checkable
class Capability(Protocol):
    """Common API required by all capabilities."""

    name: ClassVar[CapabilityName]

    def setup(self, interface_cls: type["InterfaceBase"]) -> None:
        """
        Attach this capability to the given interface class.

        Implementations should modify or extend the provided interface class so that it exposes or enables the capability's behavior (for example by registering methods, attributes, or hooks).

        Parameters:
            interface_cls (type[InterfaceBase]): The interface class to which the capability will be attached.
        """

    def teardown(self, interface_cls: type["InterfaceBase"]) -> None:
        """
        Detach this capability from the given interface class.

        Parameters:
            interface_cls (type["InterfaceBase"]): The interface class to remove this capability's behavior from.
        """
