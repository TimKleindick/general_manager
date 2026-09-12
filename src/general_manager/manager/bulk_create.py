"""Bounded, transaction-aware state for :meth:`GeneralManager.create_many`."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


@dataclass(frozen=True)
class CreateManyBatchResult:
    """Immutable progress returned after one successfully created input batch."""

    start_index: int
    end_index: int
    ids: tuple[object, ...]
    successful_count: int
    committed_successful_count: int
    pending_successful_count: int
    database_alias: str
    committed: bool

    @property
    def input_range(self) -> range:
        """Return the half-open input interval represented by this result."""
        return range(self.start_index, self.end_index)

    @property
    def durable(self) -> bool:
        """Whether this batch is durable rather than caller-transaction pending."""
        return self.committed


class CreateManyError(RuntimeError):
    """Base error that retains restart and progress metadata for ``create_many``."""

    def __init__(
        self,
        *,
        failure_index: int | None,
        batch_start_index: int,
        batch_end_index: int,
        cause: BaseException,
        successful_count: int,
        committed_successful_count: int,
        pending_successful_count: int,
        database_alias: str,
        committed: bool,
    ) -> None:
        self.failure_index = failure_index
        self.batch_start_index = batch_start_index
        self.batch_end_index = batch_end_index
        self.cause = cause
        self.successful_count = successful_count
        self.committed_successful_count = committed_successful_count
        self.pending_successful_count = pending_successful_count
        self.database_alias = database_alias
        self.committed = committed
        self.durable = committed
        location = "unknown" if failure_index is None else str(failure_index)
        super().__init__(
            "create_many failed at input index "
            f"{location} in batch [{batch_start_index}, {batch_end_index})"
        )

    @property
    def input_range(self) -> range:
        """Return the consumed portion of the batch related to this failure."""
        return range(self.batch_start_index, self.batch_end_index)


class CreateManyPostCommitError(CreateManyError):
    """A post-commit callback failed after a batch row set was committed."""

    def __init__(
        self,
        *,
        ids: tuple[object, ...],
        failure_index: int | None,
        batch_start_index: int,
        batch_end_index: int,
        cause: BaseException,
        successful_count: int,
        committed_successful_count: int,
        pending_successful_count: int,
        database_alias: str,
        committed: bool,
    ) -> None:
        self.ids = ids
        super().__init__(
            failure_index=failure_index,
            batch_start_index=batch_start_index,
            batch_end_index=batch_end_index,
            cause=cause,
            successful_count=successful_count,
            committed_successful_count=committed_successful_count,
            pending_successful_count=pending_successful_count,
            database_alias=database_alias,
            committed=committed,
        )


class CreateManyUnsupportedError(TypeError):
    """The manager cannot provide the ORM transaction contract of ``create_many``."""

    @classmethod
    def custom_create(cls, manager_name: str) -> "CreateManyUnsupportedError":
        """Build the error for an overridden manager-level create method."""
        return cls(
            f"{manager_name}.create_many does not support a custom create override."
        )

    @classmethod
    def non_orm_interface(cls, manager_name: str) -> "CreateManyUnsupportedError":
        """Build the error for a manager without the ORM batch contract."""
        return cls(f"{manager_name}.create_many requires a writable ORM interface.")

    @classmethod
    def missing_id(cls, manager_name: str) -> "CreateManyUnsupportedError":
        """Build the error for a noncanonical create identification payload."""
        return cls(f"{manager_name}.create returned no id identification.")

    @classmethod
    def workflow_registry(cls) -> "CreateManyUnsupportedError":
        """Build the error for an outbox mode that cannot remain batch-safe."""
        return cls(
            "create_many requires an asynchronous database workflow registry "
            "on the source database."
        )

    @classmethod
    def uploads(cls) -> "CreateManyUnsupportedError":
        """Upload finalization cannot share the ordinary batch transaction."""
        return cls("create_many does not support upload claims.")

    @classmethod
    def routing(cls) -> "CreateManyUnsupportedError":
        """An implicit router would write outside the batch transaction."""
        return cls(
            "create_many requires Interface.database for non-default database routing."
        )

    @classmethod
    def history_routing(cls) -> "CreateManyUnsupportedError":
        """Custom routers require history explicitly bound to the source database."""
        return cls(
            "create_many requires database-aware history when database routers are configured."
        )

    @classmethod
    def manual_transaction(cls) -> "CreateManyUnsupportedError":
        """Manual transaction management cannot provide batch commit outcomes."""
        return cls("create_many requires autocommit or a transaction.atomic() block.")

    @classmethod
    def transaction_scope_changed(cls) -> "CreateManyUnsupportedError":
        """Provisional progress cannot be transferred to another caller scope."""
        return cls(
            "Consume create_many within the same caller transaction and savepoint scope."
        )


class CreateManyInvalidBatchSizeError(ValueError):
    """``batch_size`` was not a positive non-boolean integer."""

    def __init__(self) -> None:
        super().__init__("batch_size must be a positive integer.")


def validate_create_many_record(record: Mapping[str, object]) -> None:
    """Reject file storage operations before any record in the batch is saved."""
    from django.core.files.base import File
    from general_manager.uploads.types import UploadCandidate

    if isinstance(record, Mapping) and any(
        isinstance(value, (UploadCandidate, File)) for value in record.values()
    ):
        raise CreateManyUnsupportedError.uploads()


def validate_create_many_transaction(database_alias: str) -> None:
    """Reject a silent rollback request before a batch can be reported successful."""
    from django.db import transaction
    from django.db.transaction import TransactionManagementError

    if transaction.get_rollback(using=database_alias):
        message = "A create_many mutation marked its batch transaction for rollback."
        raise TransactionManagementError(message)


def validate_create_many_workflow(database_alias: str, registry: object) -> None:
    """Require the built-in durable outbox to share the source transaction."""
    from django.db import DEFAULT_DB_ALIAS, router
    from general_manager.workflow.config import workflow_async_enabled
    from general_manager.workflow.event_registry import DatabaseEventRegistry
    from general_manager.workflow.models import WorkflowEventRecord, WorkflowOutbox

    if not isinstance(registry, DatabaseEventRegistry):
        return
    if (
        type(registry) is not DatabaseEventRegistry
        or database_alias != DEFAULT_DB_ALIAS
        or not workflow_async_enabled()
        or any(
            router.db_for_write(model) != database_alias
            for model in (WorkflowEventRecord, WorkflowOutbox)
        )
    ):
        raise CreateManyUnsupportedError.workflow_registry()


def validate_create_many_history(model: object) -> None:
    """Keep routed audit writes in the source transaction, including nested work."""
    from django.db import router
    from general_manager.interface.utils.history import DATABASE_AWARE_HISTORY_MARKER

    history_name = getattr(
        getattr(model, "_meta", None), "simple_history_manager_attribute", None
    )
    if history_name and router.routers:
        history_model = getattr(model, history_name).model
        if not getattr(history_model, DATABASE_AWARE_HISTORY_MARKER, False):
            raise CreateManyUnsupportedError.history_routing()


class _CallerAtomicState(Protocol):
    """Django's live atomic stack, omitted by the current django-types stubs."""

    atomic_blocks: list[object]


@dataclass(frozen=True)
class CreateManyCallerScope:
    """Keep provisional iteration in the exact caller transaction/savepoint scope."""

    atomic_blocks: tuple[object, ...]
    savepoint_ids: tuple[str | None, ...]
    callback: Callable[[], None]

    @classmethod
    def capture(cls, database_alias: str) -> "CreateManyCallerScope":
        from django.db import connections, transaction

        def marker() -> None:
            pass

        transaction.on_commit(marker, using=database_alias)
        state = cast(_CallerAtomicState, connections[database_alias])
        return cls(
            tuple(state.atomic_blocks),
            tuple(connections[database_alias].savepoint_ids),
            marker,
        )

    def validate(self, database_alias: str) -> None:
        from django.db import connections

        connection = connections[database_alias]
        # Atomic objects can be reused in another transaction. Registration
        # liveness also catches that case, and callbacks skipped after an error.
        registered = any(
            callback is self.callback for _, callback, _ in connection.run_on_commit
        )
        state = cast(_CallerAtomicState, connection)
        if (
            tuple(state.atomic_blocks) != self.atomic_blocks
            or tuple(connection.savepoint_ids) != self.savepoint_ids
            or not registered
        ):
            raise CreateManyUnsupportedError.transaction_scope_changed()


@dataclass
class CreateManyBatchContext:
    """Context-local coordination state for one batch and one database alias."""

    database_alias: str
    caller_owns_transaction: bool
    manager_class: type[object]
    workflow_callbacks: list[Callable[[], object]] = field(default_factory=list)
    notification_callbacks: list[Callable[[], None]] = field(default_factory=list)
    search_work: list[object] = field(default_factory=list)
    search_configs: dict[type[object], object | None] = field(default_factory=dict)
    history_actors: dict[int, AbstractBaseUser] = field(default_factory=dict)
    flushing_search_work: bool = False
    notifications_registered: bool = False

    @property
    def committed(self) -> bool:
        """Whether a yielded batch can be durably checkpointed."""
        return not self.caller_owns_transaction


_current_batch: ContextVar[CreateManyBatchContext | None] = ContextVar(
    "general_manager_create_many_batch",
    default=None,
)
_batch_signal_depth: ContextVar[int] = ContextVar(
    "general_manager_create_many_signal_depth", default=0
)


def current_create_many_batch(
    database_alias: str,
) -> CreateManyBatchContext | None:
    """Return the live matching batch context without crossing task boundaries."""
    if _batch_signal_depth.get() > 1:
        return None
    return enclosing_create_many_batch(database_alias)


def enclosing_create_many_batch(
    database_alias: str | None = None,
) -> CreateManyBatchContext | None:
    """Find the batch envelope, including inside a nested mutation savepoint."""
    context = _current_batch.get()
    if context is None or (
        database_alias is not None and context.database_alias != database_alias
    ):
        return None
    return context


@contextmanager
def create_many_signal_scope() -> Iterator[None]:
    """Keep nested writes on their canonical savepoint and callback paths."""
    if _current_batch.get() is None:
        yield
        return
    token = _batch_signal_depth.set(_batch_signal_depth.get() + 1)
    try:
        yield
    finally:
        _batch_signal_depth.reset(token)


@contextmanager
def create_many_batch_context(
    database_alias: str,
    *,
    caller_owns_transaction: bool,
    manager_class: type[object],
) -> Iterator[CreateManyBatchContext]:
    """Install scoped batch optimizations and always restore prior task state."""
    context = CreateManyBatchContext(
        database_alias=database_alias,
        caller_owns_transaction=caller_owns_transaction,
        manager_class=manager_class,
    )
    token = _current_batch.set(context)
    try:
        yield context
    finally:
        _current_batch.reset(token)
