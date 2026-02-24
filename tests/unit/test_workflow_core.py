from __future__ import annotations

import pytest
from django.test import SimpleTestCase

from general_manager.workflow.actions import (
    ActionExecutionError,
    ActionNotFoundError,
    ActionRegistry,
)
from general_manager.workflow.backends.local import LocalWorkflowEngine
from general_manager.workflow.engine import (
    WorkflowDefinition,
    WorkflowExecutionNotFoundError,
)
from general_manager.workflow.event_registry import InMemoryEventRegistry, WorkflowEvent
from general_manager.workflow.events import manager_updated_event


class _EchoAction:
    def execute(
        self, context: dict[str, object], params: dict[str, object]
    ) -> dict[str, object]:
        return {"context": context, "params": params}


class _FailingAction:
    def execute(self, _context: dict[str, object], _params: dict[str, object]) -> None:
        raise RuntimeError("boom")


class WorkflowCoreTests(SimpleTestCase):
    def test_local_engine_start_and_status(self) -> None:
        engine = LocalWorkflowEngine()
        definition = WorkflowDefinition(workflow_id="sync_project")
        execution = engine.start(definition, {"project_id": 1})
        loaded = engine.status(execution.execution_id)
        assert loaded.state == "completed"
        assert loaded.workflow_id == "sync_project"

    def test_local_engine_status_missing_execution_raises(self) -> None:
        engine = LocalWorkflowEngine()
        with pytest.raises(WorkflowExecutionNotFoundError):
            engine.status("missing")

    def test_action_registry_executes_named_action(self) -> None:
        registry = ActionRegistry()
        registry.register("echo", _EchoAction())
        result = registry.execute("echo", context={"user": "42"}, params={"x": 1})
        assert result == {"context": {"user": "42"}, "params": {"x": 1}}

    def test_action_registry_raises_for_missing_action(self) -> None:
        registry = ActionRegistry()
        with pytest.raises(ActionNotFoundError):
            registry.execute("missing")

    def test_action_registry_wraps_execution_errors(self) -> None:
        registry = ActionRegistry()
        registry.register("fail", _FailingAction())
        with pytest.raises(ActionExecutionError):
            registry.execute("fail")

    def test_event_registry_deduplicates_event_ids(self) -> None:
        events: list[str] = []
        registry = InMemoryEventRegistry()
        registry.register(
            "invoice.created", handler=lambda event: events.append(event.event_id)
        )

        event = WorkflowEvent(
            event_id="evt-1",
            event_type="invoice.created",
            payload={"invoice_id": 123},
        )
        first = registry.publish(event)
        second = registry.publish(event)

        assert first is True
        assert second is False
        assert events == ["evt-1"]

    def test_event_registry_register_by_name_with_when_filter(self) -> None:
        handled: list[str] = []
        registry = InMemoryEventRegistry()
        registry.register(
            "project_status_changed",
            handler=lambda event: handled.append(event.event_id),
            when=lambda event: event.payload["changes"]["status"]["new"] == "active",
        )

        inactive = manager_updated_event(
            manager="Project",
            changes={"status": "draft"},
            old_values={"status": "queued"},
            event_name="project_status_changed",
            event_id="evt-inactive",
        )
        active = manager_updated_event(
            manager="Project",
            changes={"status": "active"},
            old_values={"status": "draft"},
            event_name="project_status_changed",
            event_id="evt-active",
        )

        assert registry.publish(inactive) is False
        assert registry.publish(active) is True
        assert handled == ["evt-active"]

    def test_manager_updated_event_contains_old_and_new_values(self) -> None:
        event = manager_updated_event(
            manager="Project",
            identification={"id": 7},
            changes={"status": "active"},
            old_values={"status": "draft"},
            event_name="project_status_changed",
            event_id="evt-1",
        )
        assert event.event_name == "project_status_changed"
        assert event.payload["identification"] == {"id": 7}
        assert event.payload["changes"]["status"] == {"old": "draft", "new": "active"}

    def test_event_registry_isolates_handler_failures(self) -> None:
        handled: list[str] = []
        registry = InMemoryEventRegistry()

        def failing_handler(_event: WorkflowEvent) -> None:
            raise RuntimeError("broken handler")  # noqa: TRY003

        registry.register("invoice.created", handler=failing_handler)
        registry.register(
            "invoice.created",
            handler=lambda event: handled.append(event.event_id),
        )

        event = WorkflowEvent(
            event_id="evt-2",
            event_type="invoice.created",
            payload={"invoice_id": 9},
        )
        assert registry.publish(event) is True
        assert handled == ["evt-2"]

    def test_event_registry_dead_letter_hook_receives_failures(self) -> None:
        dead_letters: list[tuple[str, str]] = []
        registry = InMemoryEventRegistry(
            dead_letter_handler=lambda event, exc: dead_letters.append(
                (event.event_id, str(exc))
            )
        )

        registry.register(
            "invoice.created",
            handler=lambda _event: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        event = WorkflowEvent(
            event_id="evt-3",
            event_type="invoice.created",
            payload={"invoice_id": 12},
        )
        assert registry.publish(event) is False
        assert dead_letters == [("evt-3", "boom")]

    def test_event_registry_retries_handler_before_dead_letter(self) -> None:
        attempts = {"count": 0}
        registry = InMemoryEventRegistry()

        def flaky_handler(_event: WorkflowEvent) -> None:
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise RuntimeError("temporary")

        registry.register(
            "invoice.created",
            handler=flaky_handler,
            retries=1,
        )

        event = WorkflowEvent(
            event_id="evt-4",
            event_type="invoice.created",
            payload={"invoice_id": 100},
        )
        assert registry.publish(event) is True
        assert attempts["count"] == 2

    def test_event_registry_retry_on_can_disable_retries(self) -> None:
        attempts = {"count": 0}
        dead_letters: list[tuple[str, str]] = []
        registry = InMemoryEventRegistry(
            dead_letter_handler=lambda event, exc: dead_letters.append(
                (event.event_id, str(exc))
            )
        )

        def failing_handler(_event: WorkflowEvent) -> None:
            attempts["count"] += 1
            raise RuntimeError("do-not-retry")

        registry.register(
            "invoice.created",
            handler=failing_handler,
            retries=3,
            retry_on=lambda exc: isinstance(exc, TimeoutError),
        )

        event = WorkflowEvent(
            event_id="evt-5",
            event_type="invoice.created",
            payload={"invoice_id": 200},
        )
        assert registry.publish(event) is False
        assert attempts["count"] == 1
        assert dead_letters == [("evt-5", "do-not-retry")]
