"""Celery tasks for workflow event routing and execution."""

from __future__ import annotations

from secrets import randbelow
from datetime import UTC, datetime
from typing import Any, Mapping, cast

from django.db import transaction
from django.conf import settings
from django.utils.module_loading import import_string

from general_manager.logging import get_logger
from general_manager.workflow.config import (
    workflow_beat_enabled,
    workflow_beat_max_jitter_seconds,
    workflow_beat_outbox_interval_seconds,
    workflow_outbox_process_chunk_size,
)
from general_manager.workflow.engine import WorkflowExecutionNotFoundError
from general_manager.workflow.engine import ACTIVE_WORKFLOW_STATES
from general_manager.workflow.event_registry import (
    DatabaseEventRegistry,
    get_event_registry,
)
from general_manager.workflow.telemetry import (
    increment_execution_state,
    set_outbox_snapshot,
)

logger = get_logger("workflow.tasks")

try:
    from celery import current_app, shared_task

    CELERY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency boundary
    CELERY_AVAILABLE = False
    current_app = cast(Any | None, None)  # type: ignore[assignment, no-redef]

    def shared_task(func: Any | None = None, **_kwargs: Any):  # type: ignore[no-redef]
        def decorator(inner):
            return inner

        if func is None:
            return decorator
        return decorator(func)


WORKFLOW_BEAT_SCHEDULE_KEY = "general_manager.workflow.publish_outbox_batch"


def configure_workflow_beat_schedule_from_settings(
    django_settings: Any = settings,
) -> bool:
    """Register workflow outbox periodic drain schedule in Celery Beat."""
    if not workflow_beat_enabled(django_settings):
        return False
    if not CELERY_AVAILABLE or current_app is None:
        logger.warning("workflow beat schedule skipped; celery unavailable")
        return False
    schedule: dict[str, Any] = dict(
        getattr(current_app.conf, "beat_schedule", {}) or {}
    )
    interval_seconds = float(workflow_beat_outbox_interval_seconds(django_settings))
    jitter_seconds = workflow_beat_max_jitter_seconds(django_settings)
    if jitter_seconds > 0:
        max_millis = max(1, int(float(jitter_seconds) * 1000))
        interval_seconds += randbelow(max_millis + 1) / 1000.0
    schedule[WORKFLOW_BEAT_SCHEDULE_KEY] = {
        "task": "general_manager.workflow.tasks.publish_outbox_batch",
        "schedule": interval_seconds,
        "options": {"queue": "workflow.events"},
    }
    current_app.conf.beat_schedule = schedule
    logger.info(
        "workflow beat schedule configured",
        context={
            "schedule_key": WORKFLOW_BEAT_SCHEDULE_KEY,
            "interval_seconds": interval_seconds,
        },
    )
    return True


@shared_task(queue="workflow.events")
def publish_outbox_batch() -> int:
    """Claim and dispatch pending outbox entries."""
    registry = get_event_registry()
    if not isinstance(registry, DatabaseEventRegistry):
        return 0
    claims = registry.claim_outbox_batch(
        batch_size=workflow_outbox_process_chunk_size()
    )
    if claims:
        if CELERY_AVAILABLE:
            route_outbox_claims_batch.delay(claims)
        else:
            route_outbox_claims_batch(claims)
    pending_count, oldest_age = registry.outbox_snapshot()
    set_outbox_snapshot(
        pending_count=pending_count,
        oldest_pending_age_seconds=oldest_age,
    )
    return len(claims)


@shared_task(queue="workflow.events")
def route_outbox_event(outbox_id: int, claim_token: str | None = None) -> bool:
    """Route a single outbox event through the active registry."""
    registry = get_event_registry()
    if not isinstance(registry, DatabaseEventRegistry):
        return False
    return registry.process_outbox_entry(outbox_id, claim_token=claim_token)


@shared_task(queue="workflow.events")
def route_outbox_claims_batch(claims: list[tuple[int, str]]) -> int:
    """Route a claimed outbox batch with per-entry isolation."""
    routed = 0
    for outbox_id, claim_token in claims:
        try:
            if route_outbox_event(outbox_id, claim_token):
                routed += 1
        except Exception:  # pragma: no cover - defensive log path
            logger.exception(
                "workflow outbox batch item failed",
                context={"outbox_id": outbox_id},
            )
    return routed


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

    with transaction.atomic():
        execution = (
            WorkflowExecutionRecord.objects.select_for_update()
            .filter(execution_id=execution_id)
            .first()
        )
        if execution is None:
            raise WorkflowExecutionNotFoundError(execution_id)
        if execution.state != "pending":
            return
        execution.state = "running"
        execution.save(update_fields=["state", "updated_at"])
        increment_execution_state("running")
    try:
        handler = _resolve_handler(handler_path)
        result = handler(dict(input_data or {})) if callable(handler) else {}
        end_time = datetime.now(UTC)
        updated = WorkflowExecutionRecord.objects.filter(
            execution_id=execution_id, state="running"
        ).update(
            state="completed",
            output_data=dict(result or {}),
            error=None,
            ended_at=end_time,
            updated_at=end_time,
        )
        if updated == 0:
            return
        increment_execution_state("completed")
    except Exception as exc:
        logger.exception(
            "workflow execution failed",
            context={"execution_id": execution_id, "handler_path": handler_path},
        )
        end_time = datetime.now(UTC)
        updated = WorkflowExecutionRecord.objects.filter(
            execution_id=execution_id, state="running"
        ).update(
            state="failed",
            error=str(exc),
            output_data=None,
            ended_at=end_time,
            updated_at=end_time,
        )
        if updated == 0:
            return
        increment_execution_state("failed")


@shared_task(queue="workflow.executions")
def resume_execution_task(
    execution_id: str,
    signal: Mapping[str, Any] | None = None,
) -> bool:
    """Resume a persisted execution record."""
    from general_manager.workflow.models import WorkflowExecutionRecord

    with transaction.atomic():
        execution = (
            WorkflowExecutionRecord.objects.select_for_update()
            .filter(execution_id=execution_id)
            .first()
        )
        if execution is None:
            raise WorkflowExecutionNotFoundError(execution_id)
        if execution.state != "waiting":
            return False
        metadata = dict(execution.metadata)
        if signal:
            metadata["resume_signal"] = dict(signal)
        execution.metadata = metadata
        execution.state = "completed"
        execution.ended_at = datetime.now(UTC)
        execution.save(update_fields=["metadata", "state", "ended_at", "updated_at"])
    increment_execution_state("completed")
    return True


@shared_task(queue="workflow.executions")
def cancel_execution_task(execution_id: str, reason: str | None = None) -> bool:
    """Cancel a persisted execution record."""
    from general_manager.workflow.models import WorkflowExecutionRecord

    with transaction.atomic():
        execution = (
            WorkflowExecutionRecord.objects.select_for_update()
            .filter(execution_id=execution_id)
            .first()
        )
        if execution is None:
            raise WorkflowExecutionNotFoundError(execution_id)
        if execution.state not in ACTIVE_WORKFLOW_STATES:
            return False
        execution.state = "cancelled"
        execution.error = reason
        execution.ended_at = datetime.now(UTC)
        execution.save(update_fields=["state", "error", "ended_at", "updated_at"])
    increment_execution_state("cancelled")
    return True
