from __future__ import annotations

import json
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.bootstrap import ensure_chat_http_routes
from general_manager.chat.models import ChatMessage, ChatPendingConfirmation
from tests import test_urls


GENERIC_CHAT_ERROR_EVENT = {
    "type": "error",
    "message": "Chat request failed.",
    "code": "chat_error",
}


class HttpIntegrationProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        self.calls += 1

        async def _stream():
            last_message = messages[-1]
            if self.calls == 1 and last_message.content == "hello":
                from general_manager.chat.providers.base import (
                    DoneEvent,
                    TextChunkEvent,
                    TokenUsage,
                )

                yield TextChunkEvent(content="hello back")
                yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
                return
            if self.calls == 1 and last_message.content == "create a part":
                from general_manager.chat.providers.base import (
                    DoneEvent,
                    ToolCallEvent,
                    TokenUsage,
                )

                yield ToolCallEvent(
                    id="tool-create",
                    name="mutate",
                    args={"mutation": "createPart", "input": {"name": "Bolt"}},
                )
                yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
                return
            from general_manager.chat.providers.base import (
                DoneEvent,
                TextChunkEvent,
                TokenUsage,
            )

            yield TextChunkEvent(content=f"tool:{last_message.content}")
            yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=2))

        return _stream()


class ExplodingHttpProvider:
    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools

        async def _stream():
            raise RuntimeError(  # noqa: TRY003
                "database password leaked in stack context"
            )
            yield  # pragma: no cover

        return _stream()


class TimeoutHttpProvider:
    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools

        async def _stream():
            raise TimeoutError("provider timed out")  # noqa: TRY003
            yield  # pragma: no cover

        return _stream()


class LazySseProvider:
    started = False
    yielded = False

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools

        async def _stream():
            from general_manager.chat.providers.base import (
                DoneEvent,
                TextChunkEvent,
                TokenUsage,
            )

            type(self).started = True
            yield TextChunkEvent(content="first")
            type(self).yielded = True
            yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))

        return _stream()


class HttpMissingToolRecoveryProvider:
    calls: ClassVar[list[object]] = []

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        type(self).calls.append(list(messages))

        async def _stream():
            from general_manager.chat.providers.base import (
                DoneEvent,
                TextChunkEvent,
                TokenUsage,
            )

            if len(type(self).calls) == 1:
                yield TextChunkEvent(content="Steel and Cobalt.")
                yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
                return
            yield TextChunkEvent(content="Steel and Cobalt from query results.")
            yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=2))

        return _stream()


class HttpPathOnlyRecordRecoveryProvider:
    calls: ClassVar[list[object]] = []

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        type(self).calls.append(messages)

        async def _stream():
            from general_manager.chat.providers.base import (
                DoneEvent,
                TextChunkEvent,
                TokenUsage,
                ToolCallEvent,
            )

            if len(type(self).calls) == 1:
                yield ToolCallEvent(
                    id="path-1",
                    name="find_path",
                    args={
                        "from_manager": "SyntheticManager01",
                        "to_manager": "SyntheticManager08",
                    },
                )
                yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
                return
            if len(type(self).calls) == 2:
                yield TextChunkEvent(content="I found a path, but no records yet.")
                yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=2))
                return
            if len(type(self).calls) == 3:
                yield ToolCallEvent(
                    id="query-1",
                    name="query",
                    args={
                        "manager": "SyntheticManager08",
                        "query": {"fields": ["name"]},
                    },
                )
                yield DoneEvent(usage=TokenUsage(input_tokens=3, output_tokens=3))
                return
            yield TextChunkEvent(content="Found record: Recovered Synthetic Row.")
            yield DoneEvent(usage=TokenUsage(input_tokens=4, output_tokens=4))

        return _stream()


class HttpNoPathRecordProvider:
    calls: ClassVar[list[object]] = []

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        type(self).calls.append(list(messages))

        async def _stream():
            from general_manager.chat.providers.base import (
                DoneEvent,
                TextChunkEvent,
                TokenUsage,
                ToolCallEvent,
            )

            if len(type(self).calls) == 1:
                yield ToolCallEvent(
                    id="path-1",
                    name="find_path",
                    args={
                        "from_manager": "SourceManager",
                        "to_manager": "TargetManager",
                    },
                )
                yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
                return
            if len(type(self).calls) == 2:
                yield TextChunkEvent(
                    content="No path was found between those managers."
                )
                yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=2))
                return
            yield ToolCallEvent(
                id="query-1",
                name="query",
                args={"manager": "TargetManager", "query": {"fields": ["name"]}},
            )
            yield DoneEvent(usage=TokenUsage(input_tokens=3, output_tokens=3))

        return _stream()


class _Result:
    def __init__(self, data=None, errors=None) -> None:
        self.data = data
        self.errors = errors


class _Schema:
    def execute(self, query_text: str, context_value=None):  # type: ignore[no-untyped-def]
        del query_text, context_value
        return _Result(data={"createPart": {"success": True}})


@override_settings(
    GENERAL_MANAGER={
        "CHAT": {
            "enabled": True,
            "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
            "url": "/chat/",
            "allowed_mutations": ["createPart"],
            "confirm_mutations": ["createPart"],
        }
    }
)
class ChatHttpTransportTests(TestCase):
    def setUp(self) -> None:
        GraphQL.reset_registry()
        test_urls.urlpatterns[:] = []
        ensure_chat_http_routes()
        self.client = Client()
        self.user = get_user_model().objects.create_user(
            username="chat-user",
            email="chat@example.com",
            password="pw",  # noqa: S106
        )
        self.client.force_login(self.user)

        GraphQL._schema = _Schema()  # type: ignore[assignment]

    def tearDown(self) -> None:
        test_urls.urlpatterns[:] = []
        GraphQL.reset_registry()
        super().tearDown()

    def test_http_post_returns_complete_json_response(self) -> None:
        with patch(
            "general_manager.chat.views.import_provider",
            return_value=HttpIntegrationProvider,
        ):
            response = self.client.post(
                "/chat/",
                data=json.dumps({"text": "hello"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == "hello back"
        assert payload["events"][-1]["type"] == "done"

    def test_http_errors_use_public_message(self) -> None:
        with patch(
            "general_manager.chat.views.import_provider",
            return_value=ExplodingHttpProvider,
        ):
            response = self.client.post(
                "/chat/",
                data=json.dumps({"text": "hello"}),
                content_type="application/json",
            )

        payload = response.json()
        assert payload["events"] == [
            {
                "type": "error",
                "message": "Chat request failed.",
                "code": "chat_error",
            }
        ]

    def test_http_timeout_errors_use_generic_public_message(self) -> None:
        with patch(
            "general_manager.chat.views.import_provider",
            return_value=TimeoutHttpProvider,
        ):
            response = self.client.post(
                "/chat/",
                data=json.dumps({"text": "hello"}),
                content_type="application/json",
            )

        payload = response.json()
        assert payload["events"] == [
            {
                "type": "error",
                "message": "Chat request failed.",
                "code": "chat_error",
            }
        ]

    def test_http_invalid_json_body_returns_generic_error_event(self) -> None:
        self.client.raise_request_exception = False

        with patch("general_manager.chat.views.emit_chat_error") as chat_error:
            response = self.client.post(
                "/chat/",
                data='{"text": ',
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["events"] == [GENERIC_CHAT_ERROR_EVENT]
        assert "Expecting value" not in response.content.decode()
        chat_error.assert_called_once()
        assert isinstance(chat_error.call_args.kwargs["error"], json.JSONDecodeError)
        assert chat_error.call_args.kwargs["context"] == {
            "transport": "http",
            "path": "/chat/",
        }

    def test_permission_exceptions_return_generic_error_events(self) -> None:
        self.client.raise_request_exception = False
        permission_errors = [
            RuntimeError("secret permission detail"),
            RuntimeError("secret permission detail"),
            RuntimeError("secret permission detail"),
        ]
        remaining_errors = list(permission_errors)

        def _raise_permission(*_args: object, **_kwargs: object) -> bool:
            raise remaining_errors.pop(0)

        with (
            patch(
                "general_manager.chat.views.get_chat_permission",
                return_value=_raise_permission,
            ),
            patch("general_manager.chat.views.emit_chat_error") as chat_error,
        ):
            http_response = self.client.post(
                "/chat/",
                data=json.dumps({"text": "hello"}),
                content_type="application/json",
            )
            sse_response = self.client.post(
                "/chat/stream/",
                data=json.dumps({"text": "hello"}),
                content_type="application/json",
            )
            confirm_response = self.client.post(
                "/chat/confirm/",
                data=json.dumps({"confirmation_id": "tool-1", "confirmed": True}),
                content_type="application/json",
            )

        assert http_response.status_code == 200
        assert http_response.json()["events"] == [GENERIC_CHAT_ERROR_EVENT]
        assert "secret permission detail" not in http_response.content.decode()

        assert sse_response.status_code == 200
        sse_body = b"".join(sse_response.streaming_content).decode()
        assert sse_body == f"data: {json.dumps(GENERIC_CHAT_ERROR_EVENT)}\n\n"
        assert "secret permission detail" not in sse_body

        assert confirm_response.status_code == 200
        assert confirm_response.json()["events"] == [GENERIC_CHAT_ERROR_EVENT]
        assert "secret permission detail" not in confirm_response.content.decode()

        expected_contexts = [
            {"transport": "http", "path": "/chat/"},
            {"transport": "sse", "path": "/chat/stream/"},
            {"transport": "http_confirm", "path": "/chat/confirm/"},
        ]
        assert chat_error.call_count == 3
        for call_args, expected_error, expected_context in zip(
            chat_error.call_args_list,
            permission_errors,
            expected_contexts,
            strict=True,
        ):
            assert call_args.kwargs["error"] is expected_error
            assert call_args.kwargs["context"] == expected_context

    def test_http_missing_text_preserves_bad_message_event(self) -> None:
        response = self.client.post(
            "/chat/",
            data=json.dumps({}),
            content_type="application/json",
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["events"] == [
            {
                "type": "error",
                "message": "Message text is required.",
                "code": "bad_message",
            }
        ]

    def test_sse_setup_errors_return_generic_error_event(self) -> None:
        self.client.raise_request_exception = False
        original_error = RuntimeError("secret setup detail")

        with (
            patch(
                "general_manager.chat.views._conversation_for_request",
                side_effect=original_error,
            ),
            patch("general_manager.chat.views.emit_chat_error") as chat_error,
        ):
            response = self.client.post(
                "/chat/stream/",
                data=json.dumps({"text": "hello"}),
                content_type="application/json",
            )

            assert response.status_code == 200
            body = b"".join(response.streaming_content).decode()
            assert body == f"data: {json.dumps(GENERIC_CHAT_ERROR_EVENT)}\n\n"
            assert "secret setup detail" not in body
            chat_error.assert_called_once()
            assert chat_error.call_args.kwargs["error"] is original_error
            assert chat_error.call_args.kwargs["context"] == {
                "transport": "sse",
                "path": "/chat/stream/",
            }

    def test_sse_does_not_execute_provider_before_stream_is_consumed(self) -> None:
        LazySseProvider.started = False
        LazySseProvider.yielded = False

        with patch(
            "general_manager.chat.views.import_provider",
            return_value=LazySseProvider,
        ):
            response = self.client.post(
                "/chat/stream/",
                data=json.dumps({"text": "hello"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert LazySseProvider.started is False

        first_chunk = next(iter(response.streaming_content)).decode()

        assert '"type": "text_chunk"' in first_chunk
        assert LazySseProvider.started is True

    def test_http_post_rejects_confirmed_mutations(self) -> None:
        with patch(
            "general_manager.chat.views.import_provider",
            return_value=HttpIntegrationProvider,
        ):
            response = self.client.post(
                "/chat/",
                data=json.dumps({"text": "create a part"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["events"][-1] == {
            "type": "error",
            "message": "Confirmed mutations require WebSocket or SSE transport.",
            "code": "confirmation_required_transport",
        }

    def test_http_post_does_not_persist_user_message_when_rate_limited(self) -> None:
        with patch(
            "general_manager.chat.views.enforce_chat_rate_limit",
            return_value={"scope": "user", "retry_after_seconds": 60},
        ):
            response = self.client.post(
                "/chat/",
                data=json.dumps({"text": "hello"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["events"] == [
            {
                "type": "error",
                "message": "Chat rate limit exceeded. Try again later.",
                "code": "rate_limited",
                "retry_after_seconds": 60,
            }
        ]
        assert ChatMessage.objects.count() == 0

    def test_http_records_token_usage_without_double_counting_request(self) -> None:
        with (
            patch(
                "general_manager.chat.views.import_provider",
                return_value=HttpIntegrationProvider,
            ),
            patch(
                "general_manager.chat.views.enforce_chat_rate_limit",
                return_value=None,
            ) as limit,
        ):
            response = self.client.post(
                "/chat/",
                data=json.dumps({"text": "hello"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert limit.call_count == 2
        assert limit.call_args_list[0].kwargs == {}
        assert limit.call_args_list[1].kwargs == {
            "input_tokens": 1,
            "output_tokens": 1,
            "count_request": False,
        }

    def test_sse_and_confirm_round_trip_resume_pending_mutation(self) -> None:
        with patch(
            "general_manager.chat.views.import_provider",
            return_value=HttpIntegrationProvider,
        ):
            response = self.client.post(
                "/chat/stream/",
                data=json.dumps({"text": "create a part"}),
                content_type="application/json",
            )

            assert response.status_code == 200
            body = b"".join(response.streaming_content).decode()
            assert '"type": "confirm_mutation"' in body
            pending = (
                ChatPendingConfirmation.objects.filter(confirmation_id="tool-create")
                .order_by("id")
                .last()
            )
            assert pending is not None

            confirm = self.client.post(
                "/chat/confirm/",
                data=json.dumps(
                    {"confirmation_id": pending.confirmation_id, "confirmed": True}
                ),
                content_type="application/json",
            )

        assert confirm.status_code == 200
        payload = confirm.json()
        assert payload["events"][0]["type"] == "tool_result"
        assert payload["events"][1]["type"] == "text_chunk"
        assert payload["events"][-1]["type"] == "done"

    def test_confirm_errors_use_public_message(self) -> None:
        with patch(
            "general_manager.chat.views.import_provider",
            return_value=HttpIntegrationProvider,
        ):
            response = self.client.post(
                "/chat/stream/",
                data=json.dumps({"text": "create a part"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        body = b"".join(response.streaming_content).decode()
        assert '"type": "confirm_mutation"' in body
        pending = (
            ChatPendingConfirmation.objects.filter(confirmation_id="tool-create")
            .order_by("id")
            .last()
        )
        assert pending is not None
        original_error = RuntimeError("secret confirm detail")

        with (
            patch(
                "general_manager.chat.views.execute_chat_tool",
                side_effect=original_error,
            ),
            patch("general_manager.chat.views.emit_chat_error") as chat_error,
        ):
            confirm = self.client.post(
                "/chat/confirm/",
                data=json.dumps(
                    {"confirmation_id": pending.confirmation_id, "confirmed": True}
                ),
                content_type="application/json",
            )

        assert confirm.status_code == 200
        payload = confirm.json()
        assert payload["events"] == [
            {
                "type": "error",
                "message": "Chat request failed.",
                "code": "chat_error",
            }
        ]
        assert "secret confirm detail" not in confirm.content.decode()
        chat_error.assert_called_once()
        assert chat_error.call_args.kwargs["error"] is original_error

    def test_confirm_invalid_json_body_returns_generic_error_event(self) -> None:
        self.client.raise_request_exception = False

        with patch("general_manager.chat.views.emit_chat_error") as chat_error:
            response = self.client.post(
                "/chat/confirm/",
                data='{"confirmed": ',
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["events"] == [GENERIC_CHAT_ERROR_EVENT]
        assert "Expecting value" not in response.content.decode()
        chat_error.assert_called_once()
        assert isinstance(chat_error.call_args.kwargs["error"], json.JSONDecodeError)
        assert chat_error.call_args.kwargs["context"] == {
            "transport": "http_confirm",
            "path": "/chat/confirm/",
        }

    def test_confirm_bad_payload_preserves_bad_event(self) -> None:
        response = self.client.post(
            "/chat/confirm/",
            data=json.dumps({"confirmation_id": "tool-create"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["events"] == [
            {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
        ]

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "url": "/chat/",
                "recover_missing_tool_calls": True,
            }
        }
    )
    def test_http_chat_uses_same_missing_tool_recovery_setting(self) -> None:
        HttpMissingToolRecoveryProvider.calls = []

        with patch(
            "general_manager.chat.views.import_provider",
            return_value=HttpMissingToolRecoveryProvider,
        ):
            response = self.client.post(
                "/chat/",
                data=json.dumps({"text": "Which materials have density above 7?"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == "Steel and Cobalt from query results."
        assert {
            "type": "text_chunk",
            "content": "Steel and Cobalt.",
        } not in payload["events"]
        assert len(HttpMissingToolRecoveryProvider.calls) == 2
        recovery_messages = HttpMissingToolRecoveryProvider.calls[1]
        assert recovery_messages[-1].role == "system"
        assert "Do not answer from memory" in recovery_messages[-1].content

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "url": "/chat/",
                "recover_missing_tool_calls": True,
            }
        }
    )
    def test_http_chat_recovers_path_only_record_answer_by_requiring_query(
        self,
    ) -> None:
        HttpPathOnlyRecordRecoveryProvider.calls = []

        def execute_tool(name, args, context):  # type: ignore[no-untyped-def]
            del args, context
            if name == "find_path":
                return {
                    "path": ["synthetic01", "synthetic08"],
                    "from_manager": "SyntheticManager01",
                    "to_manager": "SyntheticManager08",
                }
            if name == "query":
                return {"rows": [{"name": "Recovered Synthetic Row"}]}
            raise AssertionError(name)

        with (
            patch(
                "general_manager.chat.views.import_provider",
                return_value=HttpPathOnlyRecordRecoveryProvider,
            ),
            patch(
                "general_manager.chat.views.execute_chat_tool",
                side_effect=execute_tool,
            ),
        ):
            response = self.client.post(
                "/chat/",
                data=json.dumps(
                    {
                        "text": (
                            "Find records in SyntheticManager08 related to the first "
                            "SyntheticManager01 item."
                        )
                    }
                ),
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert "Recovered Synthetic Row" in payload["answer"]
        assert "I found a path, but no records yet." not in payload["answer"]
        assert {
            "type": "text_chunk",
            "content": "I found a path, but no records yet.",
        } not in payload["events"]

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "url": "/chat/",
                "recover_missing_tool_calls": True,
            }
        }
    )
    def test_http_chat_does_not_recover_after_empty_find_path_result(
        self,
    ) -> None:
        HttpNoPathRecordProvider.calls = []
        tool_names: list[str] = []

        def execute_tool(name, args, context):  # type: ignore[no-untyped-def]
            del args, context
            tool_names.append(name)
            if name == "find_path":
                return {"path": []}
            if name == "query":
                return {"rows": [{"name": "Unexpected Row"}]}
            raise AssertionError(name)

        with (
            patch(
                "general_manager.chat.views.import_provider",
                return_value=HttpNoPathRecordProvider,
            ),
            patch(
                "general_manager.chat.views.execute_chat_tool",
                side_effect=execute_tool,
            ),
        ):
            response = self.client.post(
                "/chat/",
                data=json.dumps(
                    {
                        "text": (
                            "Find records in TargetManager related to SourceManager."
                        )
                    }
                ),
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert "No path was found between those managers." in payload["answer"]
        assert tool_names == ["find_path"]
        assert len(HttpNoPathRecordProvider.calls) == 2
        assert not any(
            message.role == "system"
            and "Schema and path tools are not data queries" in message.content
            for call_messages in HttpNoPathRecordProvider.calls
            for message in call_messages
        )
