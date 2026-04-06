from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from django.test.utils import override_settings

from general_manager.chat.providers import OllamaProvider
from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
)


class _FakeAsyncStream:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self._items = items
        self._index = 0

    def __aiter__(self) -> _FakeAsyncStream:
        return self

    async def __anext__(self) -> dict[str, object]:
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


class _FakeAsyncClient:
    def __init__(self, *, host: str, timeout: float) -> None:
        self.host = host
        self.timeout = timeout
        self.calls: list[dict[str, object]] = []

    async def chat(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return _FakeAsyncStream(
            [
                {"message": {"content": "Hello"}},
                {"message": {"content": " world"}},
                {"done": True, "prompt_eval_count": 3, "eval_count": 5},
            ]
        )


class _FakeToolClient(_FakeAsyncClient):
    async def chat(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return _FakeAsyncStream(
            [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "search_managers",
                                    "arguments": {"query": "parts"},
                                }
                            }
                        ],
                    }
                },
                {"done": True, "prompt_eval_count": 1, "eval_count": 1},
            ]
        )


class OllamaProviderTests(unittest.TestCase):
    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "general_manager.chat.providers.OllamaProvider",
                "provider_config": {
                    "model": "gemma4:e4b",
                    "base_url": "http://127.0.0.1:11434",
                    "timeout_seconds": 12,
                },
            }
        }
    )
    def test_complete_streams_text_and_usage(self) -> None:
        fake_client = _FakeAsyncClient(host="unused", timeout=0)

        async def run() -> None:
            with patch.object(
                OllamaProvider,
                "_build_async_client",
                return_value=fake_client,
            ):
                provider = OllamaProvider()
                events = [
                    event
                    async for event in provider.complete(
                        [Message(role="user", content="hello")], []
                    )
                ]
                assert isinstance(events[0], TextChunkEvent)
                assert events[0].content == "Hello"
                assert isinstance(events[1], TextChunkEvent)
                assert events[1].content == " world"
                assert isinstance(events[2], DoneEvent)
                assert events[2].usage.input_tokens == 3
                assert events[2].usage.output_tokens == 5
                assert fake_client.calls[0]["model"] == "gemma4:e4b"
                assert fake_client.calls[0]["messages"] == [
                    {"role": "user", "content": "hello"}
                ]
                assert fake_client.calls[0]["stream"] is True

        asyncio.run(run())

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "general_manager.chat.providers.OllamaProvider",
                "provider_config": {
                    "model": "gemma4:e4b",
                    "base_url": "http://127.0.0.1:11434",
                },
            }
        }
    )
    def test_complete_emits_tool_call_events(self) -> None:
        fake_client = _FakeToolClient(host="unused", timeout=0)

        async def run() -> None:
            with patch.object(
                OllamaProvider,
                "_build_async_client",
                return_value=fake_client,
            ):
                provider = OllamaProvider()
                events = [
                    event
                    async for event in provider.complete(
                        [Message(role="user", content="hello")], []
                    )
                ]
                assert isinstance(events[0], ToolCallEvent)
                assert events[0].name == "search_managers"
                assert events[0].args == {"query": "parts"}
                assert isinstance(events[1], DoneEvent)

        asyncio.run(run())

    def test_check_configuration_requires_ollama_package(self) -> None:
        with patch("general_manager.chat.providers.find_spec", return_value=None):
            with self.assertRaisesRegex(ImportError, "ollama package is not installed"):
                OllamaProvider.check_configuration()
