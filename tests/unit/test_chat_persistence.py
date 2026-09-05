from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from io import StringIO
from typing import ClassVar
from unittest import skipIf
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django import VERSION as DJANGO_VERSION
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, connection, models, transaction
from django.test.utils import CaptureQueriesContext
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
    get_conversation_messages,
    provider_messages_from_context,
    update_conversation_summary,
)
from general_manager.chat.providers.base import DoneEvent, TextChunkEvent, TokenUsage
from general_manager.chat.views import _build_messages


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

    def test_for_actor_normalizes_overlength_session_identity(self) -> None:
        raw_session_key = "signed-cookie-" + ("x" * 80)

        first = ChatConversation.for_actor(user=None, session_key=raw_session_key)
        second = ChatConversation.for_actor(user=None, session_key=raw_session_key)

        assert first.pk == second.pk
        assert len(first.session_key) == 64
        assert first.session_key != raw_session_key

    def test_for_actor_reuses_legacy_overlength_session_history(self) -> None:
        raw_session_key = "legacy-cookie-" + ("x" * 80)
        legacy = ChatConversation.objects.create(session_key=raw_session_key)
        append_chat_message(legacy, role="user", content="keep history")

        conversation = ChatConversation.for_actor(
            user=None, session_key=raw_session_key
        )

        assert conversation.pk == legacy.pk
        assert conversation.session_key != raw_session_key
        assert len(conversation.session_key) == 64
        assert get_conversation_messages(conversation)[0].content == "keep history"

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

    def test_context_reloads_linked_tool_call_group_and_results(self) -> None:
        """A persisted follow-up must retain the provider call IDs it depends on."""
        conversation = ChatConversation.for_actor(user=None, session_key="tool-history")
        append_chat_message(
            conversation,
            role="assistant",
            tool_calls=[{"id": "call-1", "name": "query", "args": {"q": "Bolt"}}],
        )
        append_chat_message(
            conversation,
            role="tool",
            content='{"name": "Bolt"}',
            tool_name="query",
            tool_args={"q": "Bolt"},
            tool_call_id="call-1",
            tool_result={"name": "Bolt"},
        )

        from general_manager.chat import models as chat_models
        from general_manager.chat.providers.base import ToolCallEvent

        messages = chat_models.provider_messages_from_context(
            get_conversation_messages(conversation)
        )

        assert messages[0].tool_calls == (
            ToolCallEvent(id="call-1", name="query", args={"q": "Bolt"}),
        )
        assert messages[1].tool_call_id == "call-1"
        assert messages[1].tool_result == {"name": "Bolt"}

    def test_context_turns_unlinked_legacy_tool_rows_into_textual_context(self) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="legacy-tool")
        append_chat_message(
            conversation,
            role="tool",
            content='{"legacy": true}',
            tool_name="query",
            tool_result={"legacy": True},
        )

        from general_manager.chat import models as chat_models

        messages = chat_models.provider_messages_from_context(
            get_conversation_messages(conversation)
        )

        assert messages[0].role == "assistant"
        assert "Historical tool data" in messages[0].content
        assert messages[0].tool_call_id is None

    def test_context_turns_partial_persisted_tool_exchange_into_assistant_text(
        self,
    ) -> None:
        """A missing result cannot leave native tool protocol data in the next turn."""
        from general_manager.chat.providers.openai import OpenAIProvider

        conversation = ChatConversation.for_actor(
            user=None, session_key="partial-tools"
        )
        declaration = append_chat_message(
            conversation,
            role="assistant",
            tool_calls=[
                {"id": "call-a", "name": "query", "args": {"q": "Bolt"}},
                {"id": "call-b", "name": "mutate", "args": {"name": "Bolt"}},
            ],
        )
        result = append_chat_message(
            conversation,
            role="tool",
            content='{"name": "Bolt"}',
            tool_name="query",
            tool_call_id="call-a",
            tool_result={"name": "Bolt"},
        )
        append_chat_message(conversation, role="user", content="follow up")

        provider_messages = provider_messages_from_context(
            get_conversation_messages(conversation)
        )
        wire_messages = OpenAIProvider._build_messages(provider_messages)

        declaration.refresh_from_db()
        result.refresh_from_db()
        assert declaration.tool_calls is not None
        assert result.tool_result == {"name": "Bolt"}
        assert all(not message.tool_calls for message in provider_messages)
        assert all(message.tool_call_id is None for message in provider_messages)
        assert all("tool_calls" not in message for message in wire_messages)
        assert all("tool_call_id" not in message for message in wire_messages)
        assert any(
            "Historical incomplete tool exchange" in message.content
            for message in provider_messages
        )

    def test_context_turns_expired_confirmation_declaration_into_assistant_text(
        self,
    ) -> None:
        """Expiration records no approval and cannot reconstruct a native mutation call."""
        from general_manager.chat.providers.openai import OpenAIProvider

        conversation = ChatConversation.for_actor(
            user=None, session_key="expired-tools"
        )
        append_chat_message(
            conversation,
            role="assistant",
            tool_calls=[
                {"id": "confirm-1", "name": "mutate", "args": {"name": "Bolt"}}
            ],
        )
        pending = create_pending_confirmation(
            conversation,
            confirmation_id="confirm-1",
            mutation_name="createPart",
            payload={"input": {"name": "Bolt"}},
            timeout_seconds=30,
        )
        pending.expires_at = timezone.now() - timedelta(seconds=1)
        pending.save(update_fields=["expires_at"])
        append_chat_message(conversation, role="user", content="follow up")

        messages = provider_messages_from_context(
            get_conversation_messages(conversation)
        )
        wire_messages = OpenAIProvider._build_messages(messages)

        pending.refresh_from_db()
        assert pending.resolved_at is None
        assert all(not message.tool_calls for message in messages)
        assert all(message.tool_call_id is None for message in messages)
        assert all("tool_calls" not in message for message in wire_messages)
        assert all("tool_call_id" not in message for message in wire_messages)
        assert any(
            "Historical incomplete tool exchange" in message.content
            for message in messages
        )

    def test_context_reconnect_turns_abandoned_confirmation_into_assistant_text(
        self,
    ) -> None:
        """Reloaded pending declarations cannot fabricate approval or native tool history."""
        from general_manager.chat.providers.openai import OpenAIProvider

        conversation = ChatConversation.for_actor(
            user=None, session_key="abandoned-tools"
        )
        append_chat_message(
            conversation,
            role="assistant",
            tool_calls=[{"id": "confirm-2", "name": "mutate", "args": {"name": "Nut"}}],
        )
        pending = create_pending_confirmation(
            conversation,
            confirmation_id="confirm-2",
            mutation_name="createPart",
            payload={"input": {"name": "Nut"}},
            timeout_seconds=30,
        )
        append_chat_message(conversation, role="user", content="reconnected follow up")

        reloaded = ChatConversation.objects.get(pk=conversation.pk)
        messages = provider_messages_from_context(get_conversation_messages(reloaded))
        wire_messages = OpenAIProvider._build_messages(messages)

        pending.refresh_from_db()
        assert pending.resolved_at is None
        assert all(not message.tool_calls for message in messages)
        assert all(message.tool_call_id is None for message in messages)
        assert all("tool_calls" not in message for message in wire_messages)
        assert all("tool_call_id" not in message for message in wire_messages)
        assert any(
            "Historical incomplete tool exchange" in message.content
            for message in messages
        )

    def test_context_keeps_late_tool_result_after_a_new_turn_as_text(self) -> None:
        """A result arriving after another turn cannot complete an old declaration."""
        from general_manager.chat.providers.openai import OpenAIProvider

        conversation = ChatConversation.for_actor(user=None, session_key="late-tool")
        append_chat_message(
            conversation,
            role="assistant",
            tool_calls=[{"id": "late-1", "name": "mutate", "args": {"name": "Bolt"}}],
        )
        append_chat_message(conversation, role="user", content="another turn")
        append_chat_message(
            conversation,
            role="tool",
            content='{"status": "late"}',
            tool_name="mutate",
            tool_call_id="late-1",
            tool_result={"status": "late"},
        )

        messages = provider_messages_from_context(
            get_conversation_messages(conversation)
        )
        wire_messages = OpenAIProvider._build_messages(messages)

        assert all(not message.tool_calls for message in messages)
        assert all(message.tool_call_id is None for message in messages)
        assert all("tool_calls" not in message for message in wire_messages)
        assert all("tool_call_id" not in message for message in wire_messages)

    @override_settings(
        GENERAL_MANAGER={"CHAT": {"max_recent_messages": 2, "summarize_after": 2}}
    )
    def test_truncated_context_keeps_a_complete_tool_exchange(self) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="tool-window")
        append_chat_message(conversation, role="user", content="before")
        append_chat_message(
            conversation,
            role="assistant",
            tool_calls=[
                {"id": "call-a", "name": "query", "args": {"q": "Bolt"}},
                {"id": "call-b", "name": "query", "args": {"q": "Nut"}},
            ],
        )
        for call_id, value in (("call-a", "Bolt"), ("call-b", "Nut")):
            append_chat_message(
                conversation,
                role="tool",
                content=json.dumps({"name": value}),
                tool_name="query",
                tool_call_id=call_id,
                tool_result={"name": value},
            )
        append_chat_message(conversation, role="user", content="follow up")

        context = build_conversation_context(conversation)
        messages = provider_messages_from_context(context)

        assert [message.role for message in messages] == [
            "assistant",
            "tool",
            "tool",
            "user",
        ]
        assert [message.tool_call_id for message in messages[1:3]] == [
            "call-a",
            "call-b",
        ]

    @override_settings(
        GENERAL_MANAGER={"CHAT": {"max_recent_messages": 2, "summarize_after": 2}}
    )
    def test_truncated_context_scopes_reused_tool_ids_to_new_exchange(self) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="reused-id")
        append_chat_message(
            conversation,
            role="assistant",
            tool_calls=[{"id": "same", "name": "query", "args": {"q": "old"}}],
        )
        append_chat_message(
            conversation,
            role="tool",
            content='{"result": "old"}',
            tool_name="query",
            tool_call_id="same",
            tool_result={"result": "old"},
        )
        append_chat_message(
            conversation,
            role="assistant",
            tool_calls=[{"id": "same", "name": "query", "args": {"q": "new"}}],
        )
        append_chat_message(
            conversation,
            role="tool",
            content='{"result": "new"}',
            tool_name="query",
            tool_call_id="same",
            tool_result={"result": "new"},
        )
        append_chat_message(conversation, role="user", content="follow up")

        context = build_conversation_context(conversation)
        messages = provider_messages_from_context(context)

        assert [message.content for message in messages] == [
            "",
            '{"result": "new"}',
            "follow up",
        ]
        assert messages[1].tool_call_id == "same"

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
        append_chat_message(stale, role="user", content="stale question")
        append_chat_message(stale, role="assistant", content="stale answer")
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

        assert deleted["conversations"] == 1
        assert deleted["messages"] == 2
        assert deleted["pending_confirmations"] == 1
        assert deleted["total"] == 4
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
        append_chat_message(conversation, role="user", content="question")
        append_chat_message(conversation, role="assistant", content="answer")
        ChatConversation.objects.filter(pk=conversation.pk).update(
            updated_at=timezone.now() - timedelta(hours=30)
        )

        stream = StringIO()
        call_command("chat_cleanup", stdout=stream)

        output = stream.getvalue()
        assert "Deleted" in output
        assert "1 chat conversations" in output
        assert "2 messages" in output
        assert "0 pending confirmations" in output
        assert "(3 rows total)" in output
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
        GENERAL_MANAGER={"CHAT": {"max_recent_messages": 2, "summarize_after": 2}}
    )
    def test_context_regenerates_summary_when_older_boundary_advances(self) -> None:
        conversation = ChatConversation.for_actor(
            user=None, session_key="summary-advance"
        )
        for content in ("u1", "a1", "u2", "a2"):
            append_chat_message(
                conversation,
                role="user" if content.startswith("u") else "assistant",
                content=content,
            )
        calls: list[list[str]] = []

        def summarizer(messages: list[ChatMessage]) -> str:
            calls.append([message.content for message in messages])
            return f"summary-{len(calls)}"

        build_conversation_context(conversation, summarizer=summarizer)
        append_chat_message(conversation, role="user", content="u3")
        build_conversation_context(conversation, summarizer=summarizer)

        conversation.refresh_from_db()
        assert calls == [["u1", "a1"], ["u1", "a1", "u2"]]
        assert conversation.summary_text == "summary-2"
        assert conversation.summarized_through.content == "u2"

    def test_late_summary_does_not_move_watermark_or_text_backward(self) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="summary-race")
        older = append_chat_message(conversation, role="user", content="older")
        newer = append_chat_message(conversation, role="assistant", content="newer")

        update_conversation_summary(
            conversation, summary_text="new summary", summarized_through=newer
        )
        update_conversation_summary(
            conversation, summary_text="late old summary", summarized_through=older
        )

        conversation.refresh_from_db()
        assert conversation.summary_text == "new summary"
        assert conversation.summarized_through_id == newer.pk

    def test_summary_update_locks_conversation_without_nullable_join(self) -> None:
        conversation = ChatConversation.for_actor(user=None, session_key="summary-lock")
        watermark = append_chat_message(conversation, role="user", content="covered")

        with CaptureQueriesContext(connection) as queries:
            update_conversation_summary(
                conversation, summary_text="summary", summarized_through=watermark
            )

        locked_select = next(
            query["sql"]
            for query in queries.captured_queries
            if 'FROM "general_manager_chatconversation"' in query["sql"]
        )
        assert "JOIN" not in locked_select.upper()

    @override_settings(
        GENERAL_MANAGER={"CHAT": {"max_recent_messages": 1, "summarize_after": 1}}
    )
    def test_timed_out_summary_keeps_text_and_watermark_unwritten(self) -> None:
        class _ReportedThenNeverCompletes:
            provider_config: ClassVar[dict[str, float]] = {"timeout_seconds": 0.03}

            async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
                del messages, tools
                yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=3))
                while True:
                    await asyncio.sleep(0.001)
                    yield TextChunkEvent(content="late")

        conversation = ChatConversation.for_actor(
            user=None, session_key="summary-timeout"
        )
        append_chat_message(conversation, role="user", content="u1")
        append_chat_message(conversation, role="assistant", content="a1")
        append_chat_message(conversation, role="user", content="u2")

        with patch("general_manager.chat.context.enforce_chat_rate_limit") as limit:
            with self.assertRaises(asyncio.TimeoutError):
                async_to_sync(_build_messages)(
                    conversation, _ReportedThenNeverCompletes(), scope={"user": None}
                )

        conversation.refresh_from_db()
        assert conversation.summary_text == ""
        assert conversation.summarized_through_id is None
        limit.assert_called_once_with(
            {"user": None}, input_tokens=2, output_tokens=3, count_request=False
        )

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
