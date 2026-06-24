"""Production workflow engine using DB persistence and Celery execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.utils.module_loading import import_string

from general_manager.workflow.config import workflow_async_enabled
from general_manager.workflow.engine import (
    ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES,
    ACTIVE_WORKFLOW_STATES,
    WorkflowCancelledError,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowExecutionNotFoundError,
    WorkflowInvalidStateError,
    WorkflowState,
)
from general_manager.workflow.tasks import CELERY_AVAILABLE, execute_workflow_handler
from general_manager.workflow.telemetry import increment_execution_state

WorkflowPayload = Mapping[str, object]
WorkflowPayloadDict = dict[str, object]
WorkflowHandler = Callable[[WorkflowPayload], WorkflowPayload | None]


class _WorkflowExecutionRecord(Protocol):
    """Typed subset of the Django execution model used to build DTOs."""

    execution_id: str
    workflow_id: str
    state: str
    input_data: WorkflowPayload
    output_data: WorkflowPayload | None
    correlation_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    error: str | None
    metadata: WorkflowPayload


def _handler_path(workflow: WorkflowDefinition) -> str | None:
    """Return the import path used to dispatch a workflow handler, if any."""
    metadata_path = workflow.metadata.get("handler_path")
    if isinstance(metadata_path, str) and metadata_path:
        return metadata_path
    handler = workflow.handler
    if handler is None:
        return None
    module = getattr(handler, "__module__", "")
    qualname = getattr(handler, "__qualname__", "")
    if module and qualname and "<locals>" not in qualname:
        return f"{module}.{qualname}"
    return None


def _to_execution(record: _WorkflowExecutionRecord) -> WorkflowExecution:
    """Convert a persisted execution record into the public DTO."""
    return WorkflowExecution(
        execution_id=record.execution_id,
        workflow_id=record.workflow_id,
        state=cast(WorkflowState, record.state),
        input_data=record.input_data,
        output_data=record.output_data,
        correlation_id=record.correlation_id,
        started_at=record.started_at,
        ended_at=record.ended_at,
        error=record.error,
        metadata=record.metadata,
    )


def _run_inline_handler(
    handler: WorkflowHandler,
    payload: WorkflowPayloadDict,
) -> tuple[WorkflowState, WorkflowPayload | None, str | None]:
    """Run a synchronous workflow handler and normalize its terminal state."""
    try:
        result = handler(payload) or {}
        return "completed", dict(result), None
    except Exception as exc:  # noqa: BLE001
        return "failed", None, str(exc)


class CeleryWorkflowEngine:
    """
    Durable workflow engine backed by `WorkflowExecutionRecord` rows.

    The engine stores every execution in the database. In sync mode it runs
    importable or inline handlers in the caller process. In async mode it creates
    a pending execution and schedules `execute_workflow_handler` with Celery
    after the surrounding transaction commits.

    Returned `WorkflowExecution` objects are snapshots. In async mode, the
    Celery worker may update the execution immediately after `start()` returns;
    call `status()` when fresh state is required.
    """

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _get_existing_correlation_execution(
        workflow_id: str, correlation_id: str
    ) -> WorkflowExecution | None:
        from general_manager.workflow.models import WorkflowExecutionRecord

        record = (
            WorkflowExecutionRecord.objects.filter(
                correlation_id=correlation_id,
                workflow_id=workflow_id,
                state__in=ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES,
            )
            .order_by("created_at", "pk")
            .first()
        )
        if record is None:
            return None
        return _to_execution(record)

    def start(
        self,
        workflow: WorkflowDefinition,
        input_data: WorkflowPayload | None = None,
        *,
        correlation_id: str | None = None,
        metadata: WorkflowPayload | None = None,
    ) -> WorkflowExecution:
        """
        Persist and start one workflow execution.

        Parameters:
            workflow: Workflow definition to execute.
            input_data: Optional JSON-like payload copied with `dict(...)`.
            correlation_id: Optional durable dedupe key scoped to
                `workflow.workflow_id`.
            metadata: Optional metadata copied with `dict(...)`.

        Returns:
            WorkflowExecution: The new execution, or an existing active or
            completed execution with the same `(workflow_id, correlation_id)`.

        Side effects:
            Creates a `WorkflowExecutionRecord`, records execution-state
            telemetry, and in async mode schedules the Celery handler task after
            transaction commit. In sync mode, handler output is stored inline.

        In async mode, handlers must resolve to importable top-level callables.
        A workflow with neither `workflow.handler` nor `metadata["handler_path"]`
        is intentionally handlerless and completes immediately with `{}` output.
        A workflow with an inline/local handler that cannot be represented as a
        handler path is malformed for async mode and is recorded as failed.
        Non-callable imports, import failures, and missing Celery support also
        create failed execution records instead of raising. Runtime async
        handler import/call/output failures are captured later by
        `execute_workflow_handler` as failed execution records. In sync mode,
        handler exceptions and invalid truthy handler outputs are captured as
        failed executions.

        `workflow.workflow_id` is the durable workflow identity. When an
        existing active or completed execution is reused for a correlation id,
        the new `input_data` and `metadata` are ignored. Failed executions are
        not reused, so the same correlation id may start another attempt.
        User metadata is copied before persistence; when a handler path is
        available, the engine writes/overwrites `metadata["handler_path"]` with
        the import path used for dispatch. Payload and metadata keys must be
        strings at the top level because the public type is
        `Mapping[str, object]`; nested values are accepted or rejected by the
        configured Django `JSONField`. Serialization failures propagate. The
        returned object is a snapshot of the row after creation and any inline
        sync handler execution, not a live object.

        Raises:
            IntegrityError: If the execution insert fails and no reusable
                correlation execution can be found.
            Exception: Propagates unexpected database, transaction, telemetry,
                or Celery task scheduling errors.
        """
        with transaction.atomic():
            from general_manager.workflow.models import WorkflowExecutionRecord

            if correlation_id:
                existing = self._get_existing_correlation_execution(
                    workflow.workflow_id, correlation_id
                )
                if existing is not None:
                    return existing

            execution_id = str(uuid4())
            started_at = self._utcnow()
            handler_path = _handler_path(workflow)
            merged_metadata = dict(metadata or {})
            if handler_path is not None:
                merged_metadata["handler_path"] = handler_path
            has_runnable_handler = (
                workflow.handler is not None or handler_path is not None
            )
            async_mode = workflow_async_enabled()
            state: WorkflowState = "pending" if has_runnable_handler else "completed"
            output_data: WorkflowPayload | None = None if has_runnable_handler else {}
            error: str | None = None
            if has_runnable_handler and async_mode:
                if handler_path is None:
                    state = "failed"
                    error = "Workflow async mode requires an importable top-level handler path."
                elif not CELERY_AVAILABLE:
                    state = "failed"
                    error = "Workflow async mode requires Celery to be installed."
                else:
                    try:
                        resolved = import_string(handler_path)
                        if not callable(resolved):
                            state = "failed"
                            error = (
                                f"Failed to resolve workflow handler path '{handler_path}': "
                                "handler is not callable"
                            )
                    except Exception as exc:  # noqa: BLE001
                        state = "failed"
                        error = f"Failed to resolve workflow handler path '{handler_path}': {exc}"
            try:
                record = WorkflowExecutionRecord.objects.create(
                    execution_id=execution_id,
                    workflow_id=workflow.workflow_id,
                    state=state,
                    input_data=dict(input_data or {}),
                    output_data=output_data,
                    correlation_id=correlation_id,
                    started_at=started_at,
                    ended_at=started_at
                    if state in {"completed", "failed", "cancelled"}
                    else None,
                    error=error,
                    metadata=merged_metadata,
                )
            except IntegrityError:
                if not correlation_id:
                    raise
                existing = self._get_existing_correlation_execution(
                    workflow.workflow_id, correlation_id
                )
                if existing is None:
                    raise
                return existing
            increment_execution_state(state)
            if has_runnable_handler:
                should_dispatch_async = (
                    async_mode
                    and CELERY_AVAILABLE
                    and handler_path is not None
                    and state == "pending"
                )
                if should_dispatch_async:
                    dispatch_handler_path = cast(str, handler_path)
                    transaction.on_commit(
                        lambda: execute_workflow_handler.delay(
                            execution_id,
                            dispatch_handler_path,
                            dict(input_data or {}),
                        )
                    )
                elif not async_mode:
                    handler = workflow.handler
                    if handler is None and handler_path is not None:
                        try:
                            imported = import_string(handler_path)
                        except Exception as exc:  # noqa: BLE001
                            record.state = "failed"
                            record.output_data = None
                            record.error = f"Failed to resolve workflow handler path '{handler_path}': {exc}"
                            record.ended_at = self._utcnow()
                            increment_execution_state("failed")
                        else:
                            if callable(imported):
                                handler = cast(WorkflowHandler, imported)
                            else:
                                record.state = "failed"
                                record.output_data = None
                                record.error = (
                                    f"Failed to resolve workflow handler path '{handler_path}': "
                                    f"handler is not callable ({type(imported).__name__})"
                                )
                                record.ended_at = self._utcnow()
                                increment_execution_state("failed")
                    if handler is not None:
                        final_state, final_output, final_error = _run_inline_handler(
                            handler, dict(input_data or {})
                        )
                        record.state = final_state
                        record.output_data = final_output
                        record.error = final_error
                        record.ended_at = self._utcnow()
                        increment_execution_state(final_state)
                    record.save(
                        update_fields=[
                            "state",
                            "output_data",
                            "ended_at",
                            "error",
                            "updated_at",
                        ]
                    )
            return _to_execution(record)

    def resume(
        self,
        execution_id: str,
        signal: WorkflowPayload | None = None,
    ) -> WorkflowExecution:
        """
        Complete a waiting workflow execution with an optional resume signal.

        Parameters:
            execution_id: Durable execution id.
            signal: Optional JSON-like payload copied into execution metadata
                under `resume_signal`.

        Returns:
            WorkflowExecution: The completed execution record.

        The update is applied inline and immediately changes the persisted state
        from `waiting` to `completed`; it does not enqueue a Celery task. A
        falsey `signal` is ignored. A truthy signal is copied into
        `metadata["resume_signal"]`. Output data is not changed.

        Raises:
            WorkflowExecutionNotFoundError: If `execution_id` is unknown.
            WorkflowCancelledError: If the execution is already `cancelled`.
            WorkflowInvalidStateError: If the execution is `pending`, `running`,
                `completed`, or `failed`.
            Exception: Propagates unexpected database or transaction errors.
        """
        from general_manager.workflow.models import WorkflowExecutionRecord

        with transaction.atomic():
            record = (
                WorkflowExecutionRecord.objects.select_for_update()
                .filter(execution_id=execution_id)
                .first()
            )
            if record is None:
                raise WorkflowExecutionNotFoundError(execution_id)
            if record.state == "cancelled":
                raise WorkflowCancelledError(execution_id)
            if record.state != "waiting":
                raise WorkflowInvalidStateError(
                    execution_id,
                    operation="resume",
                    state=cast(WorkflowState, record.state),
                    expected_states=("waiting",),
                )
            metadata = dict(record.metadata)
            if signal:
                metadata["resume_signal"] = dict(signal)
            record.metadata = metadata
            record.state = "completed"
            record.ended_at = self._utcnow()
            record.save(update_fields=["metadata", "state", "ended_at", "updated_at"])
            return _to_execution(record)

    def cancel(
        self, execution_id: str, *, reason: str | None = None
    ) -> WorkflowExecution:
        """
        Cancel an active workflow execution.

        Parameters:
            execution_id: Durable execution id.
            reason: Optional cancellation reason stored as the execution error.

        Returns:
            WorkflowExecution: The cancelled execution record.

        The update is applied inline and immediately changes the persisted state
        to `cancelled`; it does not enqueue a Celery task. `reason` is stored in
        the execution `error` field and returned as `WorkflowExecution.error`.

        Raises:
            WorkflowExecutionNotFoundError: If `execution_id` is unknown.
            WorkflowCancelledError: If the execution is already cancelled.
            WorkflowInvalidStateError: If the execution is `completed` or
                `failed`. Active states are `pending`, `running`, and `waiting`.
            Exception: Propagates unexpected database or transaction errors.
        """
        from general_manager.workflow.models import WorkflowExecutionRecord

        with transaction.atomic():
            record = (
                WorkflowExecutionRecord.objects.select_for_update()
                .filter(execution_id=execution_id)
                .first()
            )
            if record is None:
                raise WorkflowExecutionNotFoundError(execution_id)
            if record.state == "cancelled":
                raise WorkflowCancelledError(execution_id)
            if record.state not in ACTIVE_WORKFLOW_STATES:
                raise WorkflowInvalidStateError(
                    execution_id,
                    operation="cancel",
                    state=cast(WorkflowState, record.state),
                    expected_states=ACTIVE_WORKFLOW_STATES,
                )
            record.state = "cancelled"
            record.error = reason
            record.ended_at = self._utcnow()
            record.save(update_fields=["state", "error", "ended_at", "updated_at"])
            return _to_execution(record)

    def status(self, execution_id: str) -> WorkflowExecution:
        """
        Return the current persisted execution state.

        The database record stores state as a raw string; the returned
        `WorkflowExecution` snapshot exposes that value narrowed to the public
        `WorkflowState` vocabulary.

        Parameters:
            execution_id: Durable execution id.

        Returns:
            WorkflowExecution: Snapshot of the stored execution record.

        Raises:
            WorkflowExecutionNotFoundError: If `execution_id` is unknown.
            Exception: Propagates unexpected database errors.
        """
        from general_manager.workflow.models import WorkflowExecutionRecord

        record = WorkflowExecutionRecord.objects.filter(
            execution_id=execution_id
        ).first()
        if record is None:
            raise WorkflowExecutionNotFoundError(execution_id)
        return _to_execution(record)
