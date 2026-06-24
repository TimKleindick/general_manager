from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from threading import Event, Thread

import pytest
from django.test import SimpleTestCase

from general_manager.workflow.actions import (
    ActionAlreadyRegisteredError,
    ActionExecutionError,
    ActionNotFoundError,
    ActionRegistry,
)
from general_manager.workflow.backends.local import LocalWorkflowEngine
from general_manager.workflow.engine import (
    ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES,
    ACTIVE_WORKFLOW_STATES,
    TERMINAL_WORKFLOW_STATES,
    WorkflowCancelledError,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowEngineError,
    WorkflowExecution,
    WorkflowInvalidStateError,
    WorkflowExecutionNotFoundError,
)
from general_manager.workflow.event_registry import InMemoryEventRegistry, WorkflowEvent
from general_manager.workflow.events import (
    manager_created_event,
    manager_deleted_event,
    manager_updated_event,
)


class _EchoAction:
    def execute(
        self, context: Mapping[str, object], params: Mapping[str, object]
    ) -> dict[str, object]:
        return {"context": context, "params": params}


class _FailingAction:
    def execute(
        self, _context: Mapping[str, object], _params: Mapping[str, object]
    ) -> None:
        raise RuntimeError("boom")


class _CaptureAction:
    context: object | None = None
    params: object | None = None

    def execute(
        self, context: Mapping[str, object], params: Mapping[str, object]
    ) -> dict[str, object]:
        self.context = context
        self.params = params
        return {"captured": True}


class _FalseyDict(dict[str, object]):
    def __bool__(self) -> bool:
        return False


class WorkflowCoreTests(SimpleTestCase):
    def test_workflow_definition_defaults_and_mutable_metadata_boundary(self) -> None:
        metadata = {"tags": ["initial"]}
        definition = WorkflowDefinition(workflow_id="sync_project", metadata=metadata)

        metadata["tags"].append("changed")

        assert definition.version == "1"
        assert definition.description is None
        assert definition.handler is None
        assert definition.metadata == {"tags": ["initial", "changed"]}

    def test_workflow_execution_defaults_and_state_constants(self) -> None:
        execution = WorkflowExecution(
            execution_id="exec-1",
            workflow_id="sync_project",
            state="pending",
        )

        assert execution.input_data == {}
        assert execution.output_data is None
        assert execution.correlation_id is None
        assert execution.started_at is None
        assert execution.ended_at is None
        assert execution.error is None
        assert execution.metadata == {}
        assert ACTIVE_WORKFLOW_STATES == ("pending", "running", "waiting")
        assert ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES == (
            "pending",
            "running",
            "waiting",
            "completed",
        )
        assert TERMINAL_WORKFLOW_STATES == ("failed", "cancelled", "completed")

    def test_workflow_engine_errors_include_context(self) -> None:
        not_found = WorkflowExecutionNotFoundError("exec-missing")
        cancelled = WorkflowCancelledError("exec-cancelled")
        invalid = WorkflowInvalidStateError(
            "exec-invalid",
            operation="resume",
            state="completed",
            expected_states=("waiting",),
        )

        assert isinstance(not_found, WorkflowEngineError)
        assert "exec-missing" in str(not_found)
        assert "exec-cancelled" in str(cancelled)
        assert "exec-invalid" in str(invalid)
        assert "resume" in str(invalid)
        assert "completed" in str(invalid)
        assert "waiting" in str(invalid)

    def test_local_engine_satisfies_workflow_engine_protocol(self) -> None:
        assert isinstance(LocalWorkflowEngine(), WorkflowEngine)

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

    def test_action_registry_rejects_duplicate_names_by_default(self) -> None:
        registry = ActionRegistry()
        registry.register("echo", _EchoAction())

        with pytest.raises(ActionAlreadyRegisteredError):
            registry.register("echo", _EchoAction())

    def test_action_registry_replace_overwrites_existing_action(self) -> None:
        registry = ActionRegistry()
        first = _CaptureAction()
        second = _CaptureAction()
        registry.register("capture", first)
        registry.register("capture", second, replace=True)

        assert registry.get("capture") is second

    def test_action_registry_names_are_sorted(self) -> None:
        registry = ActionRegistry()
        registry.register("send_email", _EchoAction())
        registry.register("audit", _EchoAction())

        assert registry.names() == ("audit", "send_email")

    def test_action_registry_preserves_falsey_mapping_inputs(self) -> None:
        registry = ActionRegistry()
        action = _CaptureAction()
        context = _FalseyDict(user="42")
        params = _FalseyDict(priority="high")

        registry.register("capture", action)
        result = registry.execute("capture", context=context, params=params)

        assert result == {"captured": True}
        assert action.context is context
        assert action.params is params

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

    def test_manager_created_event_copies_payload_and_metadata(self) -> None:
        values = {"status": "draft"}
        identification = {"id": 7}
        metadata = {"actor": "system"}
        occurred_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)

        event = manager_created_event(
            manager="Project",
            values=values,
            identification=identification,
            event_id="evt-created",
            source="unit-test",
            occurred_at=occurred_at,
            metadata=metadata,
        )

        values["status"] = "changed"
        identification["id"] = 8
        metadata["actor"] = "other"

        assert event.event_id == "evt-created"
        assert event.event_type == "general_manager.manager.created"
        assert event.event_name == "manager_created"
        assert event.source == "unit-test"
        assert event.occurred_at == occurred_at
        assert event.payload == {
            "manager": "Project",
            "values": {"status": "draft"},
            "identification": {"id": 7},
        }
        assert event.metadata == {"actor": "system"}

    def test_manager_updated_event_uses_none_for_missing_old_values(self) -> None:
        event = manager_updated_event(
            manager="Project",
            changes={"status": "active"},
            event_id="evt-1",
        )

        assert event.payload["changes"]["status"] == {"old": None, "new": "active"}

    def test_manager_deleted_event_omits_optional_identification(self) -> None:
        event = manager_deleted_event(manager="Project", event_id="evt-deleted")

        assert event.event_id == "evt-deleted"
        assert event.event_type == "general_manager.manager.deleted"
        assert event.event_name == "manager_deleted"
        assert event.payload == {"manager": "Project"}

    def test_manager_event_helpers_default_id_and_timestamp(self) -> None:
        before = datetime.now(UTC)
        event = manager_deleted_event(manager="Project")
        after = datetime.now(UTC)

        assert event.event_id
        assert before <= event.occurred_at <= after
        assert event.occurred_at.tzinfo is UTC

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

    def test_event_registry_ignores_duplicate_identical_registrations(self) -> None:
        handled: list[str] = []
        registry = InMemoryEventRegistry()

        def handler(event: WorkflowEvent) -> None:
            handled.append(event.event_id)

        registry.register("invoice.created", handler=handler)
        registry.register("invoice.created", handler=handler)

        event = WorkflowEvent(
            event_id="evt-dup-reg",
            event_type="invoice.created",
            payload={"invoice_id": 201},
        )
        assert registry.publish(event) is True
        assert handled == ["evt-dup-reg"]

    def test_event_registry_deduplicates_after_retry_clamping(self) -> None:
        handled: list[str] = []
        registry = InMemoryEventRegistry()

        def handler(event: WorkflowEvent) -> None:
            handled.append(event.event_id)

        registry.register("invoice.created", handler=handler, retries=-1)
        registry.register("invoice.created", handler=handler, retries=0)

        event = WorkflowEvent(
            event_id="evt-dup-clamped-retries",
            event_type="invoice.created",
            payload={"invoice_id": 202},
        )
        assert registry.publish(event) is True
        assert handled == ["evt-dup-clamped-retries"]

    def test_event_registry_bounds_seen_event_cache(self) -> None:
        handled: list[str] = []
        registry = InMemoryEventRegistry(max_seen_event_ids=2)
        registry.register(
            "invoice.created", handler=lambda event: handled.append(event.event_id)
        )

        assert (
            registry.publish(
                WorkflowEvent(
                    event_id="evt-a",
                    event_type="invoice.created",
                    payload={"invoice_id": 1},
                )
            )
            is True
        )
        assert (
            registry.publish(
                WorkflowEvent(
                    event_id="evt-b",
                    event_type="invoice.created",
                    payload={"invoice_id": 2},
                )
            )
            is True
        )
        assert (
            registry.publish(
                WorkflowEvent(
                    event_id="evt-c",
                    event_type="invoice.created",
                    payload={"invoice_id": 3},
                )
            )
            is True
        )

        # The oldest seen id is evicted once the bounded cache is full.
        assert (
            registry.publish(
                WorkflowEvent(
                    event_id="evt-a",
                    event_type="invoice.created",
                    payload={"invoice_id": 4},
                )
            )
            is True
        )
        assert handled == ["evt-a", "evt-b", "evt-c", "evt-a"]

    def test_local_engine_resume_requires_waiting_state(self) -> None:
        engine = LocalWorkflowEngine()
        execution = engine.start(WorkflowDefinition(workflow_id="wf-local"))

        with pytest.raises(WorkflowInvalidStateError):
            engine.resume(execution.execution_id, {"step": "resume"})

    def test_local_engine_cancel_rejects_completed_state(self) -> None:
        engine = LocalWorkflowEngine()
        execution = engine.start(WorkflowDefinition(workflow_id="wf-local"))

        with pytest.raises(WorkflowInvalidStateError):
            engine.cancel(execution.execution_id, reason="late cancel")

    def test_local_engine_preserves_falsey_input_mapping(self) -> None:
        engine = LocalWorkflowEngine()
        payload = _FalseyDict(value=3)
        workflow = WorkflowDefinition(
            workflow_id="wf-local-falsey-input",
            handler=lambda data: {"seen": data["value"]},
        )

        execution = engine.start(workflow, payload)

        assert execution.input_data == {"value": 3}
        assert execution.output_data == {"seen": 3}

    def test_local_engine_records_falsey_resume_signal(self) -> None:
        engine = LocalWorkflowEngine()
        execution = WorkflowExecution(
            execution_id="exec-waiting",
            workflow_id="wf-local-wait",
            state="waiting",
            input_data={},
            metadata={},
        )
        engine._executions[execution.execution_id] = execution
        signal = _FalseyDict(step="approve")

        updated = engine.resume(execution.execution_id, signal)

        assert updated.state == "completed"
        assert updated.metadata["resume_signal"] == {"step": "approve"}

    def test_local_engine_reuses_existing_correlation_id(self) -> None:
        engine = LocalWorkflowEngine()
        workflow = WorkflowDefinition(
            workflow_id="wf-local-correlation",
            handler=lambda payload: {"seen": payload.get("value")},
        )

        first = engine.start(workflow, {"value": 1}, correlation_id="corr-local")
        second = engine.start(workflow, {"value": 2}, correlation_id="corr-local")

        assert first.execution_id == second.execution_id
        assert second.output_data == {"seen": 1}

    def test_local_engine_reserves_correlation_id_during_inflight_start(self) -> None:
        engine = LocalWorkflowEngine()
        handler_started = Event()
        release_handler = Event()
        calls: list[int] = []
        results: list[WorkflowExecution] = []

        def handler(payload: dict[str, object]) -> dict[str, object]:
            calls.append(int(payload["value"]))
            handler_started.set()
            assert release_handler.wait(timeout=2)
            return {"seen": payload["value"]}

        workflow = WorkflowDefinition(
            workflow_id="wf-local-correlation-race",
            handler=handler,
        )

        def start_first() -> None:
            results.append(engine.start(workflow, {"value": 1}, correlation_id="corr"))

        def start_second() -> None:
            results.append(engine.start(workflow, {"value": 2}, correlation_id="corr"))

        first_thread = Thread(target=start_first)
        first_thread.start()
        assert handler_started.wait(timeout=2)

        second_thread = Thread(target=start_second)
        second_thread.start()
        release_handler.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)

        assert len(results) == 2
        assert calls == [1]
        assert results[0].execution_id == results[1].execution_id
        assert engine.status(results[0].execution_id).output_data == {"seen": 1}
