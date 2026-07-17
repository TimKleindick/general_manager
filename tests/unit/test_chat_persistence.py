from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest import skipIf

from django import VERSION as DJANGO_VERSION
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, models, transaction
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone

from general_manager.chat.models import (
    ChatConversation,
    ChatMessage,
    ChatPendingConfirmation,
    append_chat_message,
    build_conversation_context,
    cleanup_expired_chat_records,
    create_pending_confirmation,
)


class ChatPersistenceTests(TestCase):
    def test_pending_confirmation_uniqueness_uses_portable_marker(self) -> None:
        constraint = next(
            constraint
            for constraint in ChatPendingConfirmation._meta.constraints
            if constraint.name == "gm_chat_pending_conv_conf_uniq"
        )

        assert isinstance(constraint, models.UniqueConstraint)
        assert constraint.fields == (
            "conversation",
            "confirmation_id",
            "unresolved_marker",
        )
        assert constraint.condition is None

    def test_for_actor_reuses_anonymous_session_conversation(self) -> None:
        first = ChatConversation.for_actor(user=None, session_key="anon-1")
        second = ChatConversation.for_actor(user=None, session_key="anon-1")

        assert first.pk == second.pk
        assert first.user_id is None
        assert first.session_key == "anon-1"

    def test_for_actor_starts_fresh_authenticated_conversation(self) -> None:
        user = get_user_model().objects.create_user(
            username="alice",
            email="alice@example.com",
        )
        anonymous = ChatConversation.for_actor(user=None, session_key="anon-2")

        authenticated = ChatConversation.for_actor(user=user, session_key="anon-2")

        assert authenticated.pk != anonymous.pk
        assert authenticated.user == user
        assert authenticated.session_key is None

    def test_append_chat_message_persists_tool_metadata(self) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="anon-3")

        message = append_chat_message(
            conversation,
            role="tool",
            content='{"status": "ok"}',
            tool_name="query",
            tool_args={"manager": "PartManager"},
            tool_result={"data": [{"name": "Bolt"}]},
        )

        stored = ChatMessage.objects.get(pk=message.pk)
        assert stored.tool_name == "query"
        assert stored.tool_args == {"manager": "PartManager"}
        assert stored.tool_result == {"data": [{"name": "Bolt"}]}

    def test_pending_confirmation_lookup_ignores_expired_records(self) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="anon-4")
        expired = ChatPendingConfirmation.objects.create(
            conversation=conversation,
            confirmation_id="confirm-1",
            mutation_name="createPart",
            payload={"name": "Bolt"},
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        active = create_pending_confirmation(
            conversation,
            confirmation_id="confirm-2",
            mutation_name="createPart",
            payload={"name": "Nut"},
            timeout_seconds=30,
        )

        assert (
            ChatPendingConfirmation.active_for_conversation(
                conversation=conversation,
                confirmation_id="confirm-1",
            )
            is None
        )
        assert (
            ChatPendingConfirmation.active_for_conversation(
                conversation=conversation,
                confirmation_id="confirm-2",
            ).pk
            == active.pk
        )
        assert expired.pk != active.pk

    def test_pending_confirmation_id_can_repeat_across_conversations(self) -> None:
        first = ChatConversation.for_actor(user=None, session_key="scoped-1")
        second = ChatConversation.for_actor(user=None, session_key="scoped-2")

        create_pending_confirmation(
            first,
            confirmation_id="tool-repeat",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            timeout_seconds=30,
        )
        duplicate = create_pending_confirmation(
            second,
            confirmation_id="tool-repeat",
            mutation_name="createPart",
            payload={"input": {"name": "Nut"}},
            timeout_seconds=30,
        )

        assert duplicate.confirmation_id == "tool-repeat"
        assert duplicate.conversation_id == second.pk

    def test_unresolved_pending_confirmation_id_stays_unique_within_conversation(
        self,
    ) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="scoped-3")
        create_pending_confirmation(
            conversation,
            confirmation_id="tool-repeat",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            timeout_seconds=30,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ChatPendingConfirmation.objects.create(
                    conversation=conversation,
                    confirmation_id="tool-repeat",
                    mutation_name="createPart",
                    payload={"input": {"name": "Washer"}},
                    expires_at=timezone.now() + timedelta(seconds=30),
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                create_pending_confirmation(
                    conversation,
                    confirmation_id="tool-repeat",
                    mutation_name="createPart",
                    payload={"input": {"name": "Nut"}},
                    timeout_seconds=30,
                )

    def test_resolved_pending_confirmation_id_can_repeat_within_conversation(
        self,
    ) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="scoped-4")
        first = create_pending_confirmation(
            conversation,
            confirmation_id="tool-repeat",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            timeout_seconds=30,
        )

        claimed = ChatPendingConfirmation.claim_for_conversation(
            conversation=conversation,
            confirmation_id="tool-repeat",
        )
        second = create_pending_confirmation(
            conversation,
            confirmation_id="tool-repeat",
            mutation_name="createPart",
            payload={"input": {"name": "Nut"}},
            timeout_seconds=30,
        )

        assert claimed is not None
        assert claimed.pk == first.pk
        assert claimed.unresolved_marker is None
        assert second.pk != first.pk
        assert second.conversation_id == conversation.pk
        assert second.confirmation_id == "tool-repeat"
        assert second.resolved_at is None
        assert second.unresolved_marker is True

    def test_expired_pending_confirmation_id_can_repeat_within_conversation(
        self,
    ) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="scoped-5")
        expired = ChatPendingConfirmation.objects.create(
            conversation=conversation,
            confirmation_id="tool-repeat",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        replacement = create_pending_confirmation(
            conversation,
            confirmation_id="tool-repeat",
            mutation_name="createPart",
            payload={"input": {"name": "Nut"}},
            timeout_seconds=30,
        )

        expired.refresh_from_db()
        assert expired.resolved_at is not None
        assert expired.unresolved_marker is None
        assert replacement.pk != expired.pk
        assert replacement.confirmation_id == "tool-repeat"
        assert replacement.resolved_at is None
        assert replacement.unresolved_marker is True

    def test_claim_for_conversation_marks_pending_resolved_before_returning(
        self,
    ) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="claim-1")
        now = timezone.now()
        pending = create_pending_confirmation(
            conversation,
            confirmation_id="tool-claim",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            timeout_seconds=30,
        )

        claimed = ChatPendingConfirmation.claim_for_conversation(
            conversation=conversation,
            confirmation_id="tool-claim",
            now=now,
        )

        assert claimed is not None
        assert claimed.pk == pending.pk
        assert claimed.resolved_at == now
        assert claimed.unresolved_marker is None
        pending.refresh_from_db()
        assert pending.resolved_at == now
        assert pending.unresolved_marker is None

    def test_pending_confirmation_rejects_inconsistent_resolution_state(self) -> None:
        conversation = ChatConversation.for_actor(
            user=None,
            session_key="resolution-state",
        )
        pending = create_pending_confirmation(
            conversation,
            confirmation_id="tool-resolution-state",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            timeout_seconds=30,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ChatPendingConfirmation.objects.filter(pk=pending.pk).update(
                    resolved_at=timezone.now(),
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ChatPendingConfirmation.objects.filter(pk=pending.pk).update(
                    unresolved_marker=None,
                )

    def test_pending_confirmation_create_derives_resolved_marker(self) -> None:
        conversation = ChatConversation.for_actor(
            user=None,
            session_key="resolved-create",
        )
        resolved_at = timezone.now()

        pending = ChatPendingConfirmation.objects.create(
            conversation=conversation,
            confirmation_id="tool-resolved-create",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            expires_at=resolved_at + timedelta(seconds=30),
            resolved_at=resolved_at,
        )

        pending.refresh_from_db()
        assert pending.resolved_at == resolved_at
        assert pending.unresolved_marker is None

    def test_pending_confirmation_save_updates_resolved_marker(self) -> None:
        conversation = ChatConversation.for_actor(
            user=None,
            session_key="resolved-save",
        )
        pending = create_pending_confirmation(
            conversation,
            confirmation_id="tool-resolved-save",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            timeout_seconds=30,
        )
        resolved_at = timezone.now()

        pending.resolved_at = resolved_at
        pending.save(update_fields=["resolved_at"])

        pending.refresh_from_db()
        assert pending.resolved_at == resolved_at
        assert pending.unresolved_marker is None

    @skipIf(
        DJANGO_VERSION >= (6, 0),
        "Django 6 removed positional arguments from Model.save()",
    )
    def test_pending_confirmation_save_updates_marker_with_positional_fields(
        self,
    ) -> None:
        conversation = ChatConversation.for_actor(
            user=None,
            session_key="resolved-save-positional",
        )
        pending = create_pending_confirmation(
            conversation,
            confirmation_id="tool-resolved-save-positional",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            timeout_seconds=30,
        )
        resolved_at = timezone.now()

        pending.resolved_at = resolved_at
        with self.assertWarns(DeprecationWarning):
            pending.save(False, False, None, ["resolved_at"])

        pending.refresh_from_db()
        assert pending.resolved_at == resolved_at
        assert pending.unresolved_marker is None

    @skipIf(
        DJANGO_VERSION < (6, 0),
        "Django 5 still accepts deprecated positional arguments to Model.save()",
    )
    def test_pending_confirmation_save_rejects_positional_fields(self) -> None:
        pending = ChatPendingConfirmation()

        with self.assertRaises(TypeError):
            pending.save(False)

    def test_claim_for_conversation_returns_none_after_first_claim(self) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="claim-2")
        create_pending_confirmation(
            conversation,
            confirmation_id="tool-claim-once",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            timeout_seconds=30,
        )

        first = ChatPendingConfirmation.claim_for_conversation(
            conversation=conversation,
            confirmation_id="tool-claim-once",
        )
        second = ChatPendingConfirmation.claim_for_conversation(
            conversation=conversation,
            confirmation_id="tool-claim-once",
        )

        assert first is not None
        assert second is None

    def test_claim_for_conversation_can_claim_expired_row_for_timeout(
        self,
    ) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="claim-3")
        now = timezone.now()
        expired = ChatPendingConfirmation.objects.create(
            conversation=conversation,
            confirmation_id="tool-timeout",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            expires_at=now - timedelta(seconds=1),
        )

        assert (
            ChatPendingConfirmation.claim_for_conversation(
                conversation=conversation,
                confirmation_id="tool-timeout",
                now=now,
            )
            is None
        )
        claimed = ChatPendingConfirmation.claim_for_conversation(
            conversation=conversation,
            confirmation_id="tool-timeout",
            now=now,
            allow_expired=True,
        )

        assert claimed is not None
        assert claimed.pk == expired.pk
        assert claimed.resolved_at == now
        assert claimed.unresolved_marker is None
        expired.refresh_from_db()
        assert expired.resolved_at == now
        assert expired.unresolved_marker is None
        assert (
            ChatPendingConfirmation.claim_for_conversation(
                conversation=conversation,
                confirmation_id="tool-timeout",
                now=now,
                allow_expired=True,
            )
            is None
        )

    def test_cleanup_expired_chat_records_deletes_only_stale_records(self) -> None:
        stale = ChatConversation.objects.create(session_key="stale")
        fresh = ChatConversation.objects.create(session_key="fresh")
        ChatPendingConfirmation.objects.create(
            conversation=stale,
            confirmation_id="confirm-stale",
            mutation_name="createPart",
            payload={},
            expires_at=timezone.now() - timedelta(hours=30),
        )
        ChatPendingConfirmation.objects.create(
            conversation=fresh,
            confirmation_id="confirm-fresh",
            mutation_name="createPart",
            payload={},
            expires_at=timezone.now() + timedelta(hours=1),
        )
        ChatConversation.objects.filter(pk=stale.pk).update(
            updated_at=timezone.now() - timedelta(hours=30)
        )

        deleted = cleanup_expired_chat_records(ttl_hours=24)

        assert deleted["conversations"] >= 1
        assert ChatConversation.objects.filter(pk=stale.pk).exists() is False
        assert ChatConversation.objects.filter(pk=fresh.pk).exists() is True
        assert (
            ChatPendingConfirmation.objects.filter(
                confirmation_id="confirm-fresh"
            ).exists()
            is True
        )

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "ttl_hours": 24,
            }
        }
    )
    def test_chat_cleanup_command_reports_deleted_records(self) -> None:
        conversation = ChatConversation.objects.create(session_key="stale-command")
        ChatConversation.objects.filter(pk=conversation.pk).update(
            updated_at=timezone.now() - timedelta(hours=30)
        )

        stream = StringIO()
        call_command("chat_cleanup", stdout=stream)

        output = stream.getvalue()
        assert "Deleted" in output
        assert ChatConversation.objects.filter(pk=conversation.pk).exists() is False

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "max_recent_messages": 3,
                "summarize_after": 4,
            }
        }
    )
    def test_build_conversation_context_summarizes_older_messages_once_and_caches(
        self,
    ) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="summary-1")
        append_chat_message(conversation, role="user", content="u1")
        append_chat_message(conversation, role="assistant", content="a1")
        append_chat_message(
            conversation,
            role="tool",
            content='{"data": 1}',
            tool_name="query",
            tool_result={"data": 1},
        )
        append_chat_message(conversation, role="user", content="u2")
        append_chat_message(conversation, role="assistant", content="a2")

        calls: list[list[str]] = []

        def summarizer(messages: list[ChatMessage]) -> str:
            calls.append([message.content for message in messages])
            return "cached summary"

        context = build_conversation_context(conversation, summarizer=summarizer)
        assert [message.role for message in context] == [
            "system",
            "tool",
            "user",
            "assistant",
        ]
        assert context[0].content == "cached summary"
        assert context[1].tool_name == "query"
        assert conversation.summary_text == "cached summary"
        assert len(calls) == 1

        second = build_conversation_context(conversation, summarizer=summarizer)
        assert [message.role for message in second] == [
            "system",
            "tool",
            "user",
            "assistant",
        ]
        assert len(calls) == 1

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "max_recent_messages": 4,
                "summarize_after": 10,
            }
        }
    )
    def test_build_conversation_context_skips_summary_below_threshold(self) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="summary-2")
        append_chat_message(conversation, role="user", content="u1")
        append_chat_message(conversation, role="assistant", content="a1")
        append_chat_message(conversation, role="user", content="u2")

        context = build_conversation_context(
            conversation,
            summarizer=lambda _messages: "should not be used",
        )

        assert [message.role for message in context] == ["user", "assistant", "user"]
        assert conversation.summary_text == ""
