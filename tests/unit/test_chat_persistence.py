from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
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
