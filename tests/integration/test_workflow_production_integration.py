from __future__ import annotations

from typing import ClassVar
from unittest.mock import patch

from django.core.management import call_command
from django.db.models import CharField
from django.test import override_settings

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from general_manager.workflow.event_registry import (
    DatabaseEventRegistry,
    InMemoryEventRegistry,
    WorkflowEvent,
    configure_event_registry,
)
from general_manager.workflow.models import (
    WorkflowDeliveryAttempt,
    WorkflowEventRecord,
    WorkflowOutbox,
)
from general_manager.workflow.signal_bridge import (
    connect_workflow_signal_bridge,
    disconnect_workflow_signal_bridge,
)


@override_settings(
    GENERAL_MANAGER={
        "WORKFLOW_MODE": "production",
        "WORKFLOW_ASYNC": True,
        "WORKFLOW_SIGNAL_BRIDGE": True,
        "WORKFLOW_MAX_RETRIES": 1,
        "WORKFLOW_DEAD_LETTER_ENABLED": True,
    }
)
class WorkflowProductionIntegrationTests(GeneralManagerTransactionTestCase):
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
        self.handled_events: list[str] = []
        self.registry = DatabaseEventRegistry()
        self.registry.register(
            "manager_updated",
            handler=self._on_manager_updated,
            when=lambda event: (
                event.payload.get("manager") == "Project"
                and event.payload.get("changes", {}).get("status", {}).get("new")
                == "active"
            ),
        )
        connect_workflow_signal_bridge(registry=self.registry)

    def tearDown(self) -> None:
        disconnect_workflow_signal_bridge()
        configure_event_registry(InMemoryEventRegistry())
        super().tearDown()

    def _on_manager_updated(self, event: WorkflowEvent) -> None:
        self.handled_events.append(event.event_id)

    def test_signal_bridge_persists_event_and_outbox_in_async_mode(self) -> None:
        with patch(
            "general_manager.workflow.tasks.publish_outbox_batch.delay"
        ) as delay:
            project = self.Project.create(
                name="Prod Alpha",
                status="draft",
                ignore_permission=True,
            )
            project.update(status="active", ignore_permission=True)

        assert delay.call_count == 2
        assert self.handled_events == []
        assert WorkflowEventRecord.objects.count() == 2
        update_event = WorkflowEventRecord.objects.filter(
            event_type="general_manager.manager.updated"
        ).first()
        assert update_event is not None
        assert update_event.payload["changes"]["status"]["old"] == "draft"
        assert update_event.payload["changes"]["status"]["new"] == "active"
        assert (
            WorkflowOutbox.objects.filter(status=WorkflowOutbox.STATUS_PENDING).count()
            == 2
        )

    def test_drain_outbox_routes_event_and_marks_processed(self) -> None:
        with patch("general_manager.workflow.tasks.publish_outbox_batch.delay"):
            project = self.Project.create(
                name="Prod Beta",
                status="draft",
                ignore_permission=True,
            )
            project.update(status="active", ignore_permission=True)

        with patch("general_manager.workflow.tasks.CELERY_AVAILABLE", False):
            call_command("workflow_drain_outbox")

        assert len(self.handled_events) == 1
        assert (
            WorkflowOutbox.objects.filter(
                status=WorkflowOutbox.STATUS_PROCESSED
            ).count()
            == 2
        )
        assert (
            WorkflowDeliveryAttempt.objects.filter(
                status=WorkflowDeliveryAttempt.STATUS_COMPLETED
            ).count()
            == 1
        )

    def test_dead_letter_and_replay_commands(self) -> None:
        disconnect_workflow_signal_bridge()
        failing_registry = DatabaseEventRegistry()

        def always_failing(_event: WorkflowEvent) -> None:
            raise RuntimeError("broken handler")  # noqa: TRY003

        failing_registry.register("manager_updated", handler=always_failing)
        connect_workflow_signal_bridge(registry=failing_registry)

        with patch("general_manager.workflow.tasks.publish_outbox_batch.delay"):
            project = self.Project.create(
                name="Prod Gamma",
                status="draft",
                ignore_permission=True,
            )
            project.update(status="active", ignore_permission=True)

        with patch("general_manager.workflow.tasks.CELERY_AVAILABLE", False):
            call_command("workflow_drain_outbox")

        dead_letters = WorkflowOutbox.objects.filter(
            status=WorkflowOutbox.STATUS_DEAD_LETTER
        )
        assert dead_letters.count() == 1
        dead_letter_ids = list(dead_letters.values_list("id", flat=True))

        call_command("workflow_replay_dead_letters", limit=10)
        replayed = WorkflowOutbox.objects.filter(id__in=dead_letter_ids)
        assert replayed.count() == 1
        assert replayed.first() is not None
        assert replayed.first().status == WorkflowOutbox.STATUS_PENDING
