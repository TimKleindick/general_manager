"""Production workflow engine using DB persistence and Celery execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4

from django.db import transaction

from general_manager.workflow.config import workflow_async_enabled
from general_manager.workflow.engine import (
    WorkflowCancelledError,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowExecutionNotFoundError,
)
from general_manager.workflow.tasks import CELERY_AVAILABLE, execute_workflow_handler


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


class CeleryWorkflowEngine:
    """Durable workflow engine for production workloads."""

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC)

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
                existing = WorkflowExecutionRecord.objects.filter(
                    correlation_id=correlation_id,
                    workflow_id=workflow.workflow_id,
                    state="completed",
                ).first()
                if existing is not None:
                    return _to_execution(existing)

            execution_id = str(uuid4())
            started_at = self._utcnow()
            handler_path = _handler_path(workflow)
            merged_metadata = dict(metadata or {})
            if handler_path is not None:
                merged_metadata["handler_path"] = handler_path
            state = "pending" if handler_path else "completed"
            output_data: Mapping[str, Any] | None = {} if handler_path is None else None
            record = WorkflowExecutionRecord.objects.create(
                execution_id=execution_id,
                workflow_id=workflow.workflow_id,
                state=state,
                input_data=dict(input_data or {}),
                output_data=output_data,
                correlation_id=correlation_id,
                started_at=started_at,
                ended_at=started_at if state == "completed" else None,
                metadata=merged_metadata,
            )
            if handler_path is not None:
                if workflow_async_enabled() and CELERY_AVAILABLE:
                    transaction.on_commit(
                        lambda: execute_workflow_handler.delay(
                            execution_id, handler_path, dict(input_data or {})
                        )
                    )
                elif workflow.handler is not None:
                    try:
                        result = workflow.handler(dict(input_data or {})) or {}
                        record.state = "completed"
                        record.output_data = dict(result)
                        record.ended_at = self._utcnow()
                    except Exception as exc:  # noqa: BLE001
                        record.state = "failed"
                        record.error = str(exc)
                        record.ended_at = self._utcnow()
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

        record = WorkflowExecutionRecord.objects.filter(
            execution_id=execution_id
        ).first()
        if record is None:
            raise WorkflowExecutionNotFoundError(execution_id)
        if record.state == "cancelled":
            raise WorkflowCancelledError(execution_id)
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

        record = WorkflowExecutionRecord.objects.filter(
            execution_id=execution_id
        ).first()
        if record is None:
            raise WorkflowExecutionNotFoundError(execution_id)
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
