"""Tests for workflow management commands."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from general_manager.workflow.models import WorkflowEventRecord, WorkflowOutbox


class WorkflowDrainOutboxCommandTests(SimpleTestCase):
    """Verify the workflow outbox drain command."""

    def test_drain_outbox_delegates_and_prints_plural_summary(self) -> None:
        stdout = StringIO()
        with patch(
            "general_manager.management.commands.workflow_drain_outbox."
            "publish_outbox_batch",
            return_value=2,
        ) as publish:
            call_command("workflow_drain_outbox", stdout=stdout)

        publish.assert_called_once_with()
        self.assertIn("Dispatched 2 outbox records for routing.", stdout.getvalue())

    def test_drain_outbox_prints_singular_summary(self) -> None:
        stdout = StringIO()
        with patch(
            "general_manager.management.commands.workflow_drain_outbox."
            "publish_outbox_batch",
            return_value=1,
        ):
            call_command("workflow_drain_outbox", stdout=stdout)

        self.assertIn("Dispatched 1 outbox record for routing.", stdout.getvalue())


class WorkflowReplayDeadLettersCommandValidationTests(SimpleTestCase):
    """Verify replay command validation paths that do not touch the database."""

    def test_replay_rejects_programmatic_boolean_limit(self) -> None:
        with self.assertRaisesRegex(CommandError, "limit must be an integer"):
            call_command("workflow_replay_dead_letters", limit=True)

    def test_replay_rejects_programmatic_string_limit(self) -> None:
        with self.assertRaisesRegex(CommandError, "limit must be an integer"):
            call_command("workflow_replay_dead_letters", limit="5")

    def test_replay_non_positive_limit_requeues_none(self) -> None:
        stdout = StringIO()

        call_command("workflow_replay_dead_letters", limit=0, stdout=stdout)

        self.assertIn("Requeued 0 dead-letter rows.", stdout.getvalue())


class WorkflowReplayDeadLettersCommandTests(TestCase):
    """Verify replay command database updates."""

    def test_replay_limit_updates_dead_letters_back_to_pending(self) -> None:
        event = WorkflowEventRecord.objects.create(
            event_id="evt-1",
            event_type="project.updated",
            payload={},
            metadata={},
        )
        WorkflowOutbox.objects.create(
            event=event,
            status=WorkflowOutbox.STATUS_DEAD_LETTER,
            attempts=3,
            last_error="broken",
        )
        WorkflowOutbox.objects.create(
            event=event,
            status=WorkflowOutbox.STATUS_DEAD_LETTER,
            attempts=2,
            last_error="also broken",
        )
        stdout = StringIO()

        call_command("workflow_replay_dead_letters", limit=1, stdout=stdout)

        replayed = WorkflowOutbox.objects.get(status=WorkflowOutbox.STATUS_PENDING)
        self.assertIsNone(replayed.last_error)
        self.assertEqual(replayed.attempts, 0)
        self.assertEqual(
            WorkflowOutbox.objects.filter(
                status=WorkflowOutbox.STATUS_DEAD_LETTER
            ).count(),
            1,
        )
        self.assertIn("Requeued 1 dead-letter row.", stdout.getvalue())

    def test_replay_reports_no_rows_for_empty_dead_letter_queue(self) -> None:
        stdout = StringIO()

        call_command("workflow_replay_dead_letters", limit=10, stdout=stdout)

        self.assertIn("No dead-letter outbox rows found.", stdout.getvalue())
