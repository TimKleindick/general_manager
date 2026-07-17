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
