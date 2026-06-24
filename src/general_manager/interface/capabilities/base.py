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
    "request_lifecycle",
    "read_only_management",
    "soft_delete",
]
"""
Supported capability identifiers used by interface capability registries.

Capability names are stable string keys. Interfaces store active names in
`InterfaceBase._capabilities`, map them to handler instances in
`InterfaceBase._capability_handlers`, and may use them as override keys in
`capability_overrides`.
"""

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.base_interface import InterfaceBase


@runtime_checkable
class Capability(Protocol):
    """
    Runtime-checkable protocol implemented by interface capability handlers.

    A capability advertises one stable `name` and can attach or detach behavior
    from an interface class. Implementations mutate the supplied class in place
    and return `None`. Exceptions from concrete implementations propagate; this
    protocol does not define a normalization layer or idempotency guarantee.
    """

    name: ClassVar[CapabilityName]

    def setup(self, interface_cls: type["InterfaceBase"]) -> None:
        """
        Attach this capability to the given interface class.

        Implementations should modify or extend the provided interface class so
        that it exposes or enables the capability's behavior, for example by
        registering methods, attributes, or lifecycle hooks.

        Parameters:
            interface_cls: Interface class to mutate.

        Returns:
            `None`.

        Raises:
            Exception: Concrete capability implementations define their own
                validation and setup errors, which propagate unchanged.
        """

    def teardown(self, interface_cls: type["InterfaceBase"]) -> None:
        """
        Detach this capability from the given interface class.

        Parameters:
            interface_cls: Interface class to mutate.

        Returns:
            `None`.

        Raises:
            Exception: Concrete capability implementations define their own
                teardown errors, which propagate unchanged.
        """
