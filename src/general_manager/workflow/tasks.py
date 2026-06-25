"""Celery tasks for workflow event routing and execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from secrets import randbelow
from typing import ParamSpec, Protocol, TypeVar, cast, overload

from django.conf import settings
from django.db import transaction
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

_P = ParamSpec("_P")
_R = TypeVar("_R")
_R_co = TypeVar("_R_co", covariant=True)
_F = TypeVar("_F", bound=Callable[..., object])


type WorkflowTaskPayload = Mapping[str, object]
type WorkflowTaskHandler = Callable[[dict[str, object]], WorkflowTaskPayload | None]


class _TaskCallable(Protocol[_P, _R_co]):
    """Callable Celery task shape used by this module."""

    def __call__(self, *args: _P.args, **kwargs: _P.kwargs) -> _R_co: ...

    def delay(self, *args: _P.args, **kwargs: _P.kwargs) -> object: ...


class _SharedTask(Protocol):
    """Typed subset of Celery's shared_task decorator."""

    @overload
    def __call__(
        self, func: Callable[_P, _R], **kwargs: object
    ) -> _TaskCallable[_P, _R]: ...

    @overload
    def __call__(
        self, func: None = None, **kwargs: object
    ) -> Callable[[Callable[_P, _R]], _TaskCallable[_P, _R]]: ...


class _CeleryConf(Protocol):
    beat_schedule: object


class _CeleryApp(Protocol):
    conf: _CeleryConf


try:
    from celery import current_app as _celery_current_app
    from celery import shared_task as _celery_shared_task

    CELERY_AVAILABLE = True
    current_app: _CeleryApp | None = cast(_CeleryApp, _celery_current_app)
    shared_task: _SharedTask = cast(_SharedTask, _celery_shared_task)
except ImportError:  # pragma: no cover - optional dependency boundary
    CELERY_AVAILABLE = False
    current_app = None

    def _fallback_shared_task(
        func: _F | None = None, **_kwargs: object
    ) -> _F | Callable[[_F], _F]:
        def decorator(inner: _F) -> _F:
            return inner

        if func is None:
            return decorator
        return decorator(func)

    shared_task = cast(_SharedTask, _fallback_shared_task)


WORKFLOW_BEAT_SCHEDULE_KEY = "general_manager.workflow.publish_outbox_batch"


def configure_workflow_beat_schedule_from_settings(
    django_settings: object = settings,
) -> bool:
    """
    Register the workflow outbox periodic drain schedule in Celery Beat.

    `WORKFLOW_BEAT_ENABLED` controls whether scheduling is attempted.
    `WORKFLOW_BEAT_OUTBOX_INTERVAL_SECONDS` sets the base interval in seconds
    and defaults to `5`; `WORKFLOW_BEAT_MAX_JITTER_SECONDS` defaults to `2` and
    adds a random fractional-second delay up to that value. The schedule entry is
    written to Celery's process-local `current_app.conf.beat_schedule` under
    `WORKFLOW_BEAT_SCHEDULE_KEY`.

    Returns `False` when workflow beat is disabled, Celery is not installed, or
    Celery imported but `current_app` is `None`.
    When enabled, the function replaces/updates the process-local Celery
    `beat_schedule` entry for `publish_outbox_batch` and returns `True`. A
    `False` return leaves any existing schedule entry untouched.

    The generated Beat entry has task name
    `"general_manager.workflow.tasks.publish_outbox_batch"`, numeric
    `schedule` seconds, no args/kwargs, and options
    `{"queue": "workflow.events"}`. Jitter is sampled once when this function
    configures the entry, not on each Beat tick.

    Celery task-layer retry behavior is not configured here; exceptions from
    Celery app access or schedule assignment propagate.
    """
    if not workflow_beat_enabled(django_settings):
        return False
    if not CELERY_AVAILABLE or current_app is None:
        logger.warning("workflow beat schedule skipped; celery unavailable")
        return False
    raw_schedule = getattr(current_app.conf, "beat_schedule", {}) or {}
    schedule: dict[str, object] = (
        dict(cast(Mapping[str, object], raw_schedule))
        if isinstance(raw_schedule, Mapping)
        else {}
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
    """
    Claim available workflow outbox entries and dispatch one batch route task.

    Returns:
        int: Number of outbox rows claimed for routing. Returns `0` when the
        active event registry is not a `DatabaseEventRegistry`.

    Side effects:
        Updates workflow outbox telemetry after claiming. When Celery is
        available, claimed rows are dispatched to `route_outbox_claims_batch` via
        `.delay(...)`; otherwise they are routed inline.

    `WORKFLOW_OUTBOX_PROCESS_CHUNK_SIZE` controls the claim batch size.
    `DatabaseEventRegistry.claim_outbox_batch()` defines claim ordering and claim
    TTL behavior. Empty claim batches still refresh outbox snapshot telemetry
    and do not enqueue a route task. Exceptions from claiming, telemetry, inline
    routing, or Celery `.delay(...)` propagate to the caller/Celery worker.
    """
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
    """
    Route one workflow outbox row through the active database event registry.

    Parameters:
        outbox_id: Primary key of the outbox row to process.
        claim_token: Optional ownership token from `claim_outbox_batch()`.

    Returns:
        bool: `True` when processing completed at least one route and marked the
        row processed. Returns `False` when the active registry is not a
        `DatabaseEventRegistry` or the row was not processed.

    Missing rows, already processed rows, claim-token mismatches, claimed rows
    without a matching token, duplicate in-progress delivery attempts, rows with
    no applicable handlers, and route failures return `False` according to
    `DatabaseEventRegistry.process_outbox_entry()`. This single-row task does
    not catch exceptions raised by the registry.
    """
    registry = get_event_registry()
    if not isinstance(registry, DatabaseEventRegistry):
        return False
    return bool(registry.process_outbox_entry(outbox_id, claim_token=claim_token))


@shared_task(queue="workflow.events")
def route_outbox_claims_batch(claims: list[tuple[int, str]]) -> int:
    """
    Route a claimed outbox batch with per-entry failure isolation.

    Parameters:
        claims: `(outbox_id, claim_token)` pairs returned by
            `DatabaseEventRegistry.claim_outbox_batch()`.

    Returns:
        int: Count of rows whose route processing returned `True`.

    Side effects:
        Exceptions from individual rows are logged and do not stop later rows in
        the same batch.

    The public contract expects a concrete list of `(int, str)` pairs. Malformed
    claim entries raise normal Python unpacking/type errors inside that entry,
    are logged, and are then skipped by the batch loop. Duplicate claim entries
    are processed in list order and rely on per-row registry ownership checks.
    """
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


def _resolve_handler(handler_path: str) -> object:
    """Resolve an import path to a workflow execution handler object."""
    return import_string(handler_path)


@shared_task(queue="workflow.executions")
def execute_workflow_handler(
    execution_id: str,
    handler_path: str,
    input_data: WorkflowTaskPayload | None = None,
) -> None:
    """
    Run a persisted workflow execution handler and store its terminal state.

    Parameters:
        execution_id: Durable workflow execution id.
        handler_path: Dotted import path to the callable handler.
        input_data: Optional handler payload. It is copied into a mutable dict
            before the handler is called.

    Raises:
        WorkflowExecutionNotFoundError: If `execution_id` does not exist.

    Side effects:
        Pending executions move to `running`, then `completed` with handler
        output. Executions no longer in `pending` are left unchanged. Handler
        import errors and handler exceptions are captured on the execution record
        as a failed state.

    The handler is a synchronous callable accepting one `dict[str, object]`
    payload and returning a mapping, `None`, or another falsey value. `None`
    input becomes `{}`. Successful output is stored with `dict(result or {})`,
    so non-mapping truthy outputs fail the execution. Missing execution ids
    raise `WorkflowExecutionNotFoundError`. The task returns `None` for skipped,
    completed, and failed execution paths; callers inspect the execution record
    to distinguish those outcomes.

    Row locking and conditional updates prevent completed/failed/cancelled state
    from being overwritten by concurrent workers. This task does not configure
    Celery retries; `WorkflowExecutionNotFoundError` propagates, while handler
    import/call/output conversion failures are captured as failed executions.
    """
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
        handler_candidate = _resolve_handler(handler_path)
        if callable(handler_candidate):
            handler = cast(WorkflowTaskHandler, handler_candidate)
            result = handler(dict(input_data or {}))
        else:
            result = {}
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
    signal: WorkflowTaskPayload | None = None,
) -> bool:
    """
    Mark a waiting persisted execution as completed.

    Parameters:
        execution_id: Durable workflow execution id.
        signal: Optional resume signal stored in execution metadata.

    Returns:
        bool: `True` when a waiting execution was completed, `False` when the
        execution exists but is not in `waiting`.

    Raises:
        WorkflowExecutionNotFoundError: If `execution_id` does not exist.

    The resume `signal` is stored only in execution metadata under
    `resume_signal`; output data is not changed. This task does not configure
    Celery retries, so unexpected persistence errors propagate to the
    caller/worker.
    """
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
        if signal is not None:
            metadata["resume_signal"] = dict(signal)
        execution.metadata = metadata
        execution.state = "completed"
        execution.ended_at = datetime.now(UTC)
        execution.save(update_fields=["metadata", "state", "ended_at", "updated_at"])
    increment_execution_state("completed")
    return True


@shared_task(queue="workflow.executions")
def cancel_execution_task(execution_id: str, reason: str | None = None) -> bool:
    """
    Cancel an active persisted workflow execution.

    Parameters:
        execution_id: Durable workflow execution id.
        reason: Optional cancellation reason stored as the execution error.

    Returns:
        bool: `True` when an active execution was cancelled, `False` when the
        execution exists but is already terminal.

    Raises:
        WorkflowExecutionNotFoundError: If `execution_id` does not exist.

    Active states are `pending`, `running`, and `waiting`. The optional `reason`
    is stored in the execution `error` field. This task does not configure
    Celery retries, so unexpected persistence errors propagate to the
    caller/worker.
    """
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
