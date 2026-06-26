"""Ollama chat provider."""

from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
from typing import Any
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

    @classmethod
    def check_configuration(cls) -> None:
        if find_spec("ollama") is None:
            raise OllamaDependencyImportError()
        cls._validate_base_url(cls._provider_config()["base_url"])

    @staticmethod
    def _provider_config() -> dict[str, Any]:
        settings = get_chat_settings()
        configured = settings.get("provider_config", {})
        config = dict(configured if isinstance(configured, dict) else {})
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
    ) -> dict[str, Any]:
        config = cls._provider_config()
        return {
            "model": config["model"],
            "stream": True,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
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

    @classmethod
    def _build_async_client(cls) -> Any:
        config = cls._provider_config()
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
        client = self._build_async_client()
        stream = await client.chat(**self._build_request_body(messages, tools))
        async for chunk in stream:
            message = chunk.get("message", {})
            content = message.get("content")
            if isinstance(content, str) and content:
                yield TextChunkEvent(content=content)
            tool_calls = message.get("tool_calls", [])
            if isinstance(tool_calls, list):
                for index, tool_call in enumerate(tool_calls):
                    function = tool_call.get("function", {})
                    name = function.get("name")
                    arguments = function.get("arguments", {})
                    if isinstance(name, str) and isinstance(arguments, dict):
                        yield ToolCallEvent(
                            id=f"ollama-tool-{index}",
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


__all__ = ["OllamaBaseUrlError", "OllamaDependencyImportError", "OllamaProvider"]
