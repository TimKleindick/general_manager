from __future__ import annotations

import asyncio
import json
from contextlib import suppress

import graphene
from asgiref.testing import ApplicationCommunicator
from django.test import TestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.bootstrap import ensure_chat_route
from general_manager.chat.consumer import ChatConsumer
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from tests import testing_asgi


class IntegrationProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        self.calls += 1

        async def _stream():
            last_message = messages[-1]
            if self.calls == 1 and last_message.content == "hello":
                yield TextChunkEvent(content="hello back")
                yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
                return
            if self.calls == 1 and last_message.content == "create a part":
                yield ToolCallEvent(
                    id="tool-create",
                    name="mutate",
                    args={"mutation": "createPart", "input": {"name": "Bolt"}},
                )
                yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
                return
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


class ChatTransportIntegrationTests(TestCase):
    def setUp(self) -> None:
        self._original_patterns = list(testing_asgi.websocket_urlpatterns)
        self._original_application = testing_asgi.application
        GraphQL.reset_registry()

        class Query(graphene.ObjectType):
            ping = graphene.String()

        class Mutation(graphene.ObjectType):
            createPart = graphene.Field(graphene.JSONString)

        GraphQL._schema = graphene.Schema(query=Query, mutation=Mutation)

    def tearDown(self) -> None:
        testing_asgi.websocket_urlpatterns[:] = self._original_patterns
        testing_asgi.application = self._original_application
        GraphQL.reset_registry()
        super().tearDown()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.integration.test_chat_transport.IntegrationProvider",
                "url": "/chat/",
            }
        },
        ALLOWED_HOSTS=["testserver"],
    )
    def test_websocket_chat_message_streams_final_answer(self) -> None:
        async def run_test() -> None:
            from unittest.mock import AsyncMock, patch

            with patch.object(
                ChatConsumer,
                "_get_persistent_conversation",
                new=AsyncMock(return_value=None),
            ):
                ensure_chat_route()
                communicator = await self._connect()
                await self._send_json(
                    communicator, {"type": "message", "text": "hello"}
                )

                text_event = json.loads((await communicator.receive_output())["text"])
                assert text_event == {"type": "text_chunk", "content": "hello back"}

                done_event = json.loads((await communicator.receive_output())["text"])
                assert done_event == {
                    "type": "done",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }

                await self._disconnect(communicator)

        asyncio.run(run_test())

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.integration.test_chat_transport.IntegrationProvider",
                "url": "/chat/",
                "allowed_mutations": ["createPart"],
                "confirm_mutations": ["createPart"],
            }
        },
        ALLOWED_HOSTS=["testserver"],
    )
    def test_websocket_confirm_mutation_round_trip(self) -> None:
        async def run_test() -> None:
            from unittest.mock import AsyncMock, patch

            with patch.object(
                ChatConsumer,
                "_get_persistent_conversation",
                new=AsyncMock(return_value=None),
            ):
                ensure_chat_route()
                communicator = await self._connect()

                with patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    side_effect=lambda name, args, _context: (
                        {
                            "status": "confirmation_required",
                            "mutation": "createPart",
                            "input": {"name": "Bolt"},
                        }
                        if (
                            name == "mutate"
                            and args.get("mutation") == "createPart"
                            and not args.get("confirmed", False)
                        )
                        else {"status": "executed", "data": {"success": True}}
                    ),
                ):
                    await self._send_json(
                        communicator, {"type": "message", "text": "create a part"}
                    )

                    tool_call = json.loads(
                        (await communicator.receive_output())["text"]
                    )
                    assert tool_call["type"] == "tool_call"
                    assert tool_call["name"] == "mutate"

                    confirm_event = json.loads(
                        (await communicator.receive_output())["text"]
                    )
                    assert confirm_event == {
                        "type": "confirm_mutation",
                        "id": "tool-create",
                        "mutation": "createPart",
                        "input": {"name": "Bolt"},
                    }

                    await self._send_json(
                        communicator,
                        {
                            "type": "confirm",
                            "confirmation_id": "tool-create",
                            "confirmed": True,
                        },
                    )

                    tool_result = json.loads(
                        (await communicator.receive_output())["text"]
                    )
                    assert tool_result == {
                        "type": "tool_result",
                        "id": "tool-create",
                        "name": "mutate",
                        "result": {"status": "executed", "data": {"success": True}},
                    }

                    text_event = json.loads(
                        (await communicator.receive_output())["text"]
                    )
                    assert text_event["type"] == "text_chunk"
                    assert "executed" in text_event["content"]

                    done_event = json.loads(
                        (await communicator.receive_output())["text"]
                    )
                    assert done_event == {
                        "type": "done",
                        "usage": {"input_tokens": 2, "output_tokens": 2},
                    }

                await self._disconnect(communicator)

        asyncio.run(run_test())

    async def _connect(self) -> ApplicationCommunicator:
        communicator = ApplicationCommunicator(
            testing_asgi.application,
            {
                "type": "websocket",
                "path": "/chat/",
                "headers": [
                    (b"host", b"testserver"),
                    (b"origin", b"http://testserver"),
                ],
                "query_string": b"",
                "client": ("testserver", 80),
                "server": ("testserver", 80),
                "subprotocols": [],
            },
        )
        await communicator.send_input({"type": "websocket.connect"})
        accept = await communicator.receive_output()
        assert accept["type"] == "websocket.accept"
        return communicator

    async def _send_json(
        self, communicator: ApplicationCommunicator, message: dict[str, object]
    ) -> None:
        await communicator.send_input(
            {"type": "websocket.receive", "text": json.dumps(message)}
        )

    async def _disconnect(self, communicator: ApplicationCommunicator) -> None:
        with suppress(Exception):
            await communicator.send_input(
                {"type": "websocket.disconnect", "code": 1000}
            )
        await communicator.wait()
