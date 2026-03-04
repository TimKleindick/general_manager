from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings

from general_manager.workflow.backends.celery import CeleryWorkflowEngine
from general_manager.workflow.engine import (
    WorkflowDefinition,
    WorkflowExecutionNotFoundError,
)
from general_manager.workflow.event_registry import (
    DatabaseEventRegistry,
    InMemoryEventRegistry,
    WorkflowEvent,
    configure_event_registry,
    configure_event_registry_from_settings,
    get_event_registry,
)
from general_manager.workflow.models import (
    WorkflowDeliveryAttempt,
    WorkflowEventRecord,
    WorkflowExecutionRecord,
    WorkflowOutbox,
)
from general_manager.workflow.tasks import (
    execute_workflow_handler,
    resume_execution_task,
)


def _handler(payload: dict[str, object]) -> dict[str, object]:
    return {"seen": payload.get("value")}


class WorkflowProductionRegistryTests(TestCase):
    def tearDown(self) -> None:
        configure_event_registry(InMemoryEventRegistry())
        super().tearDown()

    @override_settings(GENERAL_MANAGER={"WORKFLOW_MODE": "production"})
    def test_event_registry_defaults_to_database_in_production_mode(self) -> None:
        configure_event_registry_from_settings(settings)
        assert isinstance(get_event_registry(), DatabaseEventRegistry)

    @override_settings(
        GENERAL_MANAGER={"WORKFLOW_MODE": "production", "WORKFLOW_ASYNC": False}
    )
    def test_database_registry_persists_and_routes_event_sync(self) -> None:
        handled: list[str] = []
        registry = DatabaseEventRegistry()
        registry.register(
            "invoice.created", handler=lambda event: handled.append(event.event_id)
        )

        event = WorkflowEvent(
            event_id="evt-prod-1",
            event_type="invoice.created",
            payload={"invoice_id": 1},
        )
        assert registry.publish(event) is True
        assert handled == ["evt-prod-1"]
        assert WorkflowEventRecord.objects.filter(event_id="evt-prod-1").exists()
        assert WorkflowOutbox.objects.filter(
            event__event_id="evt-prod-1", status=WorkflowOutbox.STATUS_PROCESSED
        ).exists()
        assert WorkflowDeliveryAttempt.objects.filter(
            idempotency_key__startswith="evt-prod-1:"
        ).exists()

    @override_settings(
        GENERAL_MANAGER={"WORKFLOW_MODE": "production", "WORKFLOW_ASYNC": False}
    )
    def test_database_registry_deduplicates_by_event_id(self) -> None:
        calls: list[str] = []
        registry = DatabaseEventRegistry()
        registry.register(
            "invoice.created", handler=lambda event: calls.append(event.event_id)
        )
        event = WorkflowEvent(
            event_id="evt-prod-2",
            event_type="invoice.created",
            payload={"invoice_id": 2},
        )
        assert registry.publish(event) is True
        assert registry.publish(event) is False
        assert calls == ["evt-prod-2"]

    @override_settings(
        GENERAL_MANAGER={"WORKFLOW_MODE": "production", "WORKFLOW_ASYNC": True}
    )
    def test_process_outbox_entry_requires_matching_claim_token(self) -> None:
        handled: list[str] = []
        registry = DatabaseEventRegistry()
        registry.register(
            "invoice.created", handler=lambda event: handled.append(event.event_id)
        )
        event = WorkflowEvent(
            event_id="evt-claim-token",
            event_type="invoice.created",
            payload={"invoice_id": 3},
        )
        assert registry.publish(event) is False
        claims = registry.claim_outbox_batch()
        assert len(claims) == 1
        outbox_id, claim_token = claims[0]
        invalid_claim_token = f"{claim_token}-stale"

        assert (
            registry.process_outbox_entry(outbox_id, claim_token=invalid_claim_token)
            is False
        )
        assert handled == []
        outbox = WorkflowOutbox.objects.get(id=outbox_id)
        assert outbox.status == WorkflowOutbox.STATUS_CLAIMED

        assert registry.process_outbox_entry(outbox_id, claim_token=claim_token) is True
        assert handled == ["evt-claim-token"]

    @override_settings(
        GENERAL_MANAGER={
            "WORKFLOW_MODE": "production",
            "WORKFLOW_ASYNC": True,
            "WORKFLOW_OUTBOX_CLAIM_TTL_SECONDS": 1,
        }
    )
    def test_stale_claim_token_cannot_process_after_reclaim(self) -> None:
        calls: list[str] = []
        registry = DatabaseEventRegistry()
        registry.register(
            "invoice.created", handler=lambda event: calls.append(event.event_id)
        )
        event = WorkflowEvent(
            event_id="evt-stale-claim",
            event_type="invoice.created",
            payload={"invoice_id": 4},
        )
        assert registry.publish(event) is False
        first_claim = registry.claim_outbox_batch()
        assert len(first_claim) == 1
        outbox_id, stale_token = first_claim[0]
        WorkflowOutbox.objects.filter(id=outbox_id).update(
            claimed_at=datetime.now(UTC) - timedelta(seconds=10)
        )
        second_claim = registry.claim_outbox_batch()
        assert len(second_claim) == 1
        reclaimed_id, fresh_token = second_claim[0]
        assert reclaimed_id == outbox_id
        assert stale_token != fresh_token

        assert (
            registry.process_outbox_entry(outbox_id, claim_token=stale_token) is False
        )
        assert registry.process_outbox_entry(outbox_id, claim_token=fresh_token) is True
        assert calls == ["evt-stale-claim"]

    @override_settings(
        GENERAL_MANAGER={
            "WORKFLOW_MODE": "production",
            "WORKFLOW_ASYNC": True,
            "WORKFLOW_MAX_RETRIES": 1,
            "WORKFLOW_DEAD_LETTER_ENABLED": True,
        }
    )
    def test_process_outbox_entry_not_handled_increments_attempts_and_dead_letters(
        self,
    ) -> None:
        registry = DatabaseEventRegistry()
        registry.register(
            "invoice.created",
            handler=lambda _event: None,
            when=lambda _event: False,
        )
        event = WorkflowEvent(
            event_id="evt-not-handled",
            event_type="invoice.created",
            payload={"invoice_id": 5},
        )
        assert registry.publish(event) is False
        outbox = WorkflowOutbox.objects.get(event__event_id="evt-not-handled")
        assert registry.process_outbox_entry(int(outbox.pk)) is False
        outbox.refresh_from_db()
        assert outbox.attempts == 1
        assert outbox.status == WorkflowOutbox.STATUS_DEAD_LETTER

    @override_settings(
        GENERAL_MANAGER={
            "WORKFLOW_MODE": "production",
            "WORKFLOW_ASYNC": True,
            "WORKFLOW_DELIVERY_RUNNING_TIMEOUT_SECONDS": 300,
        }
    )
    def test_process_outbox_entry_skips_when_attempt_is_already_running(self) -> None:
        calls: list[str] = []
        registry = DatabaseEventRegistry()
        registry.register(
            "invoice.created",
            handler=lambda event: calls.append(event.event_id),
        )
        event = WorkflowEvent(
            event_id="evt-running-attempt",
            event_type="invoice.created",
            payload={"invoice_id": 7},
        )
        registration_id = registry._get_entries(event)[0].registration_id
        assert registry.publish(event) is False
        outbox = WorkflowOutbox.objects.get(event__event_id="evt-running-attempt")
        claims = registry.claim_outbox_batch()
        assert len(claims) == 1
        _, claim_token = claims[0]
        attempt = WorkflowDeliveryAttempt.objects.create(
            event=WorkflowEventRecord.objects.get(event_id="evt-running-attempt"),
            handler_registration_id=registration_id,
            idempotency_key=f"evt-running-attempt:{registration_id}",
            status=WorkflowDeliveryAttempt.STATUS_RUNNING,
            attempts=1,
        )
        # Keep the running attempt fresh so duplicate execution is suppressed.
        WorkflowDeliveryAttempt.objects.filter(pk=attempt.pk).update(
            updated_at=datetime.now(UTC)
        )
        assert (
            registry.process_outbox_entry(int(outbox.pk), claim_token=claim_token)
            is False
        )
        assert calls == []
        outbox.refresh_from_db()
        assert outbox.status == WorkflowOutbox.STATUS_CLAIMED

    @override_settings(
        GENERAL_MANAGER={"WORKFLOW_MODE": "production", "WORKFLOW_ASYNC": False}
    )
    def test_registration_ids_include_routing_options_to_avoid_collisions(self) -> None:
        calls: list[str] = []
        registry = DatabaseEventRegistry()

        def handler(event: WorkflowEvent) -> None:
            calls.append(event.event_id)

        def when_one(_event: WorkflowEvent) -> bool:
            return True

        def when_two(_event: WorkflowEvent) -> bool:
            return True

        registry.register("invoice.created", handler=handler, when=when_one)
        registry.register("invoice.created", handler=handler, when=when_two)
        event = WorkflowEvent(
            event_id="evt-reg-id",
            event_type="invoice.created",
            payload={"invoice_id": 6},
        )
        assert registry.publish(event) is True
        assert calls == ["evt-reg-id", "evt-reg-id"]
        attempts = WorkflowDeliveryAttempt.objects.filter(event__event_id="evt-reg-id")
        assert attempts.count() == 2
        assert (
            attempts.values_list("handler_registration_id", flat=True)
            .distinct()
            .count()
            == 2
        )

    @override_settings(
        GENERAL_MANAGER={"WORKFLOW_MODE": "production", "WORKFLOW_ASYNC": True}
    )
    def test_claim_outbox_batch_respects_available_at_backoff(self) -> None:
        registry = DatabaseEventRegistry()
        event = WorkflowEvent(
            event_id="evt-backoff-window",
            event_type="invoice.created",
            payload={"invoice_id": 8},
        )
        assert registry.publish(event) is False
        outbox = WorkflowOutbox.objects.get(event__event_id="evt-backoff-window")
        WorkflowOutbox.objects.filter(pk=outbox.pk).update(
            status=WorkflowOutbox.STATUS_FAILED,
            available_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        assert registry.claim_outbox_batch() == []
        WorkflowOutbox.objects.filter(pk=outbox.pk).update(
            available_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        claims = registry.claim_outbox_batch()
        assert len(claims) == 1
        assert claims[0][0] == int(outbox.pk)


class WorkflowProductionEngineTests(TestCase):
    @override_settings(
        GENERAL_MANAGER={"WORKFLOW_MODE": "production", "WORKFLOW_ASYNC": False}
    )
    def test_celery_workflow_engine_persists_execution_and_runs_inline(self) -> None:
        engine = CeleryWorkflowEngine()
        workflow = WorkflowDefinition(workflow_id="wf-inline", handler=_handler)
        execution = engine.start(workflow, {"value": 42}, correlation_id="corr-1")
        loaded = engine.status(execution.execution_id)
        assert loaded.workflow_id == "wf-inline"
        assert loaded.state == "completed"
        assert loaded.output_data == {"seen": 42}
        assert WorkflowExecutionRecord.objects.filter(
            execution_id=execution.execution_id
        ).exists()

    @override_settings(
        GENERAL_MANAGER={"WORKFLOW_MODE": "production", "WORKFLOW_ASYNC": False}
    )
    def test_celery_workflow_engine_dedupes_completed_by_correlation_id(self) -> None:
        engine = CeleryWorkflowEngine()
        workflow = WorkflowDefinition(workflow_id="wf-dedupe", handler=_handler)
        first = engine.start(workflow, {"value": 1}, correlation_id="corr-2")
        second = engine.start(workflow, {"value": 2}, correlation_id="corr-2")
        assert first.execution_id == second.execution_id
        assert (
            WorkflowExecutionRecord.objects.filter(workflow_id="wf-dedupe").count() == 1
        )

    @override_settings(
        GENERAL_MANAGER={"WORKFLOW_MODE": "production", "WORKFLOW_ASYNC": True}
    )
    def test_celery_engine_fails_non_importable_handler_in_async_mode(self) -> None:
        engine = CeleryWorkflowEngine()

        def local_handler(payload: dict[str, object]) -> dict[str, object]:
            return {"seen": payload.get("value")}

        workflow = WorkflowDefinition(
            workflow_id="wf-local-handler", handler=local_handler
        )
        execution = engine.start(workflow, {"value": 11})
        loaded = engine.status(execution.execution_id)
        assert loaded.state == "failed"
        assert loaded.error is not None

    @override_settings(
        GENERAL_MANAGER={"WORKFLOW_MODE": "production", "WORKFLOW_ASYNC": True}
    )
    def test_celery_engine_fails_when_celery_unavailable_in_async_mode(self) -> None:
        engine = CeleryWorkflowEngine()
        workflow = WorkflowDefinition(
            workflow_id="wf-celery-required", handler=_handler
        )
        with patch("general_manager.workflow.backends.celery.CELERY_AVAILABLE", False):
            execution = engine.start(workflow, {"value": 99})
        loaded = engine.status(execution.execution_id)
        assert loaded.state == "failed"
        assert loaded.error is not None

    def test_execute_workflow_handler_keeps_cancelled_state(self) -> None:
        record = WorkflowExecutionRecord.objects.create(
            execution_id="exec-cancelled",
            workflow_id="wf-cancelled",
            state="cancelled",
            input_data={"value": 1},
            output_data=None,
            error="cancelled by user",
            metadata={},
        )
        execute_workflow_handler(
            execution_id="exec-cancelled",
            handler_path="tests.unit.test_workflow_production._handler",
            input_data={"value": 1},
        )
        record.refresh_from_db()
        assert record.state == "cancelled"
        assert record.error == "cancelled by user"
        assert record.output_data is None

    def test_resume_execution_task_does_not_override_cancelled_state(self) -> None:
        record = WorkflowExecutionRecord.objects.create(
            execution_id="exec-resume-cancelled",
            workflow_id="wf-resume-cancelled",
            state="cancelled",
            input_data={},
            output_data=None,
            error="manual cancel",
            metadata={"existing": True},
        )
        assert (
            resume_execution_task("exec-resume-cancelled", {"step": "retry"}) is False
        )
        record.refresh_from_db()
        assert record.state == "cancelled"
        assert record.metadata == {"existing": True}

    def test_celery_workflow_engine_status_missing_raises(self) -> None:
        engine = CeleryWorkflowEngine()
        try:
            engine.status("missing")
        except WorkflowExecutionNotFoundError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("Expected WorkflowExecutionNotFoundError")  # noqa: TRY003
