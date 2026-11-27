"""Durable multi-step workflow contract at the production engine boundary."""

from datetime import timedelta

import pytest
from django.utils import timezone

from general_manager.workflow import (
    WorkflowDefinition,
    WorkflowInvalidStateError,
    WorkflowSignalError,
    WorkflowStep,
    WorkflowStepConfigurationError,
    WorkflowStepContext,
)
from general_manager.workflow.backends.celery import CeleryWorkflowEngine
from general_manager.workflow.durable import claim_step, complete_step, due_step_ids
from general_manager.workflow.models import WorkflowStepRecord
from general_manager.workflow.tasks import (
    cancel_execution_task,
    execute_workflow_step,
    publish_durable_steps,
)

pytestmark = pytest.mark.django_db(transaction=True)

HANDLER_PATH = f"{__name__}.record_approval"


def record_approval(context: WorkflowStepContext) -> dict[str, object]:
    return {
        "version": context.version,
        "signal": context.signal,
        "input": context.input_data,
        "key": context.idempotency_key,
    }


@pytest.fixture(autouse=True)
def use_durable_worker(monkeypatch):
    monkeypatch.setattr(
        "general_manager.workflow.backends.celery.workflow_async_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "general_manager.workflow.backends.celery.workflow_durable_steps_enabled",
        lambda: True,
    )
    monkeypatch.setattr(execute_workflow_step, "delay", lambda *_args: None)


def start_workflow():
    return CeleryWorkflowEngine().start(
        WorkflowDefinition(
            workflow_id="drawing-release",
            version="2026-11",
            steps=(WorkflowStep("approve", HANDLER_PATH, wait_for_signal=True),),
        ),
        {"drawing": "P1"},
    )


def step_id(execution_id):
    return WorkflowStepRecord.objects.get(execution__execution_id=execution_id).pk


def test_waiting_run_resumes_pinned_step_and_replays_same_signal():
    engine = CeleryWorkflowEngine()
    run = start_workflow()
    assert run.state == "waiting"
    assert due_step_ids() == []
    signal = {"step": "approve", "id": "decision-1", "data": {"approved": True}}
    resumed = engine.resume(run.execution_id, signal)
    assert resumed.state == "pending"
    execute_workflow_step(step_id(run.execution_id))
    result = engine.status(run.execution_id)
    assert result.state == "completed"
    assert result.output_data == {
        "version": "2026-11",
        "signal": {"approved": True},
        "input": {"drawing": "P1"},
        "key": f"{run.execution_id}:approve",
    }
    assert engine.resume(run.execution_id, signal).state == "completed"
    with pytest.raises(WorkflowInvalidStateError):
        engine.resume(run.execution_id, {**signal, "id": "decision-2"})


def test_expired_claim_is_recoverable_and_late_completion_is_fenced():
    engine = CeleryWorkflowEngine()
    run = start_workflow()
    engine.resume(run.execution_id, {"step": "approve", "id": "decision-1"})
    pk = step_id(run.execution_id)
    first = claim_step(pk)
    assert first is not None
    assert claim_step(pk) is None
    WorkflowStepRecord.objects.filter(pk=pk).update(
        claim_until=timezone.now() - timedelta(seconds=1)
    )
    assert due_step_ids() == [pk]
    second = claim_step(pk)
    assert second is not None
    assert first.context.idempotency_key == second.context.idempotency_key
    assert first.token != second.token
    assert not complete_step(first, {"stale": True})
    assert complete_step(second, {"restored": True})
    assert engine.status(run.execution_id).output_data == {"restored": True}


def test_cancel_fences_an_active_claim():
    engine = CeleryWorkflowEngine()
    run = start_workflow()
    engine.resume(run.execution_id, {"step": "approve", "id": "decision-1"})
    pk = step_id(run.execution_id)
    claim = claim_step(pk)
    assert claim is not None
    assert engine.cancel(run.execution_id).state == "cancelled"
    assert not complete_step(claim, {"too_late": True})
    assert claim_step(pk) is None
    assert engine.status(run.execution_id).output_data is None


def test_celery_task_cancellation_does_not_starve_later_work():
    engine = CeleryWorkflowEngine()
    signal = {"step": "approve", "id": "decision-1"}
    cancelled = start_workflow()
    engine.resume(cancelled.execution_id, signal)
    cancelled_pk = step_id(cancelled.execution_id)
    claim = claim_step(cancelled_pk)
    assert claim is not None
    assert cancel_execution_task(cancelled.execution_id, "no longer needed")
    assert not cancel_execution_task(cancelled.execution_id)
    assert not complete_step(claim, {"too_late": True})
    later = start_workflow()
    engine.resume(later.execution_id, signal)
    later_pk = step_id(later.execution_id)
    expired = timezone.now() - timedelta(seconds=1)
    WorkflowStepRecord.objects.filter(pk__in=(cancelled_pk, later_pk)).update(
        claim_until=expired, dispatch_until=expired
    )
    assert due_step_ids(batch_size=1) == [later_pk]
    assert WorkflowStepRecord.objects.get(pk=cancelled_pk).state == "cancelled"


def test_postgres_signal_replay_preserves_submitted_number():
    from django.db import connection

    if connection.vendor != "postgresql":
        pytest.skip("requires PostgreSQL JSONB normalization")
    engine = CeleryWorkflowEngine()
    run = start_workflow()
    signal = {"step": "approve", "id": "one", "data": {"amount": 1e20}}
    assert engine.resume(run.execution_id, signal).state == "pending"
    assert engine.resume(run.execution_id, signal).state == "pending"


@pytest.mark.parametrize(
    "payload", [{"nested": [{"value": "\x00"}]}, {"nested": {"\x00": True}}]
)
def test_signal_rejects_nested_null_characters_without_losing_wait(payload):
    engine = CeleryWorkflowEngine()
    run = start_workflow()
    signal = {"step": "approve", "id": "one", "data": payload}
    with pytest.raises(WorkflowSignalError):
        engine.resume(run.execution_id, signal)
    assert engine.status(run.execution_id).state == "waiting"
    assert engine.resume(run.execution_id, {**signal, "data": {}}).state == "pending"


def test_two_versions_keep_their_own_handler_and_resume_uses_a_single_decision():
    engine = CeleryWorkflowEngine()
    old = start_workflow()

    def replacement(_context):
        return {"new": True}

    # Simulate deploying a new definition at a new import path while v1 waits.
    globals()["new_approval_handler"] = replacement
    try:
        new = engine.start(
            WorkflowDefinition(
                workflow_id="drawing-release",
                version="2026-12",
                steps=(
                    WorkflowStep("approve", f"{__name__}.new_approval_handler", True),
                ),
            )
        )
        assert new.version == "2026-12"
        signal = {"step": "approve", "id": "one", "data": {"approved": True}}
        engine.resume(old.execution_id, signal)
        with pytest.raises(WorkflowSignalError):
            engine.resume(old.execution_id, {**signal, "data": {"approved": 1}})
        execute_workflow_step(step_id(old.execution_id))
        assert engine.status(old.execution_id).version == "2026-11"
        assert engine.status(old.execution_id).output_data["version"] == "2026-11"
        engine.resume(new.execution_id, signal)
        execute_workflow_step(step_id(new.execution_id))
        assert engine.status(new.execution_id).output_data == {"new": True}
    finally:
        del globals()["new_approval_handler"]


def test_handler_output_advances_to_next_step():
    engine = CeleryWorkflowEngine()
    run = engine.start(
        WorkflowDefinition(
            workflow_id="drawing-release",
            steps=(
                WorkflowStep("prepare", HANDLER_PATH),
                WorkflowStep("review", HANDLER_PATH, wait_for_signal=True),
            ),
        ),
        {"drawing": "A42"},
    )
    steps = list(
        WorkflowStepRecord.objects.filter(
            execution__execution_id=run.execution_id
        ).order_by("position")
    )
    execute_workflow_step(steps[0].pk)
    assert engine.status(run.execution_id).state == "waiting"
    engine.resume(run.execution_id, {"step": "review", "id": "one"})
    execute_workflow_step(steps[1].pk)
    first, second = [WorkflowStepRecord.objects.get(pk=step.pk) for step in steps]
    assert second.output_data["input"] == first.output_data
    assert engine.status(run.execution_id).state == "completed"


def test_postgres_concurrent_different_decisions_commit_only_once():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connection, connections

    if connection.vendor != "postgresql":
        pytest.skip("requires real row locks")
    run = start_workflow()
    barrier = Barrier(2)

    def resume(signal_id):
        try:
            barrier.wait(timeout=10)
            CeleryWorkflowEngine().resume(
                run.execution_id, {"step": "approve", "id": signal_id}
            )
        except WorkflowInvalidStateError:
            return False
        finally:
            connections.close_all()
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(resume, ("approved", "rejected")))
    assert sorted(outcomes) == [False, True]
    record = WorkflowStepRecord.objects.get(pk=step_id(run.execution_id))
    assert record.signal_id in ("approved", "rejected")
    execute_workflow_step(record.pk)
    assert CeleryWorkflowEngine().status(run.execution_id).state == "completed"


def test_failed_attempt_retries_with_same_key_but_never_exceeds_limit():
    engine = CeleryWorkflowEngine()
    run = engine.start(
        WorkflowDefinition(
            workflow_id="retry-example",
            steps=(
                WorkflowStep("work", HANDLER_PATH, max_attempts=2, retry_seconds=0),
            ),
        ),
    )
    pk = step_id(run.execution_id)
    first = claim_step(pk)
    assert first is not None and complete_step(first, failed=True)
    assert engine.status(run.execution_id).state == "pending"
    second = claim_step(pk)
    assert second is not None and complete_step(second, failed=True)
    assert first.context.idempotency_key == second.context.idempotency_key
    assert engine.status(run.execution_id).state == "failed"
    assert claim_step(pk) is None
    assert due_step_ids() == []


def test_start_transaction_rollback_leaves_no_durable_dispatch(monkeypatch):
    from unittest.mock import Mock

    from django.db import transaction

    from general_manager.workflow.models import WorkflowExecutionRecord

    dispatch = Mock()
    monkeypatch.setattr(execute_workflow_step, "delay", dispatch)
    with transaction.atomic():
        run = CeleryWorkflowEngine().start(
            WorkflowDefinition(
                "rolled-back", steps=(WorkflowStep("prepare", HANDLER_PATH),)
            )
        )
        assert run.state == "pending"
        dispatch.assert_not_called()
        transaction.set_rollback(True)
    dispatch.assert_not_called()
    assert not WorkflowExecutionRecord.objects.filter(
        execution_id=run.execution_id
    ).exists()
    assert due_step_ids() == []


def test_failed_broker_publish_is_recovered_after_dispatch_window(monkeypatch):
    published: list[int] = []

    def unavailable(_step_id):
        raise ConnectionError

    engine = CeleryWorkflowEngine()
    monkeypatch.setattr(execute_workflow_step, "delay", unavailable)
    run = engine.start(
        WorkflowDefinition("broker-outage", steps=(WorkflowStep("work", HANDLER_PATH),))
    )
    pk = step_id(run.execution_id)
    assert run.state == "pending"
    assert WorkflowStepRecord.objects.get(pk=pk).attempts == 0
    assert publish_durable_steps() == 0

    monkeypatch.setattr(execute_workflow_step, "delay", published.append)
    WorkflowStepRecord.objects.filter(pk=pk).update(
        dispatch_until=timezone.now() - timedelta(seconds=1)
    )
    assert publish_durable_steps() == 1
    assert published == [pk]
    assert publish_durable_steps() == 0
    execute_workflow_step(pk)
    execute_workflow_step(pk)  # Duplicate broker delivery is a no-op.
    assert engine.status(run.execution_id).state == "completed"
    assert WorkflowStepRecord.objects.get(pk=pk).attempts == 1


def test_step_plan_requires_opt_in_celery_and_valid_handler(monkeypatch):
    from general_manager.workflow.backends.local import LocalWorkflowEngine

    definition = WorkflowDefinition(
        "invalid-definition", steps=(WorkflowStep("work", "tests.missing.handler"),)
    )
    with pytest.raises(WorkflowStepConfigurationError, match="require Celery async"):
        LocalWorkflowEngine().start(definition)

    engine = CeleryWorkflowEngine()
    with pytest.raises(WorkflowStepConfigurationError, match="importable, callable"):
        engine.start(definition)

    monkeypatch.setattr(
        "general_manager.workflow.backends.celery.workflow_durable_steps_enabled",
        lambda: False,
    )
    with pytest.raises(WorkflowStepConfigurationError, match="require Celery async"):
        start_workflow()
    assert not WorkflowStepRecord.objects.exists()
