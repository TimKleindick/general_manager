from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.http import HttpRequest, JsonResponse
from django.test import RequestFactory, SimpleTestCase

from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from general_manager.chat.views import (
    _PreparedMessageRequest,
    _build_messages,
    _check_permission,
    _ensure_session_key,
    _execute_confirmation_request,
    _execute_message_request,
    _parse_json_body,
    _render_summary_source,
    _run_provider_turn,
    _stream_message_events,
    _summarize_messages_with_provider,
    chat_confirm_view,
    chat_http_view,
    chat_sse_view,
)


def _unwrap_view(view):  # type: ignore[no-untyped-def]
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


async def _collect_events(
    iterator: AsyncIterator[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [event async for event in iterator]


class _Session:
    session_key: str | None = None

    def __init__(self) -> None:
        self.saved = False

    def save(self) -> None:
        self.saved = True
        self.session_key = "saved-session"


class _SummaryProvider:
    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        yield TextChunkEvent(content=" brief")
        yield TextChunkEvent(content=" summary ")


class _MutateSuccessProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        self.calls += 1
        if self.calls == 1:
            yield ToolCallEvent(
                id="mutate-1",
                name="mutate",
                args={"mutation": "updatePart", "input": {"id": "1"}},
            )
            return
        yield TextChunkEvent(content="updated")
        yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=3))


class _QueryLoopProvider:
    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        yield ToolCallEvent(
            id="query-1",
            name="query",
            args={"manager": "Part", "fields": ["name"]},
        )


class _EmptyThenTextProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        self.calls += 1
        if self.calls == 1:
            yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
            return
        assert messages[-1].role == "system"
        yield TextChunkEvent(content="Recovered from tool output.")
        yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=2))


class _NoEventProvider:
    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        if False:
            yield TextChunkEvent(content="never")


class ChatViewHelperTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

    def test_ensure_session_key_saves_unsaved_session(self) -> None:
        session = _Session()
        request = SimpleNamespace(session=session)

        assert _ensure_session_key(request) == "saved-session"
        assert session.saved is True

    def test_render_summary_source_includes_tool_name(self) -> None:
        messages = [
            SimpleNamespace(role="user", tool_name="", content="List parts"),
            SimpleNamespace(role="tool", tool_name="query", content="[]"),
        ]

        assert _render_summary_source(messages) == "user: List parts\ntool:query: []"

    def test_summarize_messages_collects_text_chunks(self) -> None:
        summary = async_to_sync(_summarize_messages_with_provider)(
            _SummaryProvider(),
            [SimpleNamespace(role="user", tool_name="", content="hello")],
        )

        assert summary == "brief summary"

    def test_build_messages_updates_empty_summary_when_history_is_long(self) -> None:
        conversation = SimpleNamespace(summary_text="")
        old_message = SimpleNamespace(role="user", content="old", tool_name="")
        recent_message = SimpleNamespace(
            role="assistant", content="recent", tool_name=""
        )

        with (
            patch(
                "general_manager.chat.views.get_chat_settings",
                return_value={"summarize_after": 1, "max_recent_messages": 1},
            ),
            patch(
                "general_manager.chat.views.get_conversation_messages",
                return_value=[old_message, recent_message],
            ),
            patch(
                "general_manager.chat.views._summarize_messages_with_provider",
                new=AsyncMock(return_value="summary"),
            ),
            patch("general_manager.chat.views.update_conversation_summary") as update,
            patch(
                "general_manager.chat.views.build_conversation_context",
                return_value=[recent_message],
            ),
            patch(
                "general_manager.chat.views.build_system_prompt",
                return_value="system",
            ),
        ):
            messages = async_to_sync(_build_messages)(conversation, object())

        update.assert_called_once_with(conversation, summary_text="summary")
        assert [message.role for message in messages] == ["system", "assistant"]

    def test_parse_json_body_returns_empty_for_empty_and_non_object_body(self) -> None:
        empty = HttpRequest()
        empty._body = b""
        array = HttpRequest()
        array._body = b'["not", "an", "object"]'

        assert _parse_json_body(empty) == {}
        assert _parse_json_body(array) == {}

    def test_check_permission_denies_when_configured_callback_returns_false(
        self,
    ) -> None:
        request = self.factory.post(
            "/chat/", data=b"{}", content_type="application/json"
        )
        request.user = SimpleNamespace(is_authenticated=True)

        with patch(
            "general_manager.chat.views.get_chat_permission",
            return_value=lambda *_args: False,
        ):
            response = _check_permission(request)

        assert response is not None
        assert response.status_code == 403
        assert json.loads(response.content) == {"detail": "Forbidden"}

    def test_run_provider_turn_emits_mutation_execution_for_mutate_tool_result(
        self,
    ) -> None:
        provider = _MutateSuccessProvider()

        with (
            patch(
                "general_manager.chat.views.get_chat_settings",
                return_value={
                    "max_retries_per_message": 8,
                    "recover_missing_tool_calls": False,
                },
            ),
            patch("general_manager.chat.views.get_tool_definitions", return_value=[]),
            patch(
                "general_manager.chat.views.execute_chat_tool",
                return_value={"status": "ok"},
            ),
            patch("general_manager.chat.views.append_chat_message"),
            patch("general_manager.chat.views.enforce_chat_rate_limit"),
            patch("general_manager.chat.views.emit_chat_tool_called"),
            patch("general_manager.chat.views.emit_chat_mutation_executed") as mutation,
        ):
            events = async_to_sync(_run_provider_turn)(
                scope={"user": "actor"},
                conversation=object(),
                provider=provider,
                messages=[Message(role="user", content="Update part")],
                transport="sse",
            )

        mutation.assert_called_once()
        assert events[0]["type"] == "tool_call"
        assert events[1] == {
            "type": "tool_result",
            "id": "mutate-1",
            "name": "mutate",
            "result": {"status": "ok"},
        }
        assert events[-1]["type"] == "done"

    def test_run_provider_turn_stops_at_tool_retry_limit(self) -> None:
        with (
            patch(
                "general_manager.chat.views.get_chat_settings",
                return_value={
                    "max_retries_per_message": 1,
                    "recover_missing_tool_calls": False,
                },
            ),
            patch("general_manager.chat.views.get_tool_definitions", return_value=[]),
            patch(
                "general_manager.chat.views.execute_chat_tool",
                return_value={"status": "ok"},
            ),
            patch("general_manager.chat.views.append_chat_message"),
            patch("general_manager.chat.views.emit_chat_tool_called"),
        ):
            events = async_to_sync(_run_provider_turn)(
                scope={},
                conversation=object(),
                provider=_QueryLoopProvider(),
                messages=[Message(role="user", content="List parts")],
                transport="sse",
            )

        assert events[-1] == {
            "type": "error",
            "message": "Chat tool retry limit exceeded.",
            "code": "tool_retry_limit",
        }

    def test_run_provider_turn_recovers_empty_response_after_tool_result(self) -> None:
        provider = _EmptyThenTextProvider()
        messages = [
            Message(role="user", content="List parts"),
            Message(role="assistant", content="Called tool query."),
            Message(role="tool", content='{"rows": []}'),
        ]

        with (
            patch(
                "general_manager.chat.views.get_chat_settings",
                return_value={
                    "max_retries_per_message": 8,
                    "recover_missing_tool_calls": True,
                },
            ),
            patch("general_manager.chat.views.get_tool_definitions", return_value=[]),
            patch("general_manager.chat.views.append_chat_message"),
            patch("general_manager.chat.views.enforce_chat_rate_limit"),
        ):
            events = async_to_sync(_run_provider_turn)(
                scope={},
                conversation=object(),
                provider=provider,
                messages=messages,
                transport="sse",
            )

        assert provider.calls == 2
        assert events[0] == {
            "type": "text_chunk",
            "content": "Recovered from tool output.",
        }
        assert events[-1]["type"] == "done"

    def test_run_provider_turn_returns_empty_list_when_provider_yields_no_events(
        self,
    ) -> None:
        with (
            patch(
                "general_manager.chat.views.get_chat_settings",
                return_value={
                    "max_retries_per_message": 8,
                    "recover_missing_tool_calls": False,
                },
            ),
            patch("general_manager.chat.views.get_tool_definitions", return_value=[]),
        ):
            events = async_to_sync(_run_provider_turn)(
                scope={},
                conversation=object(),
                provider=_NoEventProvider(),
                messages=[Message(role="user", content="hello")],
                transport="http",
            )

        assert events == []

    def test_execute_message_request_returns_empty_events_without_provider(
        self,
    ) -> None:
        request = self.factory.post(
            "/chat/", data=b"{}", content_type="application/json"
        )
        conversation = object()

        with patch(
            "general_manager.chat.views._prepare_message_request",
            new=AsyncMock(
                return_value=_PreparedMessageRequest(
                    conversation=conversation,
                    scope={},
                    provider=None,
                    messages=None,
                    early_events=None,
                )
            ),
        ):
            returned, events = async_to_sync(_execute_message_request)(
                request,
                transport="http",
            )

        assert returned is conversation
        assert events == []

    def test_stream_message_events_yields_early_events(self) -> None:
        request = self.factory.post(
            "/chat/", data=b"{}", content_type="application/json"
        )
        conversation = object()
        early_events = [{"type": "error", "code": "bad_message"}]

        with patch(
            "general_manager.chat.views._prepare_message_request",
            new=AsyncMock(
                return_value=_PreparedMessageRequest(
                    conversation=conversation,
                    scope={},
                    provider=None,
                    messages=None,
                    early_events=early_events,
                )
            ),
        ):
            events = async_to_sync(_collect_events)(
                _stream_message_events(request, transport="sse")
            )

        assert events == early_events

    def test_stream_message_events_returns_without_provider_or_messages(self) -> None:
        request = self.factory.post(
            "/chat/", data=b"{}", content_type="application/json"
        )
        conversation = object()

        with patch(
            "general_manager.chat.views._prepare_message_request",
            new=AsyncMock(
                return_value=_PreparedMessageRequest(
                    conversation=conversation,
                    scope={},
                    provider=None,
                    messages=None,
                    early_events=None,
                )
            ),
        ):
            events = async_to_sync(_collect_events)(
                _stream_message_events(request, transport="sse")
            )

        assert events == []

    def test_execute_confirmation_request_returns_bad_event_for_missing_pending(
        self,
    ) -> None:
        request = self.factory.post(
            "/chat/confirm/",
            data=json.dumps({"confirmation_id": "missing", "confirmed": True}),
            content_type="application/json",
        )
        conversation = object()

        with (
            patch(
                "general_manager.chat.views._conversation_for_request",
                return_value=conversation,
            ),
            patch(
                "general_manager.chat.views.ChatPendingConfirmation.active_for_conversation",
                return_value=None,
            ),
        ):
            returned, events = async_to_sync(_execute_confirmation_request)(request)

        assert returned is conversation
        assert events == [
            {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
        ]

    def test_execute_confirmation_request_records_cancelled_mutation_result(
        self,
    ) -> None:
        request = self.factory.post(
            "/chat/confirm/",
            data=json.dumps({"confirmation_id": "confirm-1", "confirmed": False}),
            content_type="application/json",
        )
        conversation = object()
        pending = SimpleNamespace(
            confirmation_id="confirm-1",
            mutation_name="deletePart",
            payload={"input": {"id": "1"}},
            save=Mock(),
        )

        with (
            patch(
                "general_manager.chat.views._conversation_for_request",
                return_value=conversation,
            ),
            patch(
                "general_manager.chat.views.ChatPendingConfirmation.active_for_conversation",
                return_value=pending,
            ),
            patch("general_manager.chat.views.emit_chat_tool_called"),
            patch("general_manager.chat.views.emit_chat_mutation_executed"),
            patch("general_manager.chat.views.append_chat_message") as append_message,
            patch(
                "general_manager.chat.views.import_provider",
                return_value=Mock(return_value=object()),
            ),
            patch(
                "general_manager.chat.views._build_messages",
                new=AsyncMock(return_value=[Message(role="system", content="system")]),
            ),
            patch(
                "general_manager.chat.views._run_provider_turn",
                new=AsyncMock(return_value=[{"type": "done"}]),
            ),
        ):
            returned, events = async_to_sync(_execute_confirmation_request)(request)

        assert returned is conversation
        assert events[0] == {
            "type": "tool_result",
            "id": "confirm-1",
            "name": "mutate",
            "result": {"status": "cancelled", "reason": "user_rejected"},
        }
        pending.save.assert_called_once_with(update_fields=["resolved_at"])
        append_message.assert_called_once()

    def test_http_and_confirm_views_return_permission_denial(self) -> None:
        denial = JsonResponse({"detail": "Forbidden"}, status=403)
        request = self.factory.post(
            "/chat/", data=b"{}", content_type="application/json"
        )

        with patch("general_manager.chat.views._check_permission", return_value=denial):
            http_response = _unwrap_view(chat_http_view)(request)
            confirm_response = _unwrap_view(chat_confirm_view)(request)

        assert http_response.status_code == 403
        assert confirm_response.status_code == 403

    def test_sse_view_returns_permission_denial_as_sse_event(self) -> None:
        request = self.factory.post(
            "/chat/stream/",
            data=b"{}",
            content_type="application/json",
        )

        with patch(
            "general_manager.chat.views._check_permission",
            return_value=JsonResponse({"detail": "Forbidden"}, status=403),
        ):
            response = _unwrap_view(chat_sse_view)(request)

        assert response.status_code == 403
        assert b"".join(response.streaming_content) == (
            b'data: {"detail": "Forbidden"}\n\n'
        )
