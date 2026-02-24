from __future__ import annotations

from typing import Any, ClassVar

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
from general_manager.workflow.event_registry import InMemoryEventRegistry, WorkflowEvent
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
