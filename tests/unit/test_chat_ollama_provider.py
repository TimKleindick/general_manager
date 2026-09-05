from __future__ import annotations

import asyncio
import json
from types import ModuleType
import sys
import unittest
from unittest.mock import patch

from django.test.utils import override_settings
import pytest

from general_manager.chat.providers import OllamaProvider
from general_manager.chat.providers.ollama import OllamaBaseUrlError
from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
    ToolDefinition,
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

    async def _request(self, _cls, *_args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs["json"])
        return _FakeAsyncStream(
            [
                {"message": {"content": "Hello"}},
                {"message": {"content": " world"}},
                {"done": True, "prompt_eval_count": 3, "eval_count": 5},
            ]
        )


class _FakeToolClient(_FakeAsyncClient):
    async def _request(self, _cls, *_args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs["json"])
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


class _CloseTracker:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _BlockingAsyncStream:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self._continue = asyncio.Event()
        self.closed = False

    def __aiter__(self) -> _BlockingAsyncStream:
        return self

    async def __anext__(self) -> dict[str, object]:
        self.started.set()
        await self._continue.wait()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed = True
        self._continue.set()


class _PartialAsyncStream:
    def __init__(self) -> None:
        self.closed = False
        self._yielded = False

    def __aiter__(self) -> _PartialAsyncStream:
        return self

    async def __anext__(self) -> dict[str, object]:
        if self._yielded:
            raise StopAsyncIteration
        self._yielded = True
        return {"message": {"content": "partial"}}

    async def aclose(self) -> None:
        self.closed = True


class _TrackingAsyncClient:
    def __init__(self, stream: object) -> None:
        self.stream = stream
        self._client = _CloseTracker()

    async def _request(self, _cls, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return self.stream


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
        with patch(
            "general_manager.chat.providers.ollama.find_spec", return_value=None
        ):
            with self.assertRaisesRegex(ImportError, "ollama package is not installed"):
                OllamaProvider.check_configuration()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider_config": {
                    "base_url": "ftp://ollama.local",
                }
            }
        }
    )
    def test_check_configuration_rejects_unsupported_base_url_scheme(self) -> None:
        with patch(
            "general_manager.chat.providers.ollama.find_spec", return_value=True
        ):
            with self.assertRaisesRegex(OllamaBaseUrlError, "http or https"):
                OllamaProvider.check_configuration()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider_config": {
                    "base_url": "http://:11434",
                }
            }
        }
    )
    def test_check_configuration_rejects_base_url_without_hostname(self) -> None:
        with patch(
            "general_manager.chat.providers.ollama.find_spec", return_value=True
        ):
            with self.assertRaises(OllamaBaseUrlError):
                OllamaProvider.check_configuration()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider_config": {
                    "base_url": "ftp://ollama.local",
                }
            }
        }
    )
    def test_build_async_client_rejects_unsupported_base_url_scheme(self) -> None:
        with self.assertRaises(OllamaBaseUrlError):
            OllamaProvider._build_async_client()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider_config": {
                    "base_url": "https://:443",
                }
            }
        }
    )
    def test_build_async_client_rejects_base_url_without_hostname(self) -> None:
        with self.assertRaises(OllamaBaseUrlError):
            OllamaProvider._build_async_client()

    def test_validate_base_url_rejects_http_urls_without_host(self) -> None:
        for base_url in (
            "http:ollama.local",
            "http:///ollama.local",
            "https://",
            "http:// ",
            "http://[::1",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(OllamaBaseUrlError):
                    OllamaProvider._validate_base_url(base_url)

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider_config": {
                    "base_url": "https://ollama.local/",
                    "timeout_seconds": 7,
                }
            }
        }
    )
    def test_build_async_client_strips_base_url_and_sets_timeout(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeAsyncClient:
            def __init__(self, **kwargs: object) -> None:
                calls.append(kwargs)

        ollama_module = ModuleType("ollama")
        ollama_module.AsyncClient = FakeAsyncClient  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"ollama": ollama_module}):
            client = OllamaProvider._build_async_client()

        assert isinstance(client, FakeAsyncClient)
        assert calls == [{"host": "https://ollama.local", "timeout": 7.0}]

    def test_build_request_body_includes_tool_definitions(self) -> None:
        body = OllamaProvider._build_request_body(
            [Message(role="user", content="hello")],
            [
                ToolDefinition(
                    name="query",
                    description="Run a query",
                    input_schema={"type": "object"},
                )
            ],
        )

        assert body["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "query",
                    "description": "Run a query",
                    "parameters": {"type": "object"},
                },
            }
        ]

    def test_request_body_preserves_multiple_tool_call_ids_and_results(self) -> None:
        body = OllamaProvider._build_request_body(
            [
                Message(
                    role="assistant",
                    content="",
                    tool_calls=(
                        ToolCallEvent(
                            id="call-query",
                            name="query",
                            args={"manager": "PartManager"},
                        ),
                        ToolCallEvent(
                            id="call-search",
                            name="search_managers",
                            args={"query": "parts"},
                        ),
                    ),
                ),
                Message(
                    role="tool",
                    content='{"status":"success","rows":[]}',
                    tool_call_id="call-query",
                    tool_name="query",
                    tool_result={"status": "success", "rows": []},
                ),
                Message(
                    role="tool",
                    content='{"status":"success","matches":["PartManager"]}',
                    tool_call_id="call-search",
                    tool_name="search_managers",
                    tool_result={"status": "success", "matches": ["PartManager"]},
                ),
            ],
            [],
        )

        assert body["messages"] == [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-query",
                        "type": "function",
                        "function": {
                            "name": "query",
                            "arguments": {"manager": "PartManager"},
                        },
                    },
                    {
                        "id": "call-search",
                        "type": "function",
                        "function": {
                            "name": "search_managers",
                            "arguments": {"query": "parts"},
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-query",
                "tool_name": "query",
                "content": '{"status":"success","rows":[]}',
            },
            {
                "role": "tool",
                "tool_call_id": "call-search",
                "tool_name": "search_managers",
                "content": '{"status":"success","matches":["PartManager"]}',
            },
        ]

    def test_complete_uses_raw_sdk_stream_to_preserve_native_tool_ids(self) -> None:
        pytest.importorskip("httpx")
        pytest.importorskip("ollama")
        import httpx
        import ollama

        captured_bodies: list[dict[str, object]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads((await request.aread()).decode()))
            return httpx.Response(
                200,
                content=(
                    b'{"message":{"tool_calls":[{"id":"server-call-1",'
                    b'"function":{"name":"query","arguments":'
                    b'{"manager":"PartManager"}}}]},"done":true,'
                    b'"prompt_eval_count":3,"eval_count":5}\n'
                ),
            )

        client = ollama.AsyncClient(
            host="http://ollama.test",
            transport=httpx.MockTransport(handler),
        )

        async def run() -> None:
            try:
                with patch.object(
                    OllamaProvider, "_build_async_client", return_value=client
                ):
                    events = [
                        event
                        async for event in OllamaProvider().complete(
                            [
                                Message(
                                    role="assistant",
                                    content="",
                                    tool_calls=(
                                        ToolCallEvent(
                                            id="call-query",
                                            name="query",
                                            args={"manager": "PartManager"},
                                        ),
                                    ),
                                ),
                                Message(
                                    role="tool",
                                    content='{"status":"success"}',
                                    tool_call_id="call-query",
                                    tool_name="query",
                                    tool_result={"status": "success"},
                                ),
                            ],
                            [],
                        )
                    ]
                    assert client._client.is_closed
            finally:
                await client._client.aclose()

            assert events[0] == ToolCallEvent(
                id="server-call-1",
                name="query",
                args={"manager": "PartManager"},
            )
            assert events[1] == DoneEvent(
                usage=TokenUsage(input_tokens=3, output_tokens=5)
            )

        asyncio.run(run())

        assert captured_bodies == [
            {
                "model": "gemma4:e4b",
                "stream": True,
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-query",
                                "type": "function",
                                "function": {
                                    "name": "query",
                                    "arguments": {"manager": "PartManager"},
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "content": '{"status":"success"}',
                        "tool_call_id": "call-query",
                        "tool_name": "query",
                    },
                ],
                "tools": [],
            }
        ]

    def test_complete_closes_raw_stream_and_client_when_cancelled(self) -> None:
        stream = _BlockingAsyncStream()
        client = _TrackingAsyncClient(stream)

        async def run() -> None:
            with patch.object(
                OllamaProvider, "_build_async_client", return_value=client
            ):
                events = OllamaProvider().complete(
                    [Message(role="user", content="hello")], []
                )
                next_event = asyncio.create_task(anext(events))
                await stream.started.wait()
                next_event.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await next_event
                assert stream.closed
                assert client._client.closed

        asyncio.run(run())

    def test_complete_closes_raw_stream_and_client_on_early_stop(self) -> None:
        stream = _PartialAsyncStream()
        client = _TrackingAsyncClient(stream)

        async def run() -> None:
            with patch.object(
                OllamaProvider, "_build_async_client", return_value=client
            ):
                events = OllamaProvider().complete(
                    [Message(role="user", content="hello")], []
                )
                assert await anext(events) == TextChunkEvent(content="partial")
                await events.aclose()
                assert stream.closed
                assert client._client.closed

        asyncio.run(run())

    def test_complete_uses_distinct_fallback_ids_across_chunks_and_turns(self) -> None:
        pytest.importorskip("httpx")
        pytest.importorskip("ollama")
        import httpx
        import ollama

        request_count = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                content = (
                    b'{"message":{"tool_calls":[{"function":{"name":"query",'
                    b'"arguments":{"number":1}}}]}}\n'
                    b'{"message":{"tool_calls":[{"function":{"name":"query",'
                    b'"arguments":{"number":2}}}]}}\n'
                    b'{"done":true}\n'
                )
            else:
                content = (
                    b'{"message":{"tool_calls":[{"function":{"name":"query",'
                    b'"arguments":{"number":3}}}]},"done":true}\n'
                )
            return httpx.Response(200, content=content)

        client = ollama.AsyncClient(
            host="http://ollama.test",
            transport=httpx.MockTransport(handler),
        )

        async def run() -> None:
            with patch.object(
                OllamaProvider, "_build_async_client", return_value=client
            ):
                provider = OllamaProvider()
                first_events = [
                    event
                    async for event in provider.complete(
                        [Message(role="user", content="first")], []
                    )
                ]
                assert client._client.is_closed

                # Each turn owns a fresh raw SDK client. Recreate one with the same
                # mocked transport while retaining the provider's fallback sequence.
                second_client = ollama.AsyncClient(
                    host="http://ollama.test",
                    transport=httpx.MockTransport(handler),
                )
                with patch.object(
                    OllamaProvider, "_build_async_client", return_value=second_client
                ):
                    second_events = [
                        event
                        async for event in provider.complete(
                            [Message(role="user", content="second")], []
                        )
                    ]
                assert second_client._client.is_closed

            assert [
                event.id for event in first_events if isinstance(event, ToolCallEvent)
            ] == [
                "ollama-tool-0",
                "ollama-tool-1",
            ]
            assert [
                event.id for event in second_events if isinstance(event, ToolCallEvent)
            ] == ["ollama-tool-2"]

        asyncio.run(run())
