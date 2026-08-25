"""Google GenAI chat providers."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from importlib.util import find_spec
from types import MappingProxyType
from typing import Any, Self

from general_manager.chat.providers._shared import (
    AsyncIterator,
    ChatEvent,
    DoneEvent,
    Message,
    parse_tool_arguments,
    TextChunkEvent,
    ToolCallEvent,
    TokenUsage,
    ToolDefinition,
)
from general_manager.chat.providers.base import BaseLLMProvider
from general_manager.chat.settings import get_chat_settings


class GoogleDependencyImportError(ImportError):
    """Raised when the optional Google GenAI dependency is unavailable."""

    def __init__(self) -> None:
        super().__init__("google-genai package is not installed")


def _get_call_value(tool_call: Any, key: str, default: Any = None) -> Any:
    if isinstance(tool_call, dict):
        return tool_call.get(key, default)
    return getattr(tool_call, key, default)


def _normalize_tool_calls(chunk: Any) -> list[Any]:
    function_calls = getattr(chunk, "function_calls", None)
    if isinstance(function_calls, list):
        return function_calls
    tool_calls = getattr(chunk, "tool_calls", None)
    return tool_calls if isinstance(tool_calls, list) else []


class GeminiProvider(BaseLLMProvider):
    """Streaming provider backed by the Google GenAI Python SDK."""

    required_extra = "chat-google"

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Create a provider with explicit config or legacy chat settings."""
        self._instance_provider_config = MappingProxyType(
            deepcopy(self._provider_config(config))
        )

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
        """Validate that the Google GenAI SDK is available before use."""
        del config
        if find_spec("google.genai") is None:
            raise GoogleDependencyImportError()

    @staticmethod
    def _provider_config(configured: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if configured is None:
            settings = get_chat_settings()
            configured = settings.get("provider_config", {})
        config = dict(configured if isinstance(configured, Mapping) else {})
        config.setdefault("model", "gemini-2.5-flash")
        return config

    @classmethod
    def _build_async_client(cls, config: Mapping[str, Any] | None = None) -> Any:
        config = cls._provider_config(config)
        from google.genai import Client  # type: ignore[import-not-found]

        kwargs: dict[str, Any] = {}
        api_key = config.get("api_key")
        if api_key:
            kwargs["api_key"] = str(api_key)
        return Client(**kwargs)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[ChatEvent]:
        """Stream Gemini text, tool calls, and usage events for one chat turn."""
        config = self.provider_config
        client = self._build_async_client(config)
        stream = await client.aio.models.generate_content_stream(
            model=config["model"],
            contents=[
                {"role": message.role, "parts": [{"text": message.content}]}
                for message in messages
            ],
            config={
                "tools": [
                    {
                        "function_declarations": [
                            {
                                "name": tool.name,
                                "description": tool.description,
                                "parameters": tool.input_schema,
                            }
                        ]
                    }
                    for tool in tools
                ]
            },
        )
        usage = TokenUsage()
        async for chunk in stream:
            text = getattr(chunk, "text", None)
            if isinstance(text, str) and text:
                yield TextChunkEvent(content=text)
            for index, tool_call in enumerate(_normalize_tool_calls(chunk)):
                name = _get_call_value(tool_call, "name")
                args = parse_tool_arguments(_get_call_value(tool_call, "args", {}))
                if isinstance(name, str):
                    yield ToolCallEvent(
                        id=f"gemini-tool-{index}",
                        name=name,
                        args=args,
                    )
            usage_metadata = getattr(chunk, "usage_metadata", None)
            if usage_metadata is not None:
                usage = TokenUsage(
                    input_tokens=int(getattr(usage_metadata, "prompt_token_count", 0)),
                    output_tokens=int(
                        getattr(usage_metadata, "candidates_token_count", 0)
                    ),
                )
        yield DoneEvent(usage=usage)


GoogleProvider = GeminiProvider

__all__ = ["GeminiProvider", "GoogleDependencyImportError", "GoogleProvider"]
