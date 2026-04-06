from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.bootstrap import ensure_chat_http_routes
from general_manager.chat.models import ChatMessage, ChatPendingConfirmation
from tests import test_urls


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
