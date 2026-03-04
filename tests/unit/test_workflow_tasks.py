from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from general_manager.workflow.tasks import (
    WORKFLOW_BEAT_SCHEDULE_KEY,
    configure_workflow_beat_schedule_from_settings,
    publish_outbox_batch,
    route_outbox_claims_batch,
)


class WorkflowBeatScheduleTests(SimpleTestCase):
    @override_settings(
        GENERAL_MANAGER={
            "WORKFLOW_MODE": "production",
            "WORKFLOW_BEAT_ENABLED": True,
            "WORKFLOW_BEAT_OUTBOX_INTERVAL_SECONDS": 7,
            "WORKFLOW_BEAT_MAX_JITTER_SECONDS": 0,
        }
    )
    def test_configure_workflow_beat_schedule_registers_task(self) -> None:
        fake_conf = SimpleNamespace(beat_schedule={})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.workflow.tasks.CELERY_AVAILABLE", True),
            patch("general_manager.workflow.tasks.current_app", fake_app),
        ):
            configured = configure_workflow_beat_schedule_from_settings()
        assert configured is True
        entry = fake_conf.beat_schedule[WORKFLOW_BEAT_SCHEDULE_KEY]
        assert entry["task"] == "general_manager.workflow.tasks.publish_outbox_batch"
        assert entry["schedule"] == 7.0
        assert entry["options"] == {"queue": "workflow.events"}

    @override_settings(
        GENERAL_MANAGER={
            "WORKFLOW_MODE": "production",
            "WORKFLOW_BEAT_ENABLED": True,
            "WORKFLOW_BEAT_OUTBOX_INTERVAL_SECONDS": 5,
            "WORKFLOW_BEAT_MAX_JITTER_SECONDS": 0,
        }
    )
    def test_configure_workflow_beat_schedule_is_idempotent(self) -> None:
        fake_conf = SimpleNamespace(beat_schedule={})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.workflow.tasks.CELERY_AVAILABLE", True),
            patch("general_manager.workflow.tasks.current_app", fake_app),
        ):
            configure_workflow_beat_schedule_from_settings()
            configure_workflow_beat_schedule_from_settings()
        assert len(fake_conf.beat_schedule) == 1


class WorkflowBatchTaskTests(SimpleTestCase):
    def test_route_outbox_claims_batch_isolates_item_failures(self) -> None:
        with patch(
            "general_manager.workflow.tasks.route_outbox_event",
            side_effect=[True, RuntimeError("boom"), False],
        ):
            routed = route_outbox_claims_batch([(1, "a"), (2, "b"), (3, "c")])
        assert routed == 1

    def test_publish_outbox_batch_dispatches_single_batch_task(self) -> None:
        def _claim_outbox_batch(*, batch_size=None):
            del batch_size
            return [(1, "t1"), (2, "t1")]

        fake_registry = SimpleNamespace(
            claim_outbox_batch=_claim_outbox_batch,
            outbox_snapshot=lambda: (9, 4.0),
        )
        with (
            patch(
                "general_manager.workflow.tasks.get_event_registry",
                return_value=fake_registry,
            ),
            patch(
                "general_manager.workflow.tasks.DatabaseEventRegistry", SimpleNamespace
            ),
            patch("general_manager.workflow.tasks.CELERY_AVAILABLE", True),
            patch(
                "general_manager.workflow.tasks.route_outbox_claims_batch.delay"
            ) as delay,
        ):
            dispatched = publish_outbox_batch()
        assert dispatched == 2
        delay.assert_called_once_with([(1, "t1"), (2, "t1")])
