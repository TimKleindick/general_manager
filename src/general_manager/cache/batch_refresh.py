"""Opt-in refresh callbacks that understand ``GeneralManager.create_many``.

The registration point is intentionally separate from the private data-change
signals.  Applications may register a cache or projection refresh without
having to disconnect framework receivers in order to make an import efficient.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from weakref import WeakSet

from django.db import transaction

from general_manager.cache.signals import post_data_change

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.manager.bulk_create import CreateManyBatchContext
    from general_manager.manager.general_manager import GeneralManager


BatchRefreshCallback = Callable[
    [type["GeneralManager"], tuple[object, ...], str, str], None
]
_registered_receivers: WeakSet[Callable[..., None]] = WeakSet()


@dataclass(frozen=True)
class BatchRefreshDisconnect:
    """A small, idempotent handle returned by batch-refresh registration."""

    receiver: Callable[..., None]

    def disconnect(self) -> None:
        """Remove this callback without affecting unrelated signal receivers."""
        post_data_change.disconnect(self.receiver)

    def __call__(self) -> None:
        self.disconnect()


def connect_batch_refresh_receiver(
    callback: BatchRefreshCallback,
    *,
    on_commit: bool = False,
) -> BatchRefreshDisconnect:
    """Register a cache refresh callback for ordinary and batched mutations.

    Callbacks run inside the mutation transaction by default.  Passing
    ``on_commit=True`` defers ordinary changes and a successfully committed
    ``create_many`` batch until its transaction is durable.  A bulk SQL batch
    records all identifiers and invokes a registered callback once; canonical
    row-by-row batches deliberately retain their ordinary per-row timing.
    """

    def receiver(
        sender: type["GeneralManager"],
        *,
        identification: dict[str, object] | None = None,
        instance: object | None = None,
        action: str | None = None,
        database_alias: str = "default",
        **_: object,
    ) -> None:
        if action not in {"create", "update", "delete"}:
            return
        from general_manager.manager.bulk_create import current_create_many_batch

        batch = current_create_many_batch(database_alias)
        identification_value = identification
        if identification_value is None and instance is not None:
            identification_value = getattr(instance, "identification", None)
        identifier = (
            identification_value.get("id")
            if isinstance(identification_value, dict)
            else None
        )

        def invoke() -> None:
            callback(sender, (identifier,), action, database_alias)

        # The SQL path has already performed the publication barrier and asks
        # us to aggregate.  Canonical batches keep their legacy receiver timing.
        if (
            batch is not None
            and batch.manager_class is sender
            and getattr(batch, "bulk_sql_active", False)
        ):
            registration = batch.batch_refresh_callbacks.setdefault(
                id(receiver), (callback, on_commit, action, [])
            )
            registration[3].append(identifier)
            return

        if on_commit:
            transaction.on_commit(invoke, using=database_alias)
        else:
            invoke()

    post_data_change.connect(receiver, weak=False)
    _registered_receivers.add(receiver)
    return BatchRefreshDisconnect(receiver)


def flush_batch_refresh_callbacks(context: "CreateManyBatchContext") -> None:
    """Run registered callbacks once after an eligible SQL create batch."""
    callbacks = context.batch_refresh_callbacks
    database_alias = context.database_alias
    sender = cast(type["GeneralManager"], context.manager_class)
    for raw_callback, on_commit, action, identifiers in tuple(callbacks.values()):
        callback = cast(BatchRefreshCallback, raw_callback)
        frozen_ids = tuple(identifiers)

        def invoke(
            callback: BatchRefreshCallback = callback,
            frozen_ids: tuple[object, ...] = frozen_ids,
            action: str = action,
        ) -> None:
            callback(sender, frozen_ids, action, database_alias)

        if on_commit:
            transaction.on_commit(invoke, using=database_alias)
        else:
            invoke()
    callbacks.clear()


def is_batch_refresh_receiver(receiver: object) -> bool:
    """Return whether ``receiver`` is one created by this public helper."""
    try:
        return receiver in _registered_receivers
    except TypeError:
        # Application signal receivers may be callable, unhashable objects.
        return False
