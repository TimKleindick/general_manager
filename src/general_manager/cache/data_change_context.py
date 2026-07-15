"""Context-local ownership metadata for ORM data-change transactions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class _DataChangeTransaction:
    """Live marker for one framework-owned transaction envelope."""

    database_alias: str
    caller_in_atomic_block: bool
    active: bool = True


_owned_transactions: ContextVar[tuple[_DataChangeTransaction, ...]] = ContextVar(
    "owned_data_change_transactions",
    default=(),
)
_authorized_operations: ContextVar[tuple[_DataChangeTransaction, ...]] = ContextVar(
    "authorized_data_change_operations",
    default=(),
)


def _current_owner(database_alias: str) -> _DataChangeTransaction | None:
    for owner in reversed(_owned_transactions.get()):
        if owner.active and owner.database_alias == database_alias:
            return owner
    return None


@contextmanager
def own_data_change_transaction(
    database_alias: str,
    *,
    caller_in_atomic_block: bool,
) -> Iterator[None]:
    """Record a live framework envelope without inspecting Django internals."""
    owner = _DataChangeTransaction(
        database_alias=database_alias,
        caller_in_atomic_block=caller_in_atomic_block,
    )
    token = _owned_transactions.set((*_owned_transactions.get(), owner))
    try:
        yield
    finally:
        owner.active = False
        _owned_transactions.reset(token)


@contextmanager
def authorize_data_change_operation(database_alias: str) -> Iterator[None]:
    """Authorize only the decorated manager method to reuse its envelope."""
    owner = _current_owner(database_alias)
    if owner is None:
        yield
        return
    token = _authorized_operations.set((*_authorized_operations.get(), owner))
    try:
        yield
    finally:
        _authorized_operations.reset(token)


def owns_data_change_transaction(database_alias: str) -> bool:
    """Return whether this context has a live framework envelope for an alias."""
    return _current_owner(database_alias) is not None


def is_data_change_operation_authorized(database_alias: str) -> bool:
    """Return whether the current owner is running its decorated method body."""
    owner = _current_owner(database_alias)
    if owner is None:
        return False
    return any(
        authorized is owner and authorized.active
        for authorized in _authorized_operations.get()
    )


def may_reuse_data_change_transaction(database_alias: str) -> bool:
    """Conservatively authorize reuse when the manager opened the outer block."""
    owner = _current_owner(database_alias)
    return bool(
        owner is not None
        and not owner.caller_in_atomic_block
        and is_data_change_operation_authorized(database_alias)
    )
