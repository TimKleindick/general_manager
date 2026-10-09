"""Retain native dependency-cache barriers until database completion."""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
import sys
from typing import Callable, cast
from uuid import uuid4

from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper
from django.utils.connection import ConnectionDoesNotExist

from general_manager.logging import get_logger

logger = get_logger("cache.transaction_barrier")
_STATE_ATTRIBUTE = "_general_manager_dependency_barrier"


def _clear_transaction_run_cache() -> None:
    from general_manager.cache.run_context import current_calculation_run_context

    context = current_calculation_run_context()
    if context is not None:
        try:
            context.discard_dependency_cache_state()
        finally:
            # Fallible lease cleanup must not preserve rolled-back DB rows.
            context.clear_orm_bucket_results()
            context.clear_bucket_indexes()
            context.clear_bucket_projections()
            context.clear_trusted_orm_managers()


@dataclass
class _TransactionBarrier:
    connection: BaseDatabaseWrapper
    owner: str | None = None
    release_pending: bool = False
    completion_committed: bool | None = None
    restore_hooks: Callable[[bool], None] | None = None

    def finish(self, *, committed: bool) -> None:
        from general_manager.cache.dependency_index import end_dependency_data_change

        if self.owner is None:
            if self.restore_hooks is not None:
                self.restore_hooks(False)
            return
        if self.completion_committed is None:
            self.completion_committed = committed
        self.release_pending = True
        # Keep the owner on an error. Owner-aware end can be retried even when
        # transport failure followed an applied decrement.
        try:
            end_dependency_data_change(owner=self.owner)
        finally:
            _clear_transaction_run_cache()
        self.owner = None
        self.release_pending = False
        self.completion_committed = None
        if self.restore_hooks is not None:
            self.restore_hooks(False)


def _install_completion_hooks(connection: BaseDatabaseWrapper) -> _TransactionBarrier:
    state = _TransactionBarrier(connection)
    original_commit = connection.commit
    original_rollback = connection.rollback
    original_close = connection.close
    original_savepoint_rollback = connection.savepoint_rollback
    original_set_autocommit = connection.set_autocommit

    @wraps(original_commit)
    def commit() -> None:
        original_commit()
        try:
            state.finish(committed=True)
        except Exception:
            # A DB-backed cache can raise DatabaseError here. SQL already
            # committed; Django must still run its commit callbacks, rather
            # than mistake this ancillary failure for a failed SQL commit.
            logger.exception("Dependency barrier cleanup after commit failed.")

    @wraps(original_rollback)
    def rollback() -> None:
        original_rollback()
        try:
            state.finish(committed=False)
        except Exception:
            # Atomic may be unwinding a primary mutation/database exception.
            logger.exception("Dependency barrier cleanup after rollback failed.")

    @wraps(original_close)
    def close() -> None:
        try:
            original_close()
        finally:
            # SQLite's in-memory close is a no-op: it hasn't settled a pending
            # transaction and must not reopen cache publication.
            if connection.connection is None or connection.closed_in_transaction:
                try:
                    state.finish(committed=False)
                except Exception:
                    logger.exception("Dependency barrier cleanup after close failed.")

    @wraps(original_savepoint_rollback)
    def savepoint_rollback(sid: str) -> None:
        original_savepoint_rollback(sid)
        if state.owner is not None:
            try:
                _clear_transaction_run_cache()
            except Exception:
                logger.exception(
                    "Dependency cache cleanup after savepoint rollback failed."
                )

    @wraps(original_set_autocommit)
    def set_autocommit(
        autocommit: bool, force_begin_transaction_with_broken_autocommit: bool = False
    ) -> None:
        # Atomic's finally can restore autocommit while a commit interruption
        # is already unwinding. An ancillary retry must preserve that primary.
        primary: BaseException | None = sys.exception()
        try:
            original_set_autocommit(
                autocommit,
                force_begin_transaction_with_broken_autocommit,
            )
        except BaseException as error:
            primary = error
            raise
        finally:
            # User on_commit callbacks run inside set_autocommit and can raise
            # after the DB already committed. Cleanup must still run.
            if autocommit and connection.autocommit and not connection.in_atomic_block:
                rolled_back = state.completion_committed is False
                try:
                    state.finish(committed=True)
                    flush_transaction_rewarm_keys()
                except Exception:
                    if primary is None and not rolled_back:
                        raise
                    logger.exception("Dependency barrier cleanup after commit failed.")

    hooks = (
        ("commit", commit),
        ("rollback", rollback),
        ("close", close),
        ("savepoint_rollback", savepoint_rollback),
        ("set_autocommit", set_autocommit),
    )
    missing = object()
    previous = {name: vars(connection).get(name, missing) for name, _ in hooks}

    def restore_hooks(force: bool) -> None:
        # Remove only our instance overrides. Restoring class lookup avoids
        # retaining bound-method cycles on completed thread-local connections.
        # Keep the completion setter until Django restores autocommit. Eager
        # warm-up must not open a new SQL transaction before that restoration.
        defer_dispatch = (
            not force
            and connection.connection is not None
            and not connection.autocommit
            and not connection.closed_in_transaction
        )
        for name, hook in hooks:
            if defer_dispatch and name in {"close", "set_autocommit"}:
                continue
            if vars(connection).get(name) is hook:
                if previous[name] is missing:
                    vars(connection).pop(name, None)
                else:
                    setattr(connection, name, previous[name])
        if not defer_dispatch:
            if getattr(connection, _STATE_ATTRIBUTE, None) is state:
                vars(connection).pop(_STATE_ATTRIBUTE, None)
            state.restore_hooks = None

    state.restore_hooks = restore_hooks
    for name, hook in hooks:
        setattr(connection, name, hook)
    setattr(connection, _STATE_ATTRIBUTE, state)
    return state


def hold_dependency_transaction_barrier(database_alias: str) -> None:
    """Hold one owned barrier through all savepoints on a writing connection."""
    from general_manager.cache.dependency_index import begin_dependency_data_change

    try:
        connection = connections[database_alias]
    except ConnectionDoesNotExist:
        # Lightweight signal callers sometimes replace transaction.atomic.
        # The real atomic operation remains responsible for rejecting aliases.
        return
    if not connection.in_atomic_block and (
        connection.autocommit
        or (connection.connection is None and connection.settings_dict["AUTOCOMMIT"])
    ):
        return
    state = cast(
        _TransactionBarrier | None, getattr(connection, _STATE_ATTRIBUTE, None)
    )
    if state is not None:
        if state.release_pending:
            state.finish(committed=False)
        if state.owner is None:
            # A manual transaction can write again before restoring
            # autocommit. Replace its completed lifecycle before acquiring
            # another owner, including the deferred completion setter.
            if state.restore_hooks is not None:
                state.restore_hooks(True)
            state = None
    if state is None:
        state = _install_completion_hooks(connection)
    if state.owner is None:
        owner = uuid4().hex
        try:
            begin_dependency_data_change(owner=owner)
        except BaseException:
            if state.restore_hooks is not None:
                state.restore_hooks(True)
            raise
        state.owner = owner


def collect_transaction_rewarm_keys(database_alias: str, *, completed: bool) -> bool:
    """Register eligible work with Django's savepoint-aware commit callbacks.

    Callbacks own only optional warm-up work, never the transaction barrier.
    Discarding them on rollback therefore cannot leak a barrier.
    """
    from general_manager.cache.dependency_index import (
        drain_invalidated_cache_keys_for_graphql_rewarm,
        record_committed_graphql_rewarm_keys,
    )

    try:
        connection = connections[database_alias]
    except ConnectionDoesNotExist:
        return False
    if connection.connection is None or (
        not connection.in_atomic_block and not connection.autocommit
    ):
        return False
    keys = drain_invalidated_cache_keys_for_graphql_rewarm()
    if completed and keys:

        def committed_rewarm() -> None:
            try:
                record_committed_graphql_rewarm_keys(keys)
                flush_transaction_rewarm_keys()
            except Exception:
                logger.exception("GraphQL warm-up requeue failed.")

        connection.on_commit(committed_rewarm)
    return True


def flush_transaction_rewarm_keys() -> None:
    """Dispatch shared committed work after the final mutation barrier closes."""
    try:
        from general_manager.cache.dependency_index import (
            drain_committed_graphql_rewarm_keys,
        )
        from general_manager.api.graphql_warmup import enqueue_graphql_recipe_warmup

        keys = drain_committed_graphql_rewarm_keys()
        if keys:
            enqueue_graphql_recipe_warmup(keys)
    except Exception:
        logger.exception("GraphQL warm-up requeue failed.")
