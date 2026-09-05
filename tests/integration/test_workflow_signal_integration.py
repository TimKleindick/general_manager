from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar
from uuid import uuid4

from django.db.models import CharField

from general_manager.cache.signals import post_data_change
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from general_manager.workflow.actions import ActionRegistry
from general_manager.workflow.backend_registry import configure_workflow_engine
from general_manager.workflow.backend_registry import get_workflow_engine
from general_manager.workflow.backends.local import LocalWorkflowEngine
from general_manager.workflow.engine import WorkflowDefinition, WorkflowExecution
from general_manager.workflow.event_registry import (
    DatabaseEventRegistry,
    InMemoryEventRegistry,
    WorkflowEvent,
)
from general_manager.workflow.models import (
    WorkflowDeliveryAttempt,
    WorkflowEventRecord,
)
from general_manager.workflow.signal_bridge import (
    connect_workflow_signal_bridge,
    disconnect_workflow_signal_bridge,
)


class _SendEmailAction:
    def __init__(self, sent_emails: list[dict[str, Any]]) -> None:
        self._sent_emails = sent_emails

    def execute(
        self,
        context: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {**params, "context": context}
        self._sent_emails.append(payload)
        return {"message_id": "stub-message-id"}


class WorkflowSignalIntegrationTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=200)
                status = CharField(max_length=50)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

        cls.general_manager_classes = [Project]
        cls.Project = Project
        GeneralManagerMeta.all_classes = cls.general_manager_classes

    def setUp(self) -> None:
        super().setUp()
        self.sent_emails: list[dict[str, Any]] = []
        self.dead_letters: list[tuple[str, str]] = []
        self.executions: list[WorkflowExecution] = []
        self.actions = ActionRegistry()
        self.actions.register("send_email", _SendEmailAction(self.sent_emails))

        self.event_registry = InMemoryEventRegistry()
        self.event_registry.register(
            "manager_updated",
            handler=self._on_manager_updated,
            when=lambda event: (
                event.payload.get("changes", {}).get("status", {}).get("new")
                == "active"
            ),
            retries=2,
            retry_on=lambda exc: isinstance(exc, RuntimeError),
            dead_letter_handler=self._record_dead_letter,
        )
        connect_workflow_signal_bridge(registry=self.event_registry)
        configure_workflow_engine(LocalWorkflowEngine())

    def tearDown(self) -> None:
        disconnect_workflow_signal_bridge()
        configure_workflow_engine(None)
        super().tearDown()

    def _on_manager_updated(self, event: WorkflowEvent) -> None:
        payload = event.payload
        if payload.get("manager") != "Project":
            return
        changes = payload.get("changes", {})
        if "status" not in changes:
            return
        status_change = changes["status"]
        if status_change["new"] != "active":
            return

        def send_status_email(input_data: dict[str, Any]) -> dict[str, Any]:
            result = self.actions.execute(
                "send_email",
                context={"event_id": input_data["event_id"]},
                params={
                    "to": "ops@example.test",
                    "subject": f"Project status changed to {input_data['new_status']}",
                },
            )
            return {"email_result": result}

        workflow = WorkflowDefinition(
            workflow_id="project_status_email",
            handler=send_status_email,
        )
        engine = get_workflow_engine()
        execution = engine.start(
            workflow,
            input_data={
                "event_id": event.event_id,
                "project_id": payload["identification"]["id"],
                "old_status": status_change["old"],
                "new_status": status_change["new"],
            },
            correlation_id=event.event_id,
        )
        self.executions.append(execution)

    def _record_dead_letter(self, event: WorkflowEvent, exc: Exception) -> None:
        self.dead_letters.append((event.event_id, str(exc)))

    def test_manager_update_triggers_workflow_and_send_email_action(self) -> None:
        project = self.Project.create(
            name="Alpha", status="draft", ignore_permission=True
        )
        project.update(status="active", ignore_permission=True)

        assert len(self.executions) == 1
        assert self.executions[0].workflow_id == "project_status_email"
        assert self.executions[0].state == "completed"
        assert self.executions[0].input_data["old_status"] == "draft"
        assert self.executions[0].input_data["new_status"] == "active"

        assert len(self.sent_emails) == 1
        assert self.sent_emails[0]["to"] == "ops@example.test"
        assert self.sent_emails[0]["subject"] == "Project status changed to active"
        assert self.dead_letters == []

    def test_manager_update_handler_failure_goes_to_dead_letter(self) -> None:
        disconnect_workflow_signal_bridge()
        attempts = {"count": 0}

        def always_failing_handler(_event: WorkflowEvent) -> None:
            attempts["count"] += 1
            raise RuntimeError("handler failed")  # noqa: TRY003

        registry = InMemoryEventRegistry(dead_letter_handler=self._record_dead_letter)
        registry.register(
            "manager_updated",
            handler=always_failing_handler,
            retries=2,
            retry_on=lambda exc: isinstance(exc, RuntimeError),
            dead_letter_handler=self._record_dead_letter,
        )
        connect_workflow_signal_bridge(registry=registry)

        project = self.Project.create(
            name="Beta", status="draft", ignore_permission=True
        )
        project.update(status="active", ignore_permission=True)

        assert attempts["count"] == 3
        assert len(self.dead_letters) == 1
        assert self.dead_letters[0][1] == "handler failed"

    def test_durable_registration_id_reuses_completed_delivery_in_fresh_registry(
        self,
    ) -> None:
        calls: list[str] = []

        def make_handler() -> Callable[[WorkflowEvent], None]:
            def handler(event: WorkflowEvent) -> None:
                calls.append(event.event_id)

            return handler

        event = WorkflowEvent(
            event_id="project-status-durable-registration",
            event_type="general_manager.manager.updated",
            event_name="manager_updated",
            payload={},
        )
        first_registry = DatabaseEventRegistry()
        first_registry.register(
            "manager_updated",
            handler=make_handler(),
            registration_id="project-status-email-v1",
        )

        assert first_registry.publish_sync(event) is True

        fresh_registry = DatabaseEventRegistry()
        fresh_registry.register(
            "manager_updated",
            handler=make_handler(),
            registration_id="project-status-email-v1",
        )

        assert fresh_registry.publish_sync(event) is True
        attempts = WorkflowDeliveryAttempt.objects.filter(
            event__event_id=event.event_id
        )
        assert attempts.count() == 1
        assert attempts.get().handler_registration_id == "project-status-email-v1"
        assert calls == [event.event_id]

    def test_durable_registration_ids_keep_distinct_bound_handlers(self) -> None:
        calls: list[str] = []

        class Handler:
            def __init__(self, label: str) -> None:
                self.label = label

            def handle(self, _event: WorkflowEvent) -> None:
                calls.append(self.label)

        registry = DatabaseEventRegistry()
        registry.register(
            "manager_updated",
            handler=Handler("first").handle,
            registration_id="manager-updated-first",
        )
        registry.register(
            "manager_updated",
            handler=Handler("second").handle,
            registration_id="manager-updated-second",
        )

        assert registry.publish_sync(
            WorkflowEvent(
                event_id="distinct-bound-handlers",
                event_type="general_manager.manager.updated",
                event_name="manager_updated",
                payload={},
            )
        )
        assert calls == ["first", "second"]

    def test_durable_delivery_pairs_do_not_collide_when_ids_contain_colons(
        self,
    ) -> None:
        calls: list[str] = []

        def make_handler(label: str) -> Callable[[WorkflowEvent], None]:
            def handler(_event: WorkflowEvent) -> None:
                calls.append(label)

            return handler

        first_registry = DatabaseEventRegistry()
        first_registry.register(
            "manager_updated",
            handler=make_handler("first"),
            registration_id="b",
        )
        second_registry = DatabaseEventRegistry()
        second_registry.register(
            "manager_updated",
            handler=make_handler("second"),
            registration_id="a:b",
        )

        assert first_registry.publish_sync(
            WorkflowEvent(
                event_id="e:a",
                event_type="general_manager.manager.updated",
                event_name="manager_updated",
                payload={},
            )
        )
        assert second_registry.publish_sync(
            WorkflowEvent(
                event_id="e",
                event_type="general_manager.manager.updated",
                event_name="manager_updated",
                payload={},
            )
        )
        assert calls == ["first", "second"]
        assert WorkflowDeliveryAttempt.objects.count() == 2

    def test_durable_registration_preserves_existing_legacy_delivery_key(
        self,
    ) -> None:
        calls: list[str] = []

        def handler(event: WorkflowEvent) -> None:
            calls.append(event.event_id)

        registration_id = "l" * 40
        registry = DatabaseEventRegistry()
        registry.register(
            "manager_updated",
            handler=handler,
            registration_id=registration_id,
        )
        legacy_event = WorkflowEvent(
            event_id="legacy-event",
            event_type="general_manager.manager.updated",
            event_name="manager_updated",
            payload={},
        )
        legacy_record = WorkflowEventRecord.objects.create(
            event_id=legacy_event.event_id,
            event_type=legacy_event.event_type,
            event_name=legacy_event.event_name,
            payload={},
            metadata={},
        )
        legacy_attempt = WorkflowDeliveryAttempt.objects.create(
            event=legacy_record,
            handler_registration_id=registration_id,
            idempotency_key=f"{legacy_event.event_id}:{registration_id}",
            status=WorkflowDeliveryAttempt.STATUS_COMPLETED,
        )

        assert registry.publish_sync(legacy_event)

        legacy_attempt.refresh_from_db()
        assert (
            legacy_attempt.idempotency_key
            == f"{legacy_event.event_id}:{registration_id}"
        )
        assert len(legacy_attempt.idempotency_key) <= 255
        assert calls == []

    def test_durable_registration_id_uses_bounded_key_for_255_character_id(
        self,
    ) -> None:
        calls: list[str] = []

        def handler(event: WorkflowEvent) -> None:
            calls.append(event.event_id)

        registration_id = "x" * 255
        registry = DatabaseEventRegistry()
        registry.register(
            "manager_updated",
            handler=handler,
            registration_id=registration_id,
        )
        event = WorkflowEvent(
            event_id=str(uuid4()),
            event_type="general_manager.manager.updated",
            event_name="manager_updated",
            payload={},
        )

        assert registry.publish_sync(event)

        new_attempt = WorkflowDeliveryAttempt.objects.get(
            event__event_id=event.event_id
        )
        assert new_attempt.idempotency_key.startswith("v3_")
        assert len(new_attempt.idempotency_key) < 255
        assert calls == [event.event_id]

    def test_new_delivery_key_coexists_with_matching_legacy_v2_key(self) -> None:
        calls: list[str] = []
        legacy_registration_id = (
            "54b13d932c0be6b8e657aa98a227763b7f91e2581ceef09617586c59f32babb3"
        )

        def make_handler(label: str) -> Callable[[WorkflowEvent], None]:
            def handler(_event: WorkflowEvent) -> None:
                calls.append(label)

            return handler

        legacy_event = WorkflowEvent(
            event_id="v2",
            event_type="general_manager.manager.updated",
            event_name="manager_updated",
            payload={},
        )
        legacy_record = WorkflowEventRecord.objects.create(
            event_id=legacy_event.event_id,
            event_type=legacy_event.event_type,
            event_name=legacy_event.event_name,
            payload={},
            metadata={},
        )
        legacy_attempt = WorkflowDeliveryAttempt.objects.create(
            event=legacy_record,
            handler_registration_id=legacy_registration_id,
            idempotency_key=f"v2:{legacy_registration_id}",
            status=WorkflowDeliveryAttempt.STATUS_PENDING,
        )
        legacy_registry = DatabaseEventRegistry()
        legacy_registry.register(
            "manager_updated",
            handler=make_handler("legacy"),
            registration_id=legacy_registration_id,
        )
        new_registry = DatabaseEventRegistry()
        new_registry.register(
            "manager_updated",
            handler=make_handler("new"),
            registration_id="new-registration",
        )

        assert legacy_registry.publish_sync(legacy_event)
        assert new_registry.publish_sync(
            WorkflowEvent(
                event_id="new-event",
                event_type="general_manager.manager.updated",
                event_name="manager_updated",
                payload={},
            )
        )

        legacy_attempt.refresh_from_db()
        new_attempt = WorkflowDeliveryAttempt.objects.get(event__event_id="new-event")
        assert calls == ["legacy", "new"]
        assert legacy_attempt.idempotency_key == f"v2:{legacy_registration_id}"
        assert new_attempt.idempotency_key.startswith("v3_")

    def test_django_post_data_change_signal_triggers_workflow_path(self) -> None:
        signal_actions: list[str | None] = []

        def capture_post_data_change(sender: Any, **kwargs: Any) -> None:
            del sender
            signal_actions.append(kwargs.get("action"))

        dispatch_uid = "test_workflow_signal_integration_capture_post_data_change"
        post_data_change.connect(
            capture_post_data_change,
            weak=False,
            dispatch_uid=dispatch_uid,
        )
        try:
            project = self.Project.create(
                name="Gamma",
                status="draft",
                ignore_permission=True,
            )
            project.update(status="active", ignore_permission=True)
        finally:
            post_data_change.disconnect(dispatch_uid=dispatch_uid)

        assert "update" in signal_actions
        assert len(self.executions) == 1
        assert self.executions[0].workflow_id == "project_status_email"
        assert self.executions[0].state == "completed"

    def test_manager_delete_signal_includes_identification_in_workflow_event(
        self,
    ) -> None:
        deleted_events: list[WorkflowEvent] = []
        self.event_registry.register(
            "manager_deleted",
            handler=deleted_events.append,
        )
        project = self.Project.create(
            name="Delta",
            status="draft",
            ignore_permission=True,
        )
        identification_before = dict(project.identification)
        project.identification["delete_marker"] = "current"
        current_identification = dict(project.identification)

        project.delete(ignore_permission=True)

        assert len(deleted_events) == 1
        assert current_identification != identification_before
        assert deleted_events[0].payload["identification"] == current_identification
