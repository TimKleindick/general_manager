"""ORM implementation of the optional bounded create-many capability."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, ClassVar

from django.db import DEFAULT_DB_ALIAS, connections, router, transaction

from general_manager.as_of import reject_historical_mutation
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.logging import get_logger
from general_manager.manager.bulk_create import (
    CreateManyBatchContext,
    CreateManyBatchResult,
    CreateManyCallerScope,
    CreateManyError,
    CreateManyInvalidBatchSizeError,
    CreateManyPostCommitError,
    CreateManyUnsupportedError,
    create_many_batch_context,
    enclosing_create_many_batch,
    validate_create_many_record,
    validate_create_many_transaction,
    validate_create_many_workflow,
)

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager

logger = get_logger("interface.orm.create_many")


def _flush_create_many_search_work(context: CreateManyBatchContext) -> None:
    """Schedule one merged search plan while the successful batch is still atomic."""
    if not context.search_work:
        return
    from general_manager.search.invalidation import (
        SearchInvalidationPlan,
        SearchScheduledWork,
        schedule_search_invalidation_work,
    )

    works = tuple(
        work for work in context.search_work if isinstance(work, SearchScheduledWork)
    )
    if not works:
        return
    merged = SearchScheduledWork(
        upserts=SearchInvalidationPlan(
            targets=tuple(target for work in works for target in work.upserts.targets),
            dirty_fallbacks=tuple(
                fallback for work in works for fallback in work.upserts.dirty_fallbacks
            ),
        ),
        deletes=tuple(target for work in works for target in work.deletes),
    )
    context.flushing_search_work = True
    try:
        schedule_search_invalidation_work(
            merged,
            source_database_alias=context.database_alias,
        )
    finally:
        context.flushing_search_work = False


def _flush_create_many_notifications(context: CreateManyBatchContext) -> None:
    """Run captured subscription publishers under their existing coalescing context."""
    if not context.notification_callbacks:
        return
    from general_manager.api.notification_batching import bulk_data_change_notifications

    with bulk_data_change_notifications():
        for callback in context.notification_callbacks:
            callback()


def _register_create_many_notifications(context: CreateManyBatchContext) -> None:
    """Install one outer-commit notification flush for a provisional batch."""
    if context.notifications_registered or not context.notification_callbacks:
        return
    context.notifications_registered = True
    transaction.on_commit(
        lambda: _flush_create_many_notifications(context),
        using=context.database_alias,
    )


def _create_many_identifier(
    manager: GeneralManager,
    manager_name: str,
) -> object:
    """Return the canonical identifier or reject a non-ORM result payload."""
    identification = manager.identification
    if "id" not in identification:
        raise CreateManyUnsupportedError.missing_id(manager_name)
    return identification["id"]


class OrmCreateManyCapability(BaseCapability):
    """Preserve canonical ORM writes or select the safe opt-in SQL path.

    Owns batch transactions, routing, permissions, history, lifecycle delivery,
    and progress/error attribution. Arbitrary backend handlers can replace this
    capability without changing GeneralManager or InterfaceBase.
    """

    name: ClassVar[CapabilityName] = "create_many"

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
        """Create ORM records in bounded, independently atomic batches.

        The returned iterator consumes and writes one batch at a time. Rows use
        canonical manager creation unless the manager explicitly opts into safe
        SQL batching. Validation, rules, history and lifecycle contracts govern
        eligibility; custom hooks select canonical creation. A batch is all-or-nothing; successful earlier batches remain
        available for checkpointing unless the caller owns an outer transaction.
        """
        from general_manager.manager.general_manager import GeneralManager

        cls = manager_class
        reject_historical_mutation()
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size <= 0
        ):
            raise CreateManyInvalidBatchSizeError
        canonical_create = getattr(
            GeneralManager.create, "__func__", GeneralManager.create
        )
        manager_create = getattr(cls.create, "__func__", cls.create)
        if manager_create is not canonical_create:
            raise CreateManyUnsupportedError.custom_create(cls.__name__)

        from general_manager.interface.orm_interface import OrmInterfaceBase
        from general_manager.interface.base_interface import InterfaceBase
        from general_manager.interface.capabilities.orm.mutations import (
            OrmCreateCapability,
        )
        from general_manager.interface.capabilities.orm.support import (
            get_support_capability,
        )

        interface = interface_cls
        if not issubclass(interface, OrmInterfaceBase):
            raise CreateManyUnsupportedError.non_orm_interface(cls.__name__)
        if (
            getattr(interface.create, "__func__", interface.create)
            is not getattr(InterfaceBase.create, "__func__", InterfaceBase.create)
            or type(interface.get_capability_handler("create"))
            is not OrmCreateCapability
        ):
            raise CreateManyUnsupportedError.non_orm_interface(cls.__name__)
        database_alias = (
            get_support_capability(interface).get_database_alias(interface)
            or DEFAULT_DB_ALIAS
        )
        if (
            not getattr(interface, "database", None)
            and router.db_for_write(interface._model) != database_alias
        ):
            raise CreateManyUnsupportedError.routing()
        from general_manager.manager.bulk_create import validate_create_many_history

        validate_create_many_history(interface._model)
        from general_manager.workflow.event_registry import get_event_registry

        validate_create_many_workflow(database_alias, get_event_registry())

        from general_manager.interface.capabilities.orm.bulk import (
            BulkCreateRecordError,
            bulk_create_eligibility,
            create_many_with_bulk_sql,
            publish_bulk_created_rows,
        )

        def _iterate() -> Iterator[CreateManyBatchResult]:
            successful_count = 0
            committed_successful_count = 0
            pending_successful_count = 0
            caller_scope: CreateManyCallerScope | None = None
            next_index = 0
            try:
                source = iter(records)
            except Exception as error:
                raise CreateManyError(
                    failure_index=0,
                    batch_start_index=0,
                    batch_end_index=0,
                    cause=error,
                    successful_count=0,
                    committed_successful_count=0,
                    pending_successful_count=0,
                    database_alias=database_alias,
                    committed=False,
                ) from error
            while True:
                reject_historical_mutation()
                connection = connections[database_alias]
                if not connection.in_atomic_block and not connection.get_autocommit():
                    raise CreateManyUnsupportedError.manual_transaction()
                if caller_scope is not None:
                    try:
                        caller_scope.validate(database_alias)
                    except CreateManyUnsupportedError as error:
                        raise CreateManyError(
                            failure_index=next_index,
                            batch_start_index=next_index,
                            batch_end_index=next_index,
                            cause=error,
                            successful_count=successful_count,
                            committed_successful_count=committed_successful_count,
                            pending_successful_count=pending_successful_count,
                            database_alias=database_alias,
                            committed=False,
                        ) from error
                batch_start_index = next_index
                batch: list[Mapping[str, object]] = []
                try:
                    for _ in range(batch_size):
                        batch.append(next(source))
                except StopIteration:
                    pass
                except Exception as error:
                    raise CreateManyError(
                        failure_index=batch_start_index + len(batch),
                        batch_start_index=batch_start_index,
                        batch_end_index=batch_start_index + len(batch),
                        cause=error,
                        successful_count=successful_count,
                        committed_successful_count=committed_successful_count,
                        pending_successful_count=pending_successful_count,
                        database_alias=database_alias,
                        committed=False,
                    ) from error
                if not batch:
                    return

                next_index += len(batch)
                caller_owns_transaction = connections[database_alias].in_atomic_block
                ids: list[object] = []
                failure_index: int | None = None
                committed_before_callbacks = {"value": False}
                batch_context = None
                try:
                    for offset, record in enumerate(batch):
                        failure_index = batch_start_index + offset
                        validate_create_many_record(record)
                    # The iterator is intentionally lazy.  Re-check opt-in
                    # safety after source consumption so a receiver/config
                    # installed between yielded batches cannot be bypassed.
                    bulk_eligibility = bulk_create_eligibility(cls)
                    # Nested imports keep the ordinary mutation/savepoint path;
                    # the enclosing signal scope belongs to their caller.
                    use_bulk_sql = (
                        bulk_eligibility.eligible
                        and enclosing_create_many_batch() is None
                    )
                    with transaction.atomic(using=database_alias):
                        transaction.on_commit(
                            lambda marker=committed_before_callbacks: marker.__setitem__(
                                "value", True
                            ),
                            using=database_alias,
                        )
                        with create_many_batch_context(
                            database_alias,
                            caller_owns_transaction=caller_owns_transaction,
                            manager_class=cls,
                        ) as context:
                            batch_context = context
                            if use_bulk_sql:
                                from general_manager.cache.dependency_index import (
                                    begin_dependency_data_change,
                                    drain_invalidated_cache_keys_for_graphql_rewarm,
                                    end_dependency_data_change,
                                    is_dependency_data_change_active,
                                )
                                from general_manager.cache.run_context import (
                                    current_calculation_run_context,
                                )
                                from general_manager.cache.batch_refresh import (
                                    flush_batch_refresh_callbacks,
                                )
                                from general_manager.manager.bulk_create import (
                                    create_many_signal_scope,
                                )

                                run_context = current_calculation_run_context()
                                bulk_succeeded = False
                                with create_many_signal_scope():
                                    begin_dependency_data_change()
                                    try:
                                        if run_context is not None:
                                            run_context.clear_orm_bucket_results()
                                            run_context.clear_bucket_indexes()
                                            run_context.clear_bucket_projections()
                                            run_context.clear_trusted_orm_managers()
                                        context.bulk_sql_active = True
                                        failure_index = None
                                        try:
                                            persisted_rows = create_many_with_bulk_sql(
                                                cls,
                                                batch,
                                                creator_id=creator_id,
                                                history_comment=history_comment,
                                                ignore_permission=ignore_permission,
                                                database_alias=database_alias,
                                            )
                                        except BulkCreateRecordError as error:
                                            failure_index = (
                                                batch_start_index + error.index
                                            )
                                            raise error.cause from error
                                        ids.extend(row.pk for row in persisted_rows)
                                        failure_index = None
                                        publish_bulk_created_rows(
                                            cls,
                                            persisted_rows,
                                            records=batch,
                                            creator_id=creator_id,
                                            history_comment=history_comment,
                                            ignore_permission=ignore_permission,
                                            database_alias=database_alias,
                                        )
                                        flush_batch_refresh_callbacks(context)
                                        bulk_succeeded = True
                                    finally:
                                        context.bulk_sql_active = False
                                        if run_context is not None:
                                            run_context.clear_orm_bucket_results()
                                            run_context.clear_bucket_indexes()
                                            run_context.clear_bucket_projections()
                                            run_context.clear_trusted_orm_managers()
                                        end_dependency_data_change()
                                        cache_keys = (
                                            drain_invalidated_cache_keys_for_graphql_rewarm()
                                            if not is_dependency_data_change_active()
                                            else ()
                                        )
                                        if bulk_succeeded and cache_keys:
                                            from general_manager.api.graphql_warmup import (
                                                enqueue_graphql_recipe_warmup,
                                            )

                                            def rewarm_after_commit(
                                                keys: tuple[str, ...] = cache_keys,
                                            ) -> None:
                                                try:
                                                    enqueue_graphql_recipe_warmup(keys)
                                                except Exception:
                                                    logger.exception(
                                                        "GraphQL warm-up requeue failed."
                                                    )

                                            transaction.on_commit(
                                                rewarm_after_commit,
                                                using=database_alias,
                                            )
                                validate_create_many_transaction(database_alias)
                            else:
                                for offset, record in enumerate(batch):
                                    failure_index = batch_start_index + offset
                                    created = cls.create(
                                        creator_id=creator_id,
                                        history_comment=history_comment,
                                        ignore_permission=ignore_permission,
                                        **record,
                                    )
                                    ids.append(
                                        _create_many_identifier(created, cls.__name__)
                                    )
                                    validate_create_many_transaction(database_alias)
                            failure_index = None
                            _flush_create_many_search_work(context)
                            if not context.committed:
                                _register_create_many_notifications(context)
                            validate_create_many_transaction(database_alias)
                except Exception as error:
                    if committed_before_callbacks["value"]:
                        raise CreateManyPostCommitError(
                            ids=tuple(ids),
                            failure_index=failure_index,
                            batch_start_index=batch_start_index,
                            batch_end_index=next_index,
                            cause=error,
                            successful_count=successful_count + len(ids),
                            committed_successful_count=(
                                committed_successful_count + len(ids)
                            ),
                            pending_successful_count=pending_successful_count,
                            database_alias=database_alias,
                            committed=True,
                        ) from error
                    raise CreateManyError(
                        failure_index=failure_index,
                        batch_start_index=batch_start_index,
                        batch_end_index=next_index,
                        cause=error,
                        successful_count=successful_count,
                        committed_successful_count=committed_successful_count,
                        pending_successful_count=pending_successful_count,
                        database_alias=database_alias,
                        committed=False,
                    ) from error

                assert batch_context is not None
                if batch_context.committed:
                    try:
                        for callback in batch_context.workflow_callbacks:
                            callback()
                        _flush_create_many_notifications(batch_context)
                    except Exception as error:
                        raise CreateManyPostCommitError(
                            ids=tuple(ids),
                            failure_index=None,
                            batch_start_index=batch_start_index,
                            batch_end_index=next_index,
                            cause=error,
                            successful_count=successful_count + len(ids),
                            committed_successful_count=(
                                committed_successful_count + len(ids)
                            ),
                            pending_successful_count=pending_successful_count,
                            database_alias=database_alias,
                            committed=True,
                        ) from error
                successful_count += len(ids)
                if batch_context.committed:
                    committed_successful_count += len(ids)
                else:
                    if caller_scope is None:
                        caller_scope = CreateManyCallerScope.capture(database_alias)
                    pending_successful_count += len(ids)
                yield CreateManyBatchResult(
                    start_index=batch_start_index,
                    end_index=next_index,
                    ids=tuple(ids),
                    successful_count=successful_count,
                    committed_successful_count=committed_successful_count,
                    pending_successful_count=pending_successful_count,
                    database_alias=database_alias,
                    committed=batch_context.committed,
                )

        return _iterate()
