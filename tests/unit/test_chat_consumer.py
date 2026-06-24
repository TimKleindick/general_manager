from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from django.contrib.auth.models import AnonymousUser
from django.test.utils import override_settings
from django.utils import timezone

from general_manager.chat.consumer import ChatConsumer
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)


class _Session:
    def __init__(self, session_key: str | None = None) -> None:
        self.session_key = session_key
        self.saved = False

    def save(self) -> None:
        self.saved = True
        if self.session_key is None:
            self.session_key = "generated-session-key"


class _Provider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, "tools": tools})
        del tools
        yield TextChunkEvent(content=f"echo:{messages[-1].content}")
        yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=2))


class _ExplodingProvider:
    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        raise RuntimeError("secret provider stack detail")  # noqa: TRY003
        yield  # pragma: no cover


class _TimeoutProvider:
    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        raise TimeoutError("provider timed out")  # noqa: TRY003
        yield  # pragma: no cover


class _ToolLoopProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._call_count = 0

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, "tools": tools})
        if self._call_count == 0:
            self._call_count += 1
            yield ToolCallEvent(
                id="tool-1",
                name="search_managers",
                args={"query": "parts"},
            )
            yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
            return
        yield TextChunkEvent(content="final answer")
        yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=3))


class _ConfirmResumeProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, "tools": tools})
        yield TextChunkEvent(content=f"resume:{messages[-1].content}")
        yield DoneEvent(usage=TokenUsage(input_tokens=3, output_tokens=4))


class _InfiniteToolProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, "tools": tools})
        yield ToolCallEvent(
            id=f"tool-loop-{len(self.calls)}",
            name="search_managers",
            args={"query": "parts"},
        )
        yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))


class _MissingToolRecoveryProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, "tools": tools})
        if len(self.calls) == 1:
            yield TextChunkEvent(content="Steel and Cobalt.")
            yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
            return
        yield TextChunkEvent(content="Steel and Cobalt from query results.")
        yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=2))


class _EmptyAfterToolRecoveryProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, "tools": tools})
        if len(self.calls) == 1:
            yield ToolCallEvent(
                id="tool-1",
                name="find_path",
                args={"from_manager": "ProjectManager", "to_manager": "PartManager"},
            )
            yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
            return
        if len(self.calls) == 2:
            yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
            return
        yield TextChunkEvent(content="Apollo from query results.")
        yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=2))


def _deny_permission(*_args: object, **_kwargs: object) -> bool:
    return False


class ChatConsumerConnectTests(unittest.TestCase):
    def test_connect_rejects_when_permission_denied(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }

        async def run() -> None:
            with (
                patch(
                    "general_manager.chat.consumer.get_chat_permission",
                    return_value=_deny_permission,
                ),
                patch.object(consumer, "close", new_callable=AsyncMock) as mock_close,
                patch.object(consumer, "accept", new_callable=AsyncMock) as mock_accept,
            ):
                await consumer.connect()
                mock_close.assert_called_once_with(code=4403)
                mock_accept.assert_not_called()

        asyncio.run(run())

    def test_connect_closes_and_emits_error_when_permission_raises(self) -> None:
        consumer = ChatConsumer()
        user = AnonymousUser()
        consumer.scope = {
            "user": user,
            "session": _Session("existing-key"),
        }
        original_error = RuntimeError("secret permission detail")

        def raise_permission(*_args: object, **_kwargs: object) -> bool:
            raise original_error

        async def run() -> None:
            with (
                patch(
                    "general_manager.chat.consumer.get_chat_permission",
                    return_value=raise_permission,
                ),
                patch.object(consumer, "close", new_callable=AsyncMock) as mock_close,
                patch.object(consumer, "accept", new_callable=AsyncMock) as mock_accept,
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch("general_manager.chat.consumer.emit_chat_error") as chat_error,
            ):
                await consumer.connect()

            mock_close.assert_called_once_with(code=1011)
            mock_accept.assert_not_called()
            mock_send_json.assert_not_called()
            chat_error.assert_called_once_with(
                user=user,
                error=original_error,
                context={"transport": "websocket", "phase": "connect"},
            )

        asyncio.run(run())

    def test_connect_closes_and_emits_error_when_provider_import_fails(self) -> None:
        consumer = ChatConsumer()
        user = AnonymousUser()
        consumer.scope = {
            "user": user,
            "session": _Session("existing-key"),
        }
        original_error = RuntimeError("secret provider setup detail")

        async def run() -> None:
            with (
                patch(
                    "general_manager.chat.consumer.get_chat_permission",
                    return_value=None,
                ),
                patch(
                    "general_manager.chat.consumer.import_provider",
                    side_effect=original_error,
                ),
                patch.object(consumer, "close", new_callable=AsyncMock) as mock_close,
                patch.object(consumer, "accept", new_callable=AsyncMock) as mock_accept,
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch("general_manager.chat.consumer.emit_chat_error") as chat_error,
            ):
                await consumer.connect()

            mock_close.assert_called_once_with(code=1011)
            mock_accept.assert_not_called()
            mock_send_json.assert_not_called()
            chat_error.assert_called_once_with(
                user=user,
                error=original_error,
                context={"transport": "websocket", "phase": "connect"},
            )

        asyncio.run(run())

    def test_connect_creates_session_key_before_accepting(self) -> None:
        consumer = ChatConsumer()
        session = _Session()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": session,
        }

        async def run() -> None:
            with (
                patch(
                    "general_manager.chat.consumer.get_chat_permission",
                    return_value=None,
                ),
                patch.object(consumer, "accept", new_callable=AsyncMock) as mock_accept,
            ):
                await consumer.connect()
                mock_accept.assert_called_once_with()
                assert consumer.session_key == "generated-session-key"
                assert session.saved is True

        asyncio.run(run())


class ChatConsumerMessageTests(unittest.TestCase):
    def test_receive_json_streams_text_and_done_events(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _Provider()
        consumer.channel_name = "chat.test"

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.build_system_prompt",
                    return_value="system prompt text",
                ),
            ):
                await consumer.receive_json({"type": "message", "text": "hello"})
                assert mock_send_json.await_args_list[0].args[0] == {
                    "type": "text_chunk",
                    "content": "echo:hello",
                }
                assert mock_send_json.await_args_list[1].args[0] == {
                    "type": "done",
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                }
                assert consumer._history_cache is not None
                assert consumer._history_cache[-1]["content"] == "echo:hello"
                provider_messages = consumer.provider.calls[0]["messages"]
                assert provider_messages[0].role == "system"
                assert provider_messages[0].content == "system prompt text"
                assert provider_messages[-1].content == "hello"

        asyncio.run(run())

    def test_receive_json_rejects_non_object_payload(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _Provider()
        consumer.channel_name = "chat.bad-payload"

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch("general_manager.chat.consumer.emit_chat_error") as chat_error,
            ):
                await consumer.receive_json([])

            mock_send_json.assert_awaited_once_with(
                {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
            )
            chat_error.assert_not_called()
            assert consumer.provider.calls == []

        asyncio.run(run())

    def test_receive_json_rejects_unknown_event_type(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _Provider()
        consumer.channel_name = "chat.unknown-event"

        async def run() -> None:
            with patch.object(
                consumer, "send_json", new_callable=AsyncMock
            ) as mock_send_json:
                await consumer.receive_json({"type": "unknown"})

            mock_send_json.assert_awaited_once_with(
                {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
            )
            assert consumer.provider.calls == []

        asyncio.run(run())

    def test_receive_json_errors_use_public_message(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _ExplodingProvider()
        consumer.channel_name = "chat.public-error"

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.build_system_prompt",
                    return_value="system prompt text",
                ),
            ):
                await consumer.receive_json({"type": "message", "text": "hello"})

            assert mock_send_json.await_args_list[-1].args[0] == {
                "type": "error",
                "message": "Chat request failed.",
                "code": "chat_error",
            }

        asyncio.run(run())

    def test_receive_json_timeout_errors_use_generic_public_message(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _TimeoutProvider()
        consumer.channel_name = "chat.timeout-error"

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.build_system_prompt",
                    return_value="system prompt text",
                ),
            ):
                await consumer.receive_json({"type": "message", "text": "hello"})

            assert mock_send_json.await_args_list[-1].args[0] == {
                "type": "error",
                "message": "Chat request failed.",
                "code": "chat_error",
            }

        asyncio.run(run())

    def test_receive_json_setup_errors_use_public_message_and_emit_signal(
        self,
    ) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _Provider()
        consumer.channel_name = "chat.setup-error"
        original_error = RuntimeError("secret setup detail")

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.build_system_prompt",
                    side_effect=original_error,
                ),
                patch("general_manager.chat.consumer.emit_chat_error") as chat_error,
            ):
                await consumer.receive_json({"type": "message", "text": "hello"})

            error_event = mock_send_json.await_args_list[-1].args[0]
            assert error_event == {
                "type": "error",
                "message": "Chat request failed.",
                "code": "chat_error",
            }
            assert "secret setup detail" not in str(error_event)
            chat_error.assert_called_once()
            assert chat_error.call_args.kwargs["error"] is original_error
            assert chat_error.call_args.kwargs["context"] == {
                "transport": "websocket",
                "session_key": "existing-key",
            }

        asyncio.run(run())

    def test_receive_json_rejects_concurrent_messages(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _Provider()
        consumer.channel_name = "chat.test"

        async def run() -> None:
            loop = asyncio.get_running_loop()
            consumer._active_turn = loop.create_future()
            with patch.object(
                consumer, "send_json", new_callable=AsyncMock
            ) as mock_send_json:
                await consumer.receive_json({"type": "message", "text": "hello"})
                mock_send_json.assert_awaited_once_with(
                    {
                        "type": "error",
                        "message": "A chat turn is already in progress.",
                        "code": "turn_in_progress",
                    }
                )
            consumer._active_turn.cancel()

        asyncio.run(run())

    def test_receive_json_rejects_message_when_rate_limit_exceeded(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
            "client": ("127.0.0.1", 80),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _Provider()
        consumer.channel_name = "chat.rate-limit"

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.enforce_chat_rate_limit",
                    return_value={
                        "scope": "session:existing-key",
                        "retry_after_seconds": 60,
                    },
                ),
            ):
                await consumer.receive_json({"type": "message", "text": "hello"})
                assert mock_send_json.await_args_list[0].args[0] == {
                    "type": "error",
                    "message": "Chat rate limit exceeded. Try again later.",
                    "code": "rate_limited",
                    "retry_after_seconds": 60,
                }
                assert consumer.provider.calls == []

        asyncio.run(run())

    def test_receive_json_records_token_usage_without_double_counting_request(
        self,
    ) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _Provider()
        consumer.channel_name = "chat.rate-limit-tokens"

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.build_system_prompt",
                    return_value="system prompt text",
                ),
                patch(
                    "general_manager.chat.consumer.enforce_chat_rate_limit",
                    return_value=None,
                ) as limit,
            ):
                await consumer.receive_json({"type": "message", "text": "hello"})

            assert limit.call_count == 2
            assert limit.call_args_list[0].kwargs == {}
            assert limit.call_args_list[1].kwargs == {
                "input_tokens": 1,
                "output_tokens": 2,
                "count_request": False,
            }
            assert mock_send_json.await_args_list[-1].args[0] == {
                "type": "done",
                "usage": {"input_tokens": 1, "output_tokens": 2},
            }

        asyncio.run(run())

    def test_receive_json_executes_tool_calls_and_resumes_provider(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _ToolLoopProvider()
        consumer.channel_name = "chat.tools"

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.build_system_prompt",
                    return_value="system prompt text",
                ),
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    return_value=[{"manager": "PartManager"}],
                ) as execute_chat_tool,
            ):
                await consumer.receive_json({"type": "message", "text": "hello"})

                assert mock_send_json.await_args_list[0].args[0] == {
                    "type": "tool_call",
                    "id": "tool-1",
                    "name": "search_managers",
                    "args": {"query": "parts"},
                }
                assert mock_send_json.await_args_list[1].args[0] == {
                    "type": "tool_result",
                    "id": "tool-1",
                    "name": "search_managers",
                    "result": [{"manager": "PartManager"}],
                }
                assert mock_send_json.await_args_list[2].args[0] == {
                    "type": "text_chunk",
                    "content": "final answer",
                }
                assert mock_send_json.await_args_list[3].args[0] == {
                    "type": "done",
                    "usage": {"input_tokens": 2, "output_tokens": 3},
                }
                called_name, called_args, called_context = (
                    execute_chat_tool.call_args.args
                )
                assert called_name == "search_managers"
                assert called_args == {"query": "parts"}
                assert called_context.user is consumer.scope["user"]
                second_call_messages = consumer.provider.calls[1]["messages"]
                assert second_call_messages[-1].role == "tool"
                assert "PartManager" in second_call_messages[-1].content
                assert second_call_messages[-2].role == "assistant"
                assert (
                    second_call_messages[-2].content
                    == "Called tool search_managers. The next message is the tool result; answer from it exactly."
                )

        asyncio.run(run())

    def test_receive_json_stops_after_maximum_tool_retries(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _InfiniteToolProvider()
        consumer.channel_name = "chat.retry-cap"

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.build_system_prompt",
                    return_value="system prompt text",
                ),
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    return_value=[{"manager": "PartManager"}],
                ),
                patch(
                    "general_manager.chat.consumer.get_chat_settings",
                    return_value={"max_retries_per_message": 2},
                ),
            ):
                await consumer.receive_json({"type": "message", "text": "hello"})
                assert mock_send_json.await_args_list[-1].args[0] == {
                    "type": "error",
                    "message": "Chat tool retry limit exceeded.",
                    "code": "tool_retry_limit",
                }
                assert len(consumer.provider.calls) == 2

        asyncio.run(run())

    def test_receive_json_recovers_missing_tool_answer_when_setting_enabled(
        self,
    ) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _MissingToolRecoveryProvider()
        consumer.channel_name = "chat.grounding"

        async def run() -> None:
            with (
                override_settings(
                    GENERAL_MANAGER={
                        "CHAT": {
                            "recover_missing_tool_calls": True,
                        }
                    }
                ),
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.build_system_prompt",
                    return_value="system prompt text",
                ),
            ):
                await consumer.receive_json(
                    {
                        "type": "message",
                        "text": "Which materials have density above 7?",
                    }
                )

            sent_messages = [call.args[0] for call in mock_send_json.await_args_list]
            assert {
                "type": "text_chunk",
                "content": "Steel and Cobalt.",
            } not in sent_messages
            assert sent_messages[-2] == {
                "type": "text_chunk",
                "content": "Steel and Cobalt from query results.",
            }
            assert sent_messages[-1] == {
                "type": "done",
                "usage": {"input_tokens": 2, "output_tokens": 2},
            }
            assert consumer._history_cache is not None
            assert consumer._history_cache[-1]["content"] == (
                "Steel and Cobalt from query results."
            )
            assert len(consumer.provider.calls) == 2
            recovery_messages = consumer.provider.calls[1]["messages"]
            assert recovery_messages[-1].role == "system"
            assert "Do not answer from memory" in recovery_messages[-1].content

        asyncio.run(run())

    def test_receive_json_recovers_empty_response_after_tool_result(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _EmptyAfterToolRecoveryProvider()
        consumer.channel_name = "chat.empty-grounding"

        async def run() -> None:
            with (
                override_settings(
                    GENERAL_MANAGER={
                        "CHAT": {
                            "recover_missing_tool_calls": True,
                        }
                    }
                ),
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.build_system_prompt",
                    return_value="system prompt text",
                ),
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    return_value=["parts"],
                ),
            ):
                await consumer.receive_json(
                    {
                        "type": "message",
                        "text": "What projects contain parts with cobalt?",
                    }
                )

            sent_messages = [call.args[0] for call in mock_send_json.await_args_list]
            assert sent_messages[-2] == {
                "type": "text_chunk",
                "content": "Apollo from query results.",
            }
            assert sent_messages[-1] == {
                "type": "done",
                "usage": {"input_tokens": 2, "output_tokens": 2},
            }
            assert len(consumer.provider.calls) == 3
            recovery_messages = consumer.provider.calls[2]["messages"]
            assert any(
                message.role == "system"
                and "previous tool result is not a final answer" in message.content
                for message in recovery_messages
            )

        asyncio.run(run())

    def test_receive_json_emits_confirmation_event_for_confirmed_mutation_tool(
        self,
    ) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _ToolLoopProvider()
        consumer.channel_name = "chat.confirm"

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.build_system_prompt",
                    return_value="system prompt text",
                ),
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    return_value={
                        "status": "confirmation_required",
                        "mutation": "createPart",
                        "input": {"name": "Bolt"},
                    },
                ),
                patch.object(
                    consumer,
                    "_stream_provider_turn",
                    new_callable=AsyncMock,
                ) as stream_turn,
            ):
                await consumer._handle_tool_call(
                    ToolCallEvent(
                        id="tool-2",
                        name="mutate",
                        args={"mutation": "createPart", "input": {"name": "Bolt"}},
                    ),
                    [SimpleNamespace(role="system", content="system prompt text")],
                    [],
                    tool_retries=0,
                )
                assert mock_send_json.await_args_list[0].args[0]["type"] == "tool_call"
                assert mock_send_json.await_args_list[1].args[0] == {
                    "type": "confirm_mutation",
                    "id": "tool-2",
                    "mutation": "createPart",
                    "input": {"name": "Bolt"},
                }
                stream_turn.assert_not_awaited()

        asyncio.run(run())

    def test_receive_json_confirm_executes_pending_mutation_and_resumes(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _ConfirmResumeProvider()
        consumer.channel_name = "chat.resume"
        consumer._pending_confirmation = {
            "id": "tool-9",
            "mutation": "createPart",
            "input": {"name": "Bolt"},
            "messages": [SimpleNamespace(role="system", content="system prompt text")],
            "history": [],
            "expires_at": timezone.now() + timedelta(seconds=30),
        }

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    return_value={"status": "executed", "data": {"success": True}},
                ) as execute_chat_tool,
            ):
                await consumer.receive_json(
                    {"type": "confirm", "confirmation_id": "tool-9", "confirmed": True}
                )
                called_name, called_args, called_context = (
                    execute_chat_tool.call_args.args
                )
                assert called_name == "mutate"
                assert called_args == {
                    "mutation": "createPart",
                    "input": {"name": "Bolt"},
                    "confirmed": True,
                }
                assert called_context.user is consumer.scope["user"]
                assert mock_send_json.await_args_list[0].args[0] == {
                    "type": "tool_result",
                    "id": "tool-9",
                    "name": "mutate",
                    "result": {"status": "executed", "data": {"success": True}},
                }
                assert mock_send_json.await_args_list[1].args[0] == {
                    "type": "text_chunk",
                    "content": 'resume:{"data": {"success": true}, "status": "executed"}',
                }
                assert consumer._pending_confirmation is None
                assert consumer._confirmation_timeout_task is None

        asyncio.run(run())

    def test_receive_json_confirm_errors_use_public_message(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _ConfirmResumeProvider()
        consumer.channel_name = "chat.resume.error"
        consumer._pending_confirmation = {
            "id": "tool-err",
            "mutation": "createPart",
            "input": {"name": "Bolt"},
            "messages": [SimpleNamespace(role="system", content="system prompt text")],
            "history": [],
            "expires_at": timezone.now() + timedelta(seconds=30),
        }
        original_error = RuntimeError("secret confirm detail")

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    side_effect=original_error,
                ),
                patch("general_manager.chat.consumer.emit_chat_error") as chat_error,
            ):
                await consumer.receive_json(
                    {
                        "type": "confirm",
                        "confirmation_id": "tool-err",
                        "confirmed": True,
                    }
                )

            error_event = mock_send_json.await_args_list[-1].args[0]
            assert error_event == {
                "type": "error",
                "message": "Chat request failed.",
                "code": "chat_error",
            }
            assert "secret confirm detail" not in str(error_event)
            chat_error.assert_called_once()
            assert chat_error.call_args.kwargs["error"] is original_error

        asyncio.run(run())

    def test_receive_json_confirm_rejects_pending_mutation_and_resumes_with_cancellation(
        self,
    ) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _ConfirmResumeProvider()
        consumer.channel_name = "chat.resume.cancel"
        consumer._pending_confirmation = {
            "id": "tool-10",
            "mutation": "createPart",
            "input": {"name": "Bolt"},
            "messages": [SimpleNamespace(role="system", content="system prompt text")],
            "history": [],
            "expires_at": timezone.now() + timedelta(seconds=30),
        }

        async def run() -> None:
            with patch.object(
                consumer, "send_json", new_callable=AsyncMock
            ) as mock_send_json:
                await consumer.receive_json(
                    {
                        "type": "confirm",
                        "confirmation_id": "tool-10",
                        "confirmed": False,
                    }
                )
                assert mock_send_json.await_args_list[0].args[0] == {
                    "type": "tool_result",
                    "id": "tool-10",
                    "name": "mutate",
                    "result": {"status": "cancelled", "reason": "user_rejected"},
                }
                assert mock_send_json.await_args_list[1].args[0]["type"] == "text_chunk"
                assert (
                    "user_rejected"
                    in mock_send_json.await_args_list[1].args[0]["content"]
                )
                assert consumer._pending_confirmation is None
                assert consumer._confirmation_timeout_task is None

        asyncio.run(run())

    def test_load_history_prefers_database_messages_over_stale_cache(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer._history_cache = [{"role": "assistant", "content": "stale-cache"}]
        consumer.conversation = SimpleNamespace(pk=1)

        async def run() -> None:
            with patch(
                "general_manager.chat.models.get_conversation_messages",
                return_value=[SimpleNamespace(role="user", content="from-db")],
            ):
                history = await consumer._load_history()

            assert history == [{"role": "user", "content": "from-db"}]
            assert consumer._history_cache == [{"role": "user", "content": "from-db"}]

        asyncio.run(run())

    def test_pending_confirmation_times_out_and_auto_cancels(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": _Session("existing-key"),
        }
        consumer.session_key = "existing-key"
        consumer.provider = _ConfirmResumeProvider()
        consumer.channel_name = "chat.timeout"

        async def run() -> None:
            with (
                patch.object(
                    consumer, "send_json", new_callable=AsyncMock
                ) as mock_send_json,
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    return_value={
                        "status": "confirmation_required",
                        "mutation": "createPart",
                        "input": {"name": "Bolt"},
                    },
                ),
                patch(
                    "general_manager.chat.consumer.get_chat_settings",
                    return_value={
                        "confirm_timeout_seconds": 0.01,
                        "max_retries_per_message": 8,
                    },
                ),
            ):
                await consumer._handle_tool_call(
                    ToolCallEvent(
                        id="tool-timeout",
                        name="mutate",
                        args={"mutation": "createPart", "input": {"name": "Bolt"}},
                    ),
                    [SimpleNamespace(role="system", content="system prompt text")],
                    [],
                    tool_retries=0,
                )

                await asyncio.sleep(0.05)

                assert mock_send_json.await_args_list[1].args[0] == {
                    "type": "confirm_mutation",
                    "id": "tool-timeout",
                    "mutation": "createPart",
                    "input": {"name": "Bolt"},
                }
                assert mock_send_json.await_args_list[2].args[0] == {
                    "type": "tool_result",
                    "id": "tool-timeout",
                    "name": "mutate",
                    "result": {
                        "status": "cancelled",
                        "reason": "confirmation_timed_out",
                    },
                }
                assert mock_send_json.await_args_list[3].args[0]["type"] == "text_chunk"
                assert (
                    "confirmation_timed_out"
                    in mock_send_json.await_args_list[3].args[0]["content"]
                )
                assert consumer._pending_confirmation is None
                assert consumer._confirmation_timeout_task is None

        asyncio.run(run())
