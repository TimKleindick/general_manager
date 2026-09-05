"""Ollama chat provider."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from importlib import import_module
from importlib.util import find_spec
from inspect import isawaitable
from types import MappingProxyType
from typing import Any, Self
from urllib.parse import urlparse

from general_manager.chat.providers._shared import (
    AsyncIterator,
    ChatEvent,
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
    TokenUsage,
    ToolDefinition,
)
from general_manager.chat.providers.base import BaseLLMProvider
from general_manager.chat.settings import get_chat_settings


class OllamaDependencyImportError(ImportError):
    """Raised when the optional Ollama dependency is unavailable."""

    def __init__(self) -> None:
        super().__init__("ollama package is not installed")


class OllamaBaseUrlError(ValueError):
    """Raised when the configured Ollama base URL is unsupported or malformed."""

    def __init__(self, base_url: str) -> None:
        super().__init__(
            f"Ollama base_url must use http or https with a host: {base_url}"
        )


class OllamaProvider(BaseLLMProvider):
    """Streaming provider backed by the official Ollama Python client."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Create a provider with explicit config or legacy chat settings."""
        self._instance_provider_config = MappingProxyType(
            deepcopy(self._provider_config(config))
        )
        self._fallback_tool_call_sequence = 0

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> Self:
        """Create a provider with a copied profile configuration."""
        return cls(config)

    @property
    def provider_config(self) -> Mapping[str, Any]:
        """Return the read-only configuration for this provider instance."""
        return self._instance_provider_config

    @classmethod
    def check_configuration(cls, config: Mapping[str, Any] | None = None) -> None:
        """Validate that the Ollama SDK and base URL are usable."""
        if find_spec("ollama") is None:
            raise OllamaDependencyImportError()
        cls._validate_base_url(cls._provider_config(config)["base_url"])

    @staticmethod
    def _provider_config(configured: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if configured is None:
            settings = get_chat_settings()
            configured = settings.get("provider_config", {})
        config = dict(configured if isinstance(configured, Mapping) else {})
        config.setdefault("model", "gemma4:e4b")
        config.setdefault("base_url", "http://127.0.0.1:11434")
        config.setdefault("timeout_seconds", 60)
        return config

    @staticmethod
    def _validate_base_url(base_url: Any) -> str:
        value = str(base_url)
        try:
            parsed = urlparse(value)
            hostname = parsed.hostname
        except ValueError as exc:
            raise OllamaBaseUrlError(value) from exc
        if (
            parsed.scheme not in {"http", "https"}
            or hostname is None
            or not hostname.strip()
        ):
            raise OllamaBaseUrlError(value)
        normalized = value.rstrip("/")
        return normalized

    @classmethod
    def _build_request_body(
        cls,
        messages: list[Message],
        tools: list[ToolDefinition],
        config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = cls._provider_config(config)
        return {
            "model": config["model"],
            "stream": True,
            "messages": [cls._build_message(message) for message in messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ],
        }

    @staticmethod
    def _build_message(message: Message) -> dict[str, Any]:
        """Encode neutral tool history for the Ollama chat endpoint."""
        provider_message: dict[str, Any] = {
            "role": message.role,
            "content": message.content,
        }
        if message.tool_calls:
            provider_message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.args},
                }
                for call in message.tool_calls
            ]
        if message.role == "tool":
            if message.tool_call_id is not None:
                provider_message["tool_call_id"] = message.tool_call_id
            if message.tool_name is not None:
                provider_message["tool_name"] = message.tool_name
        return provider_message

    @classmethod
    def _build_async_client(cls, config: Mapping[str, Any] | None = None) -> Any:
        config = cls._provider_config(config)
        base_url = cls._validate_base_url(config["base_url"])
        ollama = import_module("ollama")
        return ollama.AsyncClient(
            host=base_url,
            timeout=float(config["timeout_seconds"]),
        )

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[ChatEvent]:
        """Stream Ollama text, tool calls, and usage events for one chat turn."""
        config = self.provider_config
        client = self._build_async_client(config)
        stream: Any | None = None
        try:
            stream = await client._request(
                dict,
                "POST",
                "/api/chat",
                json=self._build_request_body(messages, tools, config),
                stream=True,
            )
            async for chunk in stream:
                message = chunk.get("message", {})
                content = message.get("content")
                if isinstance(content, str) and content:
                    yield TextChunkEvent(content=content)
                tool_calls = message.get("tool_calls", [])
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        function = tool_call.get("function", {})
                        name = function.get("name")
                        arguments = function.get("arguments", {})
                        if isinstance(name, str) and isinstance(arguments, dict):
                            call_id = tool_call.get("id")
                            if not isinstance(call_id, str) or not call_id:
                                call_id = (
                                    f"ollama-tool-{self._fallback_tool_call_sequence}"
                                )
                                self._fallback_tool_call_sequence += 1
                            yield ToolCallEvent(
                                id=call_id,
                                name=name,
                                args=arguments,
                            )
                if chunk.get("done") is True:
                    yield DoneEvent(
                        usage=TokenUsage(
                            input_tokens=int(chunk.get("prompt_eval_count", 0)),
                            output_tokens=int(chunk.get("eval_count", 0)),
                        )
                    )
                    return
        finally:
            await self._close_async_resource(stream)
            await self._close_async_resource(getattr(client, "_client", None))

    @staticmethod
    async def _close_async_resource(resource: Any) -> None:
        """Close a raw SDK stream or owned client when it exposes a closer."""
        close = getattr(resource, "aclose", None)
        if callable(close):
            result = close()
            if isawaitable(result):
                await result


__all__ = ["OllamaBaseUrlError", "OllamaDependencyImportError", "OllamaProvider"]
