"""Database state machine for opt-in versioned workflow steps."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from json import dumps, loads
from typing import cast
from uuid import uuid4

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string

from general_manager.workflow.engine import (
    ACTIVE_WORKFLOW_STATES,
    WorkflowDefinition,
    WorkflowInvalidStateError,
    WorkflowPayload,
    WorkflowState,
    WorkflowSignalError,
    WorkflowStepConfigurationError,
    WorkflowStepContext,
)
from general_manager.workflow.models import WorkflowExecutionRecord, WorkflowStepRecord


@dataclass(frozen=True)
class StepClaim:
    """A lease and the persisted payload for one specific attempt."""

    step_id: int
    token: str
    handler_path: str
    context: WorkflowStepContext


def validate_definition(workflow: WorkflowDefinition) -> None:
    """Check the pinned definition before persisting a new plan."""
    if (
        workflow.handler is not None
        or workflow.metadata.get("handler_path") is not None
    ):
        raise WorkflowStepConfigurationError("mixed")
    if not workflow.version.strip() or len(workflow.version) > 128:
        raise WorkflowStepConfigurationError("version")
    keys: set[str] = set()
    for step in workflow.steps:
        if not step.key.strip() or len(step.key) > 128 or step.key in keys:
            raise WorkflowStepConfigurationError("keys")
        keys.add(step.key)
        if not 1 <= step.max_attempts <= 100:
            raise WorkflowStepConfigurationError("attempts")
        if (
            not 1 <= step.claim_seconds <= 86_400
            or not 0 <= step.retry_seconds <= 86_400
        ):
            raise WorkflowStepConfigurationError("timing")
        if not step.handler_path or len(step.handler_path) > 512:
            raise WorkflowStepConfigurationError("handler")
        try:
            candidate = import_string(step.handler_path)
        except ImportError as exc:
            raise WorkflowStepConfigurationError(
                "handler", path=step.handler_path
            ) from exc
        if not callable(candidate):
            raise WorkflowStepConfigurationError("handler", path=step.handler_path)


def create_steps(record: WorkflowExecutionRecord, workflow: WorkflowDefinition) -> None:
    """Insert one immutable declaration per step with only the first step active."""
    WorkflowStepRecord.objects.bulk_create(
        [
            WorkflowStepRecord(
                execution=record,
                position=position,
                key=step.key,
                handler_path=step.handler_path,
                wait_for_signal=step.wait_for_signal,
                max_attempts=step.max_attempts,
                claim_seconds=step.claim_seconds,
                retry_seconds=step.retry_seconds,
                state=record.state if position == 0 else "blocked",
                input_data=record.input_data if position == 0 else {},
                available_at=timezone.now()
                if position == 0 and record.state == "pending"
                else None,
            )
            for position, step in enumerate(workflow.steps)
        ]
    )
    if record.state == "pending":
        first = WorkflowStepRecord.objects.get(execution=record, position=0)
        _kick_after_commit(first.pk)


def _kick_after_commit(step_id: int) -> None:
    from general_manager.workflow.tasks import dispatch_workflow_step

    transaction.on_commit(lambda: dispatch_workflow_step(step_id), robust=True)


def _contains_null_character(value: object) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, list):
        return any(_contains_null_character(item) for item in value)
    if isinstance(value, dict):
        return any(
            _contains_null_character(key) or _contains_null_character(item)
            for key, item in value.items()
        )
    return False


def resume_steps(
    record: WorkflowExecutionRecord, signal: WorkflowPayload | None
) -> None:
    """Accept a named, idempotent approval under the locked execution row."""
    key = (signal or {}).get("step")
    signal_id = (signal or {}).get("id")
    payload = (signal or {}).get("data", {})
    if (
        not isinstance(key, str)
        or not key.strip()
        or "\x00" in key
        or not isinstance(signal_id, str)
        or not signal_id.strip()
        or "\x00" in signal_id
        or len(signal_id) > 128
        or not isinstance(payload, Mapping)
        or not all(isinstance(k, str) for k in payload)
    ):
        raise WorkflowSignalError()
    try:
        canonical_payload = dumps(dict(payload), sort_keys=True, allow_nan=False)
        if _contains_null_character(loads(canonical_payload)):
            raise WorkflowSignalError()
    except (TypeError, ValueError, RecursionError) as exc:
        raise WorkflowSignalError() from exc
    # JSONB can normalize numbers when reading them back. Compare the accepted
    # representation rather than re-serializing the database-decoded value.
    payload_digest = sha256(canonical_payload.encode()).hexdigest()
    step = WorkflowStepRecord.objects.filter(execution=record, key=key).first()
    if step is not None and step.signal_id == signal_id:
        if step.signal_digest != payload_digest:
            raise WorkflowSignalError(conflict=True)
        return
    if record.state != "waiting" or step is None or step.state != "waiting":
        raise WorkflowInvalidStateError(
            record.execution_id,
            operation="resume durable step",
            state=cast(WorkflowState, record.state),
            expected_states=("waiting",),
        )
    now = timezone.now()
    step.signal_id = signal_id
    step.signal_data = dict(payload)
    step.signal_digest = payload_digest
    step.signalled_at = now
    step.state = "pending"
    step.available_at = now
    step.save(
        update_fields=[
            "signal_id",
            "signal_data",
            "signal_digest",
            "signalled_at",
            "state",
            "available_at",
        ]
    )
    record.state = "pending"
    record.save(update_fields=["state", "updated_at"])
    _kick_after_commit(step.pk)


def cancel_steps(record: WorkflowExecutionRecord) -> None:
    """Close pending and future work, fencing any still-running attempt."""
    WorkflowStepRecord.objects.filter(
        execution=record,
        state__in=("blocked", "pending", "running", "waiting"),
    ).update(
        state="cancelled",
        claim_token=None,
        claim_until=None,
        finished_at=timezone.now(),
    )


def _due() -> Q:
    now = timezone.now()
    return (
        Q(state="pending", available_at__lte=now)
        | Q(state="running", claim_until__lte=now)
    ) & (Q(dispatch_until__isnull=True) | Q(dispatch_until__lte=now))


def due_step_ids(batch_size: int = 100) -> list[int]:
    """Bounded list of work that Beat can send or resend."""
    return list(
        WorkflowStepRecord.objects.filter(_due())
        .order_by("pk")
        .values_list("pk", flat=True)[:batch_size]
    )


def reserve_dispatch(step_id: int) -> bool:
    """Throttle duplicate broker deliveries without losing rows when publish fails."""
    return bool(
        WorkflowStepRecord.objects.filter(_due(), pk=step_id).update(
            dispatch_until=timezone.now() + timedelta(seconds=15)
        )
    )


def claim_step(step_id: int) -> StepClaim | None:
    """Claim or reclaim a due step without holding a transaction during its action."""
    execution_pk = (
        WorkflowStepRecord.objects.filter(pk=step_id)
        .values_list("execution_id", flat=True)
        .first()
    )
    if execution_pk is None:
        return None
    with transaction.atomic():
        run = (
            WorkflowExecutionRecord.objects.select_for_update()
            .filter(pk=execution_pk)
            .first()
        )
        if (
            run is None
            or run.state not in ACTIVE_WORKFLOW_STATES
            or run.durable_version is None
        ):
            return None
        step = WorkflowStepRecord.objects.get(pk=step_id, execution=run)
        now = timezone.now()
        if not (
            (step.state == "pending" and step.available_at and step.available_at <= now)
            or (
                step.state == "running" and step.claim_until and step.claim_until <= now
            )
        ):
            return None
        if step.attempts >= step.max_attempts:
            _fail_terminal(
                run, step, "Step worker lease expired and attempts are exhausted."
            )
            return None
        token = str(uuid4())
        step.state = "running"
        step.attempts += 1
        step.first_claimed_at = step.first_claimed_at or now
        step.claim_token = token
        step.claim_until = now + timedelta(seconds=step.claim_seconds)
        step.dispatch_until = None
        step.save(
            update_fields=[
                "state",
                "attempts",
                "first_claimed_at",
                "claim_token",
                "claim_until",
                "dispatch_until",
            ]
        )
        run.state = "running"
        run.save(update_fields=["state", "updated_at"])
        context = WorkflowStepContext(
            execution_id=run.execution_id,
            workflow_id=run.workflow_id,
            version=run.durable_version,
            step=step.key,
            idempotency_key=f"{run.execution_id}:{step.key}",
            input_data=deepcopy(dict(step.input_data)),
            signal=None
            if step.signal_data is None
            else deepcopy(dict(step.signal_data)),
        )
        return StepClaim(step.pk, token, step.handler_path, context)


def _fail_terminal(
    run: WorkflowExecutionRecord, step: WorkflowStepRecord, message: str
) -> None:
    now = timezone.now()
    step.state = "failed"
    step.finished_at = now
    step.claim_token = None
    step.claim_until = None
    step.last_error = message
    step.save(
        update_fields=[
            "state",
            "finished_at",
            "claim_token",
            "claim_until",
            "last_error",
        ]
    )
    run.state = "failed"
    run.error = message
    run.ended_at = now
    run.save(update_fields=["state", "error", "ended_at", "updated_at"])


def complete_step(
    claim: StepClaim, output: WorkflowPayload | None = None, *, failed: bool = False
) -> bool:
    """Checkpoint a result, retry, or transition only for the current lease holder."""
    with transaction.atomic():
        run = (
            WorkflowExecutionRecord.objects.select_for_update()
            .filter(execution_id=claim.context.execution_id)
            .first()
        )
        if run is None or run.state != "running":
            return False
        step = WorkflowStepRecord.objects.filter(
            pk=claim.step_id, execution=run, state="running", claim_token=claim.token
        ).first()
        if step is None:
            return False
        now = timezone.now()
        if failed:
            message = "Step handler failed."
            if step.attempts >= step.max_attempts:
                _fail_terminal(run, step, message)
                return True
            step.state = "pending"
            step.available_at = now + timedelta(seconds=step.retry_seconds)
            step.claim_token = None
            step.claim_until = None
            step.last_error = message
            step.save(
                update_fields=[
                    "state",
                    "available_at",
                    "claim_token",
                    "claim_until",
                    "last_error",
                ]
            )
            run.state = "pending"
            run.save(update_fields=["state", "updated_at"])
            return True

        result = {} if output is None else dict(output)
        step.state = "completed"
        step.output_data = result
        step.finished_at = now
        step.claim_token = None
        step.claim_until = None
        step.save(
            update_fields=[
                "state",
                "output_data",
                "finished_at",
                "claim_token",
                "claim_until",
            ]
        )
        next_step = WorkflowStepRecord.objects.filter(
            execution=run, position=step.position + 1
        ).first()
        if next_step is None:
            run.state = "completed"
            run.output_data = result
            run.ended_at = now
            run.save(update_fields=["state", "output_data", "ended_at", "updated_at"])
        else:
            next_step.input_data = result
            next_step.state = "waiting" if next_step.wait_for_signal else "pending"
            next_step.available_at = None if next_step.wait_for_signal else now
            next_step.save(update_fields=["input_data", "state", "available_at"])
            run.state = next_step.state
            run.save(update_fields=["state", "updated_at"])
            if next_step.state == "pending":
                _kick_after_commit(next_step.pk)
        return True
