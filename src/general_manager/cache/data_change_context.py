"""Context-local ownership metadata for ORM data-change transactions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import connections


_OwnedTransaction = tuple[str, object]

_owned_transactions: ContextVar[tuple[_OwnedTransaction, ...]] = ContextVar(
    "owned_data_change_transactions",
    default=(),
)


def application_atomic_blocks(database_alias: str) -> tuple[object, ...]:
    """Return active non-TestCase atomic blocks in nesting order."""
    if database_alias not in connections:
        return ()
    connection = connections[database_alias]
    try:
        blocks = connection.atomic_blocks  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        return ()
    if not isinstance(blocks, (list, tuple)):
        return ()
    return tuple(
        block for block in blocks if not getattr(block, "_from_testcase", False)
    )


@contextmanager
def own_data_change_transaction(database_alias: str) -> Iterator[None]:
    """Record the exact atomic block opened by a data-change envelope."""
    blocks = application_atomic_blocks(database_alias)
    if not blocks:
        yield
        return
    token = _owned_transactions.set(
        (*_owned_transactions.get(), (database_alias, blocks[-1]))
    )
    try:
        yield
    finally:
        _owned_transactions.reset(token)


def owns_data_change_transaction(database_alias: str) -> bool:
    """Return whether the active data-change envelope owns this alias."""
    recorded = tuple(
        entry[1] for entry in _owned_transactions.get() if entry[0] == database_alias
    )
    return any(
        any(block is recorded_block for recorded_block in recorded)
        for block in application_atomic_blocks(database_alias)
    )


def exclusively_owns_data_change_transaction(database_alias: str) -> bool:
    """Return whether every active application atomic is envelope-owned."""
    owned = tuple(
        entry[1] for entry in _owned_transactions.get() if entry[0] == database_alias
    )
    active = application_atomic_blocks(database_alias)
    if not active:
        return False
    return all(any(block is owned_block for owned_block in owned) for block in active)
