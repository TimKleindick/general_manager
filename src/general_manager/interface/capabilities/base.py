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
]
"""Enumeration of supported capability identifiers."""

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.base_interface import InterfaceBase


@runtime_checkable
class Capability(Protocol):
    """Common API required by all capabilities."""

    name: ClassVar[CapabilityName]

    def setup(self, interface_cls: type["InterfaceBase"]) -> None:
        """Attach the capability to the target interface."""

    def teardown(self, interface_cls: type["InterfaceBase"]) -> None:
        """Remove the capability behaviour from the target interface."""
