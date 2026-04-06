from __future__ import annotations

import asyncio
from importlib import import_module
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from django.test.utils import override_settings

from general_manager.chat.providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
)


class _AsyncIterator:
    def __init__(self, items: list[object]) -> None:
        self._items = items
        self._index = 0

    def __aiter__(self) -> _AsyncIterator:
        return self

    async def __anext__(self) -> object:
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


class _OpenAIChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return _AsyncIterator(
            [
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content=None,
                                tool_calls=[
                                    SimpleNamespace(
                                        function=SimpleNamespace(
                                            name="query",
                                            arguments='{"manager":"PartManager"}',
                                        )
                                    )
                                ],
                            )
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[],
                    usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5),
                ),
            ]
        )


class _OpenAIClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_OpenAIChatCompletions())


class _AnthropicMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return _AsyncIterator(
            [
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(text="Hello"),
                ),
                SimpleNamespace(
                    type="content_block_start",
                    content_block=SimpleNamespace(
                        type="tool_use",
                        id="tool-1",
                        name="search_managers",
                        input={"query": "parts"},
                    ),
                ),
                SimpleNamespace(
                    type="message_delta",
                    usage=SimpleNamespace(input_tokens=7, output_tokens=11),
                ),
            ]
        )


class _AnthropicClient:
    def __init__(self) -> None:
        self.messages = _AnthropicMessages()


class _GeminiModels:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def generate_content_stream(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return _AsyncIterator(
            [
                SimpleNamespace(text="Hello"),
                SimpleNamespace(
                    text=None,
                    tool_calls=[{"name": "find_path", "args": {"from_manager": "A"}}],
                ),
                SimpleNamespace(
                    text=None,
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=2,
                        candidates_token_count=4,
                    ),
                ),
            ]
        )


class _GeminiClient:
    def __init__(self) -> None:
        self.aio = SimpleNamespace(models=_GeminiModels())


class AdditionalProviderTests(unittest.TestCase):
    def test_provider_modules_export_same_public_classes(self) -> None:
        ollama_module = import_module("general_manager.chat.providers.ollama")
        openai_module = import_module("general_manager.chat.providers.openai")
        anthropic_module = import_module("general_manager.chat.providers.anthropic")
        google_module = import_module("general_manager.chat.providers.google")

        from general_manager.chat import providers

        assert providers.OllamaProvider is ollama_module.OllamaProvider
        assert providers.OpenAIProvider is openai_module.OpenAIProvider
        assert providers.AnthropicProvider is anthropic_module.AnthropicProvider
        assert providers.GeminiProvider is google_module.GeminiProvider
        assert providers.GoogleProvider is google_module.GoogleProvider

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider_config": {"model": "gpt-4.1-mini"},
            }
        }
    )
    def test_openai_provider_streams_text_tool_calls_and_usage(self) -> None:
        client = _OpenAIClient()

        async def run() -> None:
            with patch.object(
                OpenAIProvider, "_build_async_client", return_value=client
            ):
                provider = OpenAIProvider()
                events = [
                    event
                    async for event in provider.complete(
                        [Message(role="user", content="hi")], []
                    )
                ]
                assert isinstance(events[0], TextChunkEvent)
                assert events[0].content == "Hello"
                assert isinstance(events[1], ToolCallEvent)
                assert events[1].name == "query"
                assert events[1].args == {"manager": "PartManager"}
                assert isinstance(events[2], DoneEvent)
                assert events[2].usage.input_tokens == 3
                assert events[2].usage.output_tokens == 5

        asyncio.run(run())

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider_config": {"model": "claude-3-5-haiku-latest"},
            }
        }
    )
    def test_anthropic_provider_streams_text_tool_calls_and_usage(self) -> None:
        client = _AnthropicClient()

        async def run() -> None:
            with patch.object(
                AnthropicProvider, "_build_async_client", return_value=client
            ):
                provider = AnthropicProvider()
                events = [
                    event
                    async for event in provider.complete(
                        [Message(role="user", content="hi")], []
                    )
                ]
                assert isinstance(events[0], TextChunkEvent)
                assert events[0].content == "Hello"
                assert isinstance(events[1], ToolCallEvent)
                assert events[1].name == "search_managers"
                assert events[1].args == {"query": "parts"}
                assert isinstance(events[2], DoneEvent)
                assert events[2].usage.input_tokens == 7
                assert events[2].usage.output_tokens == 11

        asyncio.run(run())

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider_config": {"model": "gemini-2.5-flash"},
            }
        }
    )
    def test_gemini_provider_streams_text_tool_calls_and_usage(self) -> None:
        client = _GeminiClient()

        async def run() -> None:
            with patch.object(
                GeminiProvider, "_build_async_client", return_value=client
            ):
                provider = GeminiProvider()
                events = [
                    event
                    async for event in provider.complete(
                        [Message(role="user", content="hi")], []
                    )
                ]
                assert isinstance(events[0], TextChunkEvent)
                assert events[0].content == "Hello"
                assert isinstance(events[1], ToolCallEvent)
                assert events[1].name == "find_path"
                assert events[1].args == {"from_manager": "A"}
                assert isinstance(events[2], DoneEvent)
                assert events[2].usage.input_tokens == 2
                assert events[2].usage.output_tokens == 4

        asyncio.run(run())
