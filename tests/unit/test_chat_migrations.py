from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class ChatPendingConfirmationMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[str, str]] = (
        "general_manager",
        "0009_upload_cleanup_state",
    )
    migrate_to: ClassVar[tuple[str, str]] = (
        "general_manager",
        "0010_chat_pending_confirmation_portable_uniqueness",
    )

    def test_upgrade_backfills_marker_and_enforces_portable_constraints(
        self,
    ) -> None:
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([self.migrate_from])
            old_apps = executor.loader.project_state(self.migrate_from).apps
            old_conversation_model = old_apps.get_model(
                "general_manager",
                "ChatConversation",
            )
            old_pending_model = old_apps.get_model(
                "general_manager",
                "ChatPendingConfirmation",
            )
            conversation = old_conversation_model.objects.create(
                session_key="migration-upgrade",
            )
            now = timezone.now()
            common_fields = {
                "conversation_id": conversation.pk,
                "confirmation_id": "shared-confirmation",
                "mutation_name": "createPart",
                "payload": {"input": {"name": "Bolt"}},
                "expires_at": now + timedelta(minutes=5),
            }
            unresolved = old_pending_model.objects.create(**common_fields)
            first_resolved = old_pending_model.objects.create(
                **common_fields,
                resolved_at=now,
            )
            second_resolved = old_pending_model.objects.create(
                **common_fields,
                resolved_at=now + timedelta(seconds=1),
            )

            executor = MigrationExecutor(connection)
            executor.migrate([self.migrate_to])
            new_apps = executor.loader.project_state(self.migrate_to).apps
            pending_model = new_apps.get_model(
                "general_manager",
                "ChatPendingConfirmation",
            )
            migrated_markers = dict(
                pending_model.objects.filter(
                    pk__in=[unresolved.pk, first_resolved.pk, second_resolved.pk]
                ).values_list("pk", "unresolved_marker")
            )

            self.assertEqual(
                set(migrated_markers),
                {unresolved.pk, first_resolved.pk, second_resolved.pk},
            )
            self.assertIs(migrated_markers[unresolved.pk], True)
            self.assertIsNone(migrated_markers[first_resolved.pk])
            self.assertIsNone(migrated_markers[second_resolved.pk])

            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    pending_model.objects.create(**common_fields)

            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    pending_model.objects.create(
                        **{
                            **common_fields,
                            "confirmation_id": "invalid-resolution-state",
                        },
                        resolved_at=now,
                        unresolved_marker=True,
                    )

            additional_resolved = pending_model.objects.create(
                **common_fields,
                resolved_at=now + timedelta(seconds=2),
                unresolved_marker=None,
            )
            self.assertIsNotNone(additional_resolved.pk)
        finally:
            MigrationExecutor(connection).migrate(latest_targets)


class ChatSummaryWatermarkMigrationTests(TransactionTestCase):
    """Verify the summary-watermark migration preserves existing chat history."""

    migrate_from: ClassVar[tuple[str, str]] = (
        "general_manager",
        "0011_search_index_state_dirty_generation",
    )
    migrate_to: ClassVar[tuple[str, str]] = (
        "general_manager",
        "0012_chat_context_watermarks",
    )

    def test_upgrade_keeps_legacy_rows_unsummarized_and_round_trips_new_fields(
        self,
    ) -> None:
        """Existing conversations get a null watermark; new rows persist linkage."""
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([self.migrate_from])
            old_apps = executor.loader.project_state(self.migrate_from).apps
            old_conversation_model = old_apps.get_model(
                "general_manager", "ChatConversation"
            )
            old_message_model = old_apps.get_model("general_manager", "ChatMessage")
            conversation = old_conversation_model.objects.create(
                session_key="watermark-upgrade",
                summary_text="legacy cached summary",
            )
            message = old_message_model.objects.create(
                conversation_id=conversation.pk,
                role="user",
                content="legacy context",
            )

            executor = MigrationExecutor(connection)
            executor.migrate([self.migrate_to])
            new_apps = executor.loader.project_state(self.migrate_to).apps
            conversation_model = new_apps.get_model(
                "general_manager", "ChatConversation"
            )
            message_model = new_apps.get_model("general_manager", "ChatMessage")
            migrated = conversation_model.objects.get(pk=conversation.pk)

            self.assertIsNone(migrated.summarized_through_id)
            self.assertEqual(migrated.summary_text, "legacy cached summary")
            self.assertEqual(
                message_model.objects.get(pk=message.pk).tool_call_id, None
            )

            fresh = conversation_model.objects.create(session_key="watermark-new")
            fresh_message = message_model.objects.create(
                conversation_id=fresh.pk,
                role="assistant",
                content="new context",
                tool_name="lookup_part",
                tool_args={"part_id": 7},
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "lookup_part",
                        "arguments": {"part_id": 7},
                    }
                ],
            )
            tool_result = message_model.objects.create(
                conversation_id=fresh.pk,
                role="tool",
                content="part found",
                tool_name="lookup_part",
                tool_call_id="call-1",
                tool_result={"part_id": 7, "name": "Bolt"},
            )
            fresh.summarized_through_id = fresh_message.pk
            fresh.summary_text = "summary"
            fresh.save(update_fields=["summarized_through", "summary_text"])

            restored = conversation_model.objects.get(pk=fresh.pk)
            self.assertEqual(restored.summarized_through_id, fresh_message.pk)
            self.assertEqual(restored.summary_text, "summary")
            restored_declaration = message_model.objects.get(pk=fresh_message.pk)
            self.assertEqual(restored_declaration.tool_calls[0]["name"], "lookup_part")
            self.assertEqual(
                restored_declaration.tool_calls[0]["arguments"], {"part_id": 7}
            )
            restored_result = message_model.objects.get(pk=tool_result.pk)
            self.assertEqual(restored_result.tool_call_id, "call-1")
            self.assertEqual(
                restored_result.tool_result, {"part_id": 7, "name": "Bolt"}
            )
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
