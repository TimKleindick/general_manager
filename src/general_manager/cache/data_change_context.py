"""Context-local ownership metadata for ORM data-change transactions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class DataChangeTransactionContext:
    """Mutable state owned by one framework-managed data-change transaction."""

    database_alias: str
    caller_in_atomic_block: bool
    changed_classes: set[str] = field(default_factory=set)
    metadata: dict[str, object] = field(default_factory=dict)
    phase_seconds: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DataChangeTransactionScope:
    """Public view of a context-manager entry's transaction state."""

    transaction: DataChangeTransactionContext
    is_outermost: bool


@dataclass
class _DataChangeTransaction:
    """Live marker for one framework-owned transaction envelope."""

    context: DataChangeTransactionContext
    active: bool = True

    @property
    def database_alias(self) -> str:
        return self.context.database_alias

    @property
    def caller_in_atomic_block(self) -> bool:
        return self.context.caller_in_atomic_block


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


def current_data_change_transaction(
    database_alias: str,
) -> DataChangeTransactionContext | None:
    """Return the live framework-owned context for a database alias, if any."""
    owner = _current_owner(database_alias)
    return owner.context if owner is not None else None


@contextmanager
def own_data_change_transaction(
    database_alias: str,
    *,
    caller_in_atomic_block: bool,
) -> Iterator[DataChangeTransactionScope]:
    """Record a live framework envelope without inspecting Django internals."""
    existing_owner = _current_owner(database_alias)
    if existing_owner is not None:
        yield DataChangeTransactionScope(
            transaction=existing_owner.context,
            is_outermost=False,
        )
        return

    context = DataChangeTransactionContext(
        database_alias=database_alias,
        caller_in_atomic_block=caller_in_atomic_block,
    )
    owner = _DataChangeTransaction(
        context=context,
    )
    token = _owned_transactions.set((*_owned_transactions.get(), owner))
    try:
        yield DataChangeTransactionScope(transaction=context, is_outermost=True)
    finally:
        owner.active = False
        _owned_transactions.reset(token)


def register_data_change_class(class_name: str, database_alias: str) -> bool:
    """Record a changed class when the framework owns the live transaction."""
    context = current_data_change_transaction(database_alias)
    if context is None or context.caller_in_atomic_block:
        return False
    context.changed_classes.add(class_name)
    return True


def record_data_change_phase(
    phase: Literal["database", "invalidation", "subscription", "search", "workflow"],
    duration_seconds: float,
    database_alias: str,
) -> None:
    """Accumulate a non-negative duration for a phase in the live context."""
    context = current_data_change_transaction(database_alias)
    if context is not None:
        context.phase_seconds[phase] = context.phase_seconds.get(phase, 0.0) + max(
            duration_seconds, 0.0
        )


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
