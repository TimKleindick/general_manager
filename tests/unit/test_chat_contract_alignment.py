from __future__ import annotations

import asyncio
from pathlib import Path
import tomllib
import unittest
from unittest.mock import AsyncMock, patch

from django.contrib.auth.models import AnonymousUser
from django.dispatch import receiver
from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.chat.consumer import ChatConsumer
from general_manager.chat.providers.base import Message
from general_manager.chat.settings import get_chat_settings
from general_manager.chat.signals import (
    chat_error,
    chat_message_received,
    chat_mutation_executed,
    chat_tool_called,
)


class _SlowAsyncIterator:
    def __aiter__(self) -> _SlowAsyncIterator:
        return self

    async def __anext__(self) -> object:
        await asyncio.sleep(0.05)
        raise StopAsyncIteration


class _SlowProvider:
    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        return _SlowAsyncIterator()


class ChatSettingsCompatibilityTests(SimpleTestCase):
    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "rate_limit": {
                    "max_requests_per_hour": 12,
                    "max_tokens_per_hour": 345,
                },
                "query_limits": {
                    "max_results": 22,
                    "query_timeout_seconds": 7,
                    "max_retries_per_message": 4,
                },
                "conversation": {
                    "max_recent_messages": 8,
                    "summarize_after": 5,
                    "ttl_hours": 48,
                },
            }
        }
    )
    def test_get_chat_settings_accepts_documented_nested_contract(self) -> None:
        settings = get_chat_settings()

        assert settings["max_results"] == 22
        assert settings["query_timeout_seconds"] == 7
        assert settings["max_retries_per_message"] == 4
        assert settings["max_recent_messages"] == 8
        assert settings["summarize_after"] == 5
        assert settings["ttl_hours"] == 48
        assert settings["rate_limit"]["requests"] == 12
        assert settings["rate_limit"]["tokens"] == 345
        assert settings["rate_limit"]["window_seconds"] == 3600


class ChatPackagingContractTests(SimpleTestCase):
    def test_pyproject_declares_all_documented_chat_provider_extras(self) -> None:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        extras = data["project"]["optional-dependencies"]

        assert "chat-ollama" in extras
        assert "chat-openai" in extras
        assert "chat-anthropic" in extras
        assert "chat-google" in extras


class ChatSignalEmissionTests(unittest.TestCase):
    def test_receive_json_emits_message_signal_before_provider_turn(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": None,
            "client": ("127.0.0.1", 80),
        }
        consumer.session_key = "signal-session"
        consumer.provider = _SlowProvider()
        consumer.channel_name = "chat.signal.message"

        seen: list[dict[str, object]] = []

        @receiver(chat_message_received)
        def _handle_signal(sender, **kwargs):  # type: ignore[no-untyped-def]
            del sender
            seen.append(kwargs)

        async def run() -> None:
            with (
                patch.object(consumer, "send_json", new_callable=AsyncMock),
                patch(
                    "general_manager.chat.consumer.build_system_prompt",
                    return_value="system prompt text",
                ),
                patch(
                    "general_manager.chat.consumer._iter_provider_events",
                    side_effect=TimeoutError("provider timeout"),
                ),
            ):
                await consumer.receive_json({"type": "message", "text": "hello"})

        try:
            asyncio.run(run())
        finally:
            chat_message_received.disconnect(_handle_signal)

        assert seen
        assert seen[0]["message"] == "hello"
        assert seen[0]["conversation_id"] is None

    def test_handle_tool_call_emits_tool_and_mutation_signals(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": None,
        }
        consumer.session_key = "signal-session"

        tool_calls: list[dict[str, object]] = []
        mutations: list[dict[str, object]] = []

        @receiver(chat_tool_called)
        def _handle_tool(sender, **kwargs):  # type: ignore[no-untyped-def]
            del sender
            tool_calls.append(kwargs)

        @receiver(chat_mutation_executed)
        def _handle_mutation(sender, **kwargs):  # type: ignore[no-untyped-def]
            del sender
            mutations.append(kwargs)

        async def run() -> None:
            from general_manager.chat.providers.base import ToolCallEvent

            with (
                patch.object(consumer, "send_json", new_callable=AsyncMock),
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    return_value={"status": "executed", "data": {"success": True}},
                ),
                patch.object(consumer, "_stream_provider_turn", new_callable=AsyncMock),
            ):
                await consumer._handle_tool_call(
                    ToolCallEvent(
                        id="tool-1",
                        name="mutate",
                        args={"mutation": "createPart", "input": {"name": "Bolt"}},
                    ),
                    [Message(role="system", content="system prompt text")],
                    [],
                    tool_retries=0,
                )

        try:
            asyncio.run(run())
        finally:
            chat_tool_called.disconnect(_handle_tool)
            chat_mutation_executed.disconnect(_handle_mutation)

        assert tool_calls
        assert tool_calls[0]["tool_name"] == "mutate"
        assert mutations
        assert mutations[0]["mutation"] == "createPart"

    def test_receive_json_emits_error_signal_on_provider_failure(self) -> None:
        consumer = ChatConsumer()
        consumer.scope = {
            "user": AnonymousUser(),
            "session": None,
            "client": ("127.0.0.1", 80),
        }
        consumer.session_key = "signal-session"
        consumer.provider = _SlowProvider()
        consumer.channel_name = "chat.signal.error"

        errors: list[dict[str, object]] = []

        @receiver(chat_error)
        def _handle_error(sender, **kwargs):  # type: ignore[no-untyped-def]
            del sender
            errors.append(kwargs)

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
                    "general_manager.chat.consumer._iter_provider_events",
                    side_effect=TimeoutError("provider timeout"),
                ),
            ):
                await consumer.receive_json({"type": "message", "text": "hello"})
                assert mock_send_json.await_args_list[-1].args[0] == {
                    "type": "error",
                    "message": "provider timeout",
                    "code": "chat_error",
                }

        try:
            asyncio.run(run())
        finally:
            chat_error.disconnect(_handle_error)

        assert errors
        assert "provider timeout" in str(errors[0]["error"])


class ChatConsumerDisconnectTests(unittest.TestCase):
    def test_disconnect_cancels_inflight_provider_task(self) -> None:
        consumer = ChatConsumer()

        async def run() -> None:
            consumer._provider_task = asyncio.create_task(asyncio.sleep(10))
            await consumer.disconnect(1000)
            await asyncio.sleep(0)
            assert consumer._provider_task.cancelled() is True

        asyncio.run(run())
