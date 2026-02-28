"""Celery tasks for workflow event routing and execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from django.utils.module_loading import import_string

from general_manager.logging import get_logger
from general_manager.workflow.engine import WorkflowExecutionNotFoundError
from general_manager.workflow.event_registry import (
    DatabaseEventRegistry,
    get_event_registry,
)

logger = get_logger("workflow.tasks")

try:
    from celery import shared_task

    CELERY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency boundary
    CELERY_AVAILABLE = False

    def shared_task(func: Any | None = None, **_kwargs: Any):  # type: ignore[no-redef]
        def decorator(inner):
            return inner

        if func is None:
            return decorator
        return decorator(func)


@shared_task(queue="workflow.events")
def publish_outbox_batch() -> int:
    """Claim and dispatch pending outbox entries."""
    registry = get_event_registry()
    if not isinstance(registry, DatabaseEventRegistry):
        return 0
    ids = registry.claim_outbox_batch()
    if not ids:
        return 0
    for outbox_id in ids:
        route_outbox_event.delay(outbox_id) if CELERY_AVAILABLE else route_outbox_event(
            outbox_id
        )
    return len(ids)


@shared_task(queue="workflow.events")
def route_outbox_event(outbox_id: int) -> bool:
    """Route a single outbox event through the active registry."""
    registry = get_event_registry()
    if not isinstance(registry, DatabaseEventRegistry):
        return False
    return registry.process_outbox_entry(outbox_id)


def _resolve_handler(handler_path: str):
    return import_string(handler_path)


@shared_task(queue="workflow.executions")
def execute_workflow_handler(
    execution_id: str,
    handler_path: str,
    input_data: Mapping[str, Any] | None = None,
) -> None:
    """Run workflow handler and persist execution state."""
    from general_manager.workflow.models import WorkflowExecutionRecord

    execution = WorkflowExecutionRecord.objects.filter(
        execution_id=execution_id
    ).first()
    if execution is None:
        raise WorkflowExecutionNotFoundError(execution_id)
    execution.state = "running"
    execution.save(update_fields=["state", "updated_at"])
    try:
        handler = _resolve_handler(handler_path)
        result = handler(dict(input_data or {})) if callable(handler) else {}
        execution.state = "completed"
        execution.output_data = dict(result or {})
        execution.error = None
    except Exception as exc:
        logger.exception(
            "workflow execution failed",
            context={"execution_id": execution_id, "handler_path": handler_path},
        )
        execution.state = "failed"
        execution.error = str(exc)
        execution.output_data = None
    execution.ended_at = datetime.now(UTC)
    execution.save(
        update_fields=["state", "output_data", "error", "ended_at", "updated_at"]
    )


@shared_task(queue="workflow.executions")
def resume_execution_task(
    execution_id: str,
    signal: Mapping[str, Any] | None = None,
) -> bool:
    """Resume a persisted execution record."""
    from general_manager.workflow.models import WorkflowExecutionRecord

    execution = WorkflowExecutionRecord.objects.filter(
        execution_id=execution_id
    ).first()
    if execution is None:
        raise WorkflowExecutionNotFoundError(execution_id)
    metadata = dict(execution.metadata)
    if signal:
        metadata["resume_signal"] = dict(signal)
    execution.metadata = metadata
    execution.state = "completed"
    execution.ended_at = datetime.now(UTC)
    execution.save(update_fields=["metadata", "state", "ended_at", "updated_at"])
    return True


@shared_task(queue="workflow.executions")
def cancel_execution_task(execution_id: str, reason: str | None = None) -> bool:
    """Cancel a persisted execution record."""
    from general_manager.workflow.models import WorkflowExecutionRecord

    execution = WorkflowExecutionRecord.objects.filter(
        execution_id=execution_id
    ).first()
    if execution is None:
        raise WorkflowExecutionNotFoundError(execution_id)
    execution.state = "cancelled"
    execution.error = reason
    execution.ended_at = datetime.now(UTC)
    execution.save(update_fields=["state", "error", "ended_at", "updated_at"])
    return True
