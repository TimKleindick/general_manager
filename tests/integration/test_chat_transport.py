from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

import graphene
from asgiref.testing import ApplicationCommunicator
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.contrib.sessions.models import Session
from django.test import TransactionTestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.bootstrap import ensure_chat_route
from general_manager.chat.consumer import ChatConsumer
from general_manager.chat.models import (
    ChatConversation,
    append_chat_message,
    create_pending_confirmation,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from general_manager.chat.tools import ScopeChatContext
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


class ConfirmedMutationIntegrationProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        self.calls += 1

        async def _stream():
            if self.calls == 1:
                yield ToolCallEvent(
                    id="tool-provider-confirmed",
                    name="mutate",
                    args={
                        "mutation": "createPart",
                        "input": {"name": "Bolt"},
                        "confirmed": True,
                    },
                )
                yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
                return
            yield TextChunkEvent(content=f"confirmed:{messages[-1].content}")
            yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=2))

        return _stream()


class ReconnectConfirmationIntegrationProvider:
    requests: ClassVar[list[list[object]]] = []

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        self.requests.append(list(messages))

        async def _stream():
            yield TextChunkEvent(content="reconnect follow-up")
            yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=2))

        return _stream()


class SummaryContextIntegrationProvider:
    provider_config: ClassVar[dict[str, int]] = {"timeout_seconds": 1}

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools

        async def _stream():
            if messages[0].content.startswith("Summarize the prior conversation"):
                yield TextChunkEvent(content="summary")
                yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=2))
                return
            has_summary = any(
                message.role == "system" and message.content == "summary"
                for message in messages
            )
            yield TextChunkEvent(
                content="answer with summary" if has_summary else "answer"
            )
            yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))

        return _stream()


class _Result:
    def __init__(self, data=None, errors=None) -> None:
        self.data = data
        self.errors = errors


class _Schema:
    def execute(self, query_text: str, context_value=None):  # type: ignore[no-untyped-def]
        del query_text, context_value
        return _Result(data={"createPart": {"success": True}})


class ChatTransportIntegrationTests(TransactionTestCase):
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
            }
        },
        ALLOWED_HOSTS=["testserver"],
    )
    def test_websocket_streams_same_planned_read_contract_as_http_and_sse(self) -> None:
        """Bypassing the neutral iterator would omit planned task ownership metadata."""
        expected_events = [
            {"type": "tool_call", "task_id": "task_1", "id": "call_1"},
            {"type": "tool_result", "task_id": "task_1", "id": "call_1"},
            {"type": "text_chunk", "content": "planned synthesis"},
            {
                "type": "done",
                "usage": {"input_tokens": 3, "output_tokens": 4},
                "orchestration": {"status": "complete"},
            },
        ]

        async def prepare(*_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(mutation_plan=None)

        async def stream(*_args: object, **_kwargs: object):
            for event in expected_events:
                yield event

        async def run_test() -> None:
            from unittest.mock import AsyncMock, patch

            with (
                patch.object(
                    ChatConsumer,
                    "_get_persistent_conversation",
                    new=AsyncMock(return_value=None),
                ),
                patch(
                    "general_manager.chat.consumer.get_planned_chat_settings",
                    return_value=SimpleNamespace(enabled=True),
                ),
                patch(
                    "general_manager.chat.consumer.prepare_planned_turn", new=prepare
                ),
                patch(
                    "general_manager.chat.consumer.iter_planned_read_events",
                    new=stream,
                ),
            ):
                ensure_chat_route()
                communicator = await self._connect()
                await self._send_json(
                    communicator, {"type": "message", "text": "show parts"}
                )
                events = [
                    json.loads((await communicator.receive_output())["text"])
                    for _ in expected_events
                ]
                await self._disconnect(communicator)

            assert events == expected_events
            assert [event["type"] for event in events][-2:] == ["text_chunk", "done"]
            assert all("task_id" in event for event in events[:2])

        asyncio.run(run_test())

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
    def test_websocket_disconnect_cancels_active_planned_iterator(self) -> None:
        """Awaiting the iterator in receive_json prevents disconnect dispatch."""
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def prepare(*_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(mutation_plan=None)

        async def stream(*_args: object, **_kwargs: object):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            yield {"type": "done"}

        async def run_test() -> None:
            from unittest.mock import AsyncMock, patch

            communicator: ApplicationCommunicator | None = None
            try:
                with (
                    patch.object(
                        ChatConsumer,
                        "_get_persistent_conversation",
                        new=AsyncMock(return_value=None),
                    ),
                    patch(
                        "general_manager.chat.consumer.get_planned_chat_settings",
                        return_value=SimpleNamespace(enabled=True),
                    ),
                    patch(
                        "general_manager.chat.consumer.prepare_planned_turn",
                        new=prepare,
                    ),
                    patch(
                        "general_manager.chat.consumer.iter_planned_read_events",
                        new=stream,
                    ),
                ):
                    ensure_chat_route()
                    communicator = await self._connect()
                    await self._send_json(
                        communicator, {"type": "message", "text": "show parts"}
                    )
                    await asyncio.wait_for(entered.wait(), timeout=1)
                    await communicator.send_input(
                        {"type": "websocket.disconnect", "code": 1000}
                    )
                    await asyncio.wait_for(cancelled.wait(), timeout=0.2)
                    await communicator.wait()
            finally:
                if communicator is not None:
                    communicator.stop()

            assert cancelled.is_set()

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
    def test_websocket_planned_mutation_preserves_confirmation_flow(self) -> None:
        """A planned mutation must still be handled by the legacy confirmation loop."""

        async def prepare(*_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(mutation_plan=object())

        async def run_test() -> None:
            from unittest.mock import AsyncMock, patch

            with (
                patch.object(
                    ChatConsumer,
                    "_get_persistent_conversation",
                    new=AsyncMock(return_value=None),
                ),
                patch(
                    "general_manager.chat.consumer.get_planned_chat_settings",
                    return_value=SimpleNamespace(enabled=True),
                ),
                patch(
                    "general_manager.chat.consumer.prepare_planned_turn", new=prepare
                ),
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    return_value={
                        "status": "confirmation_required",
                        "mutation": "createPart",
                        "input": {"name": "Bolt"},
                    },
                ),
            ):
                ensure_chat_route()
                communicator = await self._connect()
                await self._send_json(
                    communicator, {"type": "message", "text": "create a part"}
                )
                events = [
                    json.loads((await communicator.receive_output())["text"])
                    for _ in range(2)
                ]
                await self._disconnect(communicator)

            assert [event["type"] for event in events] == [
                "tool_call",
                "confirm_mutation",
            ]
            assert all("orchestration" not in event for event in events)

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

                with (
                    patch(
                        "general_manager.chat.consumer.execute_chat_tool",
                        return_value={
                            "status": "confirmation_required",
                            "mutation": "createPart",
                            "input": {"name": "Bolt"},
                        },
                    ),
                    patch(
                        "general_manager.chat.consumer.execute_confirmed_chat_mutation",
                        return_value={"status": "executed", "data": {"success": True}},
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

                    await self._send_json(
                        communicator,
                        {
                            "type": "confirm",
                            "confirmation_id": "tool-create",
                            "confirmed": True,
                        },
                    )
                    assert json.loads(
                        (await communicator.receive_output())["text"]
                    ) == {
                        "type": "error",
                        "message": "Unknown chat event.",
                        "code": "bad_event",
                    }

                await self._disconnect(communicator)

        asyncio.run(run_test())

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": (
                    "tests.integration.test_chat_transport."
                    "ConfirmedMutationIntegrationProvider"
                ),
                "url": "/chat/",
                "allowed_mutations": ["createPart"],
                "confirm_mutations": ["createPart"],
            }
        },
        ALLOWED_HOSTS=["testserver"],
    )
    def test_websocket_provider_confirmation_argument_stays_pending(self) -> None:
        async def run_test() -> None:
            from unittest.mock import AsyncMock, patch

            with (
                patch.object(
                    ChatConsumer,
                    "_get_persistent_conversation",
                    new=AsyncMock(return_value=None),
                ),
                patch.object(
                    ScopeChatContext,
                    "from_scope",
                    return_value=SimpleNamespace(
                        user=SimpleNamespace(is_authenticated=True)
                    ),
                ),
            ):
                ensure_chat_route()
                communicator = await self._connect()
                await self._send_json(
                    communicator, {"type": "message", "text": "create a part"}
                )

                tool_call = json.loads((await communicator.receive_output())["text"])
                confirmation = json.loads((await communicator.receive_output())["text"])

                assert tool_call["type"] == "tool_call"
                assert tool_call["args"]["confirmed"] is True
                assert confirmation == {
                    "type": "confirm_mutation",
                    "id": "tool-provider-confirmed",
                    "mutation": "createPart",
                    "input": {"name": "Bolt"},
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
    def test_websocket_reconnect_preserves_pending_tool_exchange(self) -> None:
        for sequence, confirmed in enumerate((True, False)):
            with self.subTest(confirmed=confirmed):
                ReconnectConfirmationIntegrationProvider.requests.clear()
                session = SessionStore()
                session["_gm_chat_session"] = f"pending-{sequence}"
                session.save()
                conversation = ChatConversation.for_actor(
                    user=None, session_key=session.session_key
                )
                append_chat_message(conversation, role="user", content="create a part")
                append_chat_message(
                    conversation,
                    role="assistant",
                    tool_calls=[
                        {
                            "id": "reconnect-confirm",
                            "name": "mutate",
                            "args": {
                                "mutation": "createPart",
                                "input": {"name": "Bolt"},
                            },
                        }
                    ],
                )
                create_pending_confirmation(
                    conversation,
                    confirmation_id="reconnect-confirm",
                    mutation_name="createPart",
                    payload={"input": {"name": "Bolt"}},
                    timeout_seconds=30,
                )

                async def run_test(session_key: str, is_confirmed: bool) -> None:
                    ensure_chat_route()
                    cookie = f"sessionid={session_key}".encode()
                    with (
                        patch(
                            "general_manager.chat.consumer.execute_confirmed_chat_mutation",
                            return_value={"status": "executed"},
                        ) as execute_mutation,
                        patch(
                            "general_manager.chat.consumer.import_provider",
                            return_value=ReconnectConfirmationIntegrationProvider,
                        ),
                    ):
                        first = await self._connect(cookie=cookie)
                        await self._send_json(
                            first,
                            {
                                "type": "confirm",
                                "confirmation_id": "reconnect-confirm",
                                "confirmed": is_confirmed,
                            },
                        )
                        assert (
                            json.loads((await first.receive_output())["text"])["type"]
                            == "tool_result"
                        )
                        assert (
                            json.loads((await first.receive_output())["text"])["type"]
                            == "text_chunk"
                        )
                        assert (
                            json.loads((await first.receive_output())["text"])["type"]
                            == "done"
                        )
                        if is_confirmed:
                            execute_mutation.assert_called_once()
                        else:
                            execute_mutation.assert_not_called()
                        await self._disconnect(first)

                        replay = await self._connect(cookie=cookie)
                        await self._send_json(
                            replay,
                            {
                                "type": "confirm",
                                "confirmation_id": "reconnect-confirm",
                                "confirmed": is_confirmed,
                            },
                        )
                        assert (
                            json.loads((await replay.receive_output())["text"])["code"]
                            == "bad_event"
                        )
                        await self._disconnect(replay)

                asyncio.run(run_test(session.session_key, confirmed))

                messages = ReconnectConfirmationIntegrationProvider.requests[-1]
                declarations = [
                    message
                    for message in messages
                    if getattr(message, "tool_calls", ())
                ]
                results = [
                    message
                    for message in messages
                    if getattr(message, "role", None) == "tool"
                ]
                assert len(declarations) == 1
                assert len(results) == 1
                declaration = declarations[0]
                result = results[0]
                assert declaration.tool_calls[0].id == "reconnect-confirm"
                assert result.tool_call_id == declaration.tool_calls[0].id

    @override_settings(
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.integration.test_chat_transport.IntegrationProvider",
                "url": "/chat/",
            }
        },
        ALLOWED_HOSTS=["testserver"],
    )
    def test_websocket_connect_persists_database_session_and_conversation(self) -> None:
        async def run_test() -> None:
            ensure_chat_route()
            communicator = await self._connect()
            await self._disconnect(communicator)

        asyncio.run(run_test())

        assert Session.objects.count() == 1
        conversation = ChatConversation.objects.get()
        assert conversation.session_key == Session.objects.get().session_key

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": (
                    "tests.integration.test_chat_transport."
                    "SummaryContextIntegrationProvider"
                ),
                "url": "/chat/",
                "max_recent_messages": 2,
                "summarize_after": 2,
            }
        },
        ALLOWED_HOSTS=["testserver"],
    )
    def test_websocket_uses_shared_summary_preparation_for_long_history(self) -> None:
        session = SessionStore()
        session["_gm_chat_session"] = True
        session.save()
        conversation = ChatConversation.for_actor(
            user=None, session_key=session.session_key
        )
        append_chat_message(conversation, role="user", content="old question")
        append_chat_message(conversation, role="assistant", content="old answer")
        append_chat_message(conversation, role="user", content="newer question")

        async def run_test() -> None:
            ensure_chat_route()
            communicator = await self._connect(
                cookie=f"sessionid={session.session_key}".encode()
            )
            await self._send_json(communicator, {"type": "message", "text": "latest"})
            assert json.loads((await communicator.receive_output())["text"]) == {
                "type": "text_chunk",
                "content": "answer with summary",
            }
            assert (
                json.loads((await communicator.receive_output())["text"])["type"]
                == "done"
            )
            await self._disconnect(communicator)

        asyncio.run(run_test())
        conversation.refresh_from_db()
        assert conversation.summarized_through_id is not None
        assert conversation.summary_text == "summary"

    async def _connect(self, *, cookie: bytes | None = None) -> ApplicationCommunicator:
        headers = [
            (b"host", b"testserver"),
            (b"origin", b"http://testserver"),
        ]
        if cookie is not None:
            headers.append((b"cookie", cookie))
        communicator = ApplicationCommunicator(
            testing_asgi.application,
            {
                "type": "websocket",
                "path": "/chat/",
                "headers": headers,
                "query_string": b"",
                "client": ("testserver", 80),
                "server": ("testserver", 80),
                "subprotocols": [],
            },
        )
        await communicator.send_input({"type": "websocket.connect"})
        accept = await communicator.receive_output()
        assert accept["type"] == "websocket.accept", accept
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
