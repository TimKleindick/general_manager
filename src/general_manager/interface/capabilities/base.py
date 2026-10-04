"""Base capability protocol and shared type aliases."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping

from typing import ClassVar, Literal, Protocol, TYPE_CHECKING, runtime_checkable

CapabilityName = Literal[
    "read",
    "create",
    "create_many",
    "update",
    "delete",
    "history",
    "validation",
    "query",
    "orm_support",
    "orm_mutation",
    "orm_lifecycle",
    "calculation_lifecycle",
    "excel_lifecycle",
    "excel_sync",
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
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.manager.bulk_create import CreateManyBatchResult
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


class CreateManyCapability(Capability, Protocol):
    """Optional backend batch contract used by InterfaceBase.create_many.

    A handler validates support before consuming input and returns a lazy,
    bounded iterator. Each batch is atomic, including required history/outbox
    writes; permissions and canonical lifecycle/observability must be preserved
    or an explicitly safe fallback selected. It must recheck historical mutation
    restrictions when the iterator advances. Failed writes and post-commit
    effects remain distinct using CreateManyError/CreateManyPostCommitError.
    Results carry backend-defined opaque ids (including complete mappings),
    accurate provisional/durable counts and an optional Django database alias.
    A backend unable to provide this contract must reject before consumption;
    there is deliberately no row-by-row non-atomic fallback in the dispatcher.
    """

    def create_many(
        self,
        interface_cls: type[InterfaceBase],
        records: Iterable[Mapping[str, object]],
        *,
        manager_class: type[GeneralManager],
        creator_id: int | None = None,
        history_comment: str | None = None,
        ignore_permission: bool = False,
        batch_size: int = 1000,
    ) -> Iterator[CreateManyBatchResult]:
        """Return progress only after a whole batch has successfully persisted."""
        ...
