from __future__ import annotations

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

    def test_celery_workflow_engine_status_missing_raises(self) -> None:
        engine = CeleryWorkflowEngine()
        try:
            engine.status("missing")
        except WorkflowExecutionNotFoundError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("Expected WorkflowExecutionNotFoundError")  # noqa: TRY003
