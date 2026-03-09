"""Production workflow engine using DB persistence and Celery execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable, Mapping, cast
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
)
from general_manager.workflow.tasks import CELERY_AVAILABLE, execute_workflow_handler
from general_manager.workflow.telemetry import increment_execution_state


def _handler_path(workflow: WorkflowDefinition) -> str | None:
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


def _to_execution(record: Any) -> WorkflowExecution:
    return WorkflowExecution(
        execution_id=record.execution_id,
        workflow_id=record.workflow_id,
        state=record.state,  # type: ignore[arg-type]
        input_data=record.input_data,
        output_data=record.output_data,
        correlation_id=record.correlation_id,
        started_at=record.started_at,
        ended_at=record.ended_at,
        error=record.error,
        metadata=record.metadata,
    )


def _run_inline_handler(
    handler: Callable[[dict[str, Any]], Mapping[str, Any] | None],
    payload: dict[str, Any],
) -> tuple[str, Mapping[str, Any] | None, str | None]:
    try:
        result = handler(payload) or {}
        return "completed", dict(result), None
    except Exception as exc:  # noqa: BLE001
        return "failed", None, str(exc)


class CeleryWorkflowEngine:
    """Durable workflow engine for production workloads."""

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
        input_data: Mapping[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkflowExecution:
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
            state = "pending" if has_runnable_handler else "completed"
            output_data: Mapping[str, Any] | None = None if has_runnable_handler else {}
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
                                handler = imported
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
        signal: Mapping[str, Any] | None = None,
    ) -> WorkflowExecution:
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
                    state=record.state,
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
                    state=record.state,
                    expected_states=ACTIVE_WORKFLOW_STATES,
                )
            record.state = "cancelled"
            record.error = reason
            record.ended_at = self._utcnow()
            record.save(update_fields=["state", "error", "ended_at", "updated_at"])
            return _to_execution(record)

    def status(self, execution_id: str) -> WorkflowExecution:
        from general_manager.workflow.models import WorkflowExecutionRecord

        record = WorkflowExecutionRecord.objects.filter(
            execution_id=execution_id
        ).first()
        if record is None:
            raise WorkflowExecutionNotFoundError(execution_id)
        return _to_execution(record)
