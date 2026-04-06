"""Anthropic chat provider."""

from __future__ import annotations

from importlib.util import find_spec
from typing import Any

from general_manager.chat.providers._shared import (
    AsyncIterator,
    ChatEvent,
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
    TokenUsage,
    ToolDefinition,
    get_attr,
)
from general_manager.chat.providers.base import BaseLLMProvider
from general_manager.chat.settings import get_chat_settings


class AnthropicDependencyImportError(ImportError):
    """Raised when the optional Anthropic dependency is unavailable."""

    def __init__(self) -> None:
        super().__init__("anthropic package is not installed")


class AnthropicProvider(BaseLLMProvider):
    """Streaming provider backed by the Anthropic Python SDK."""

    required_extra = "chat-anthropic"

    @classmethod
    def check_configuration(cls) -> None:
        if find_spec("anthropic") is None:
            raise AnthropicDependencyImportError()

    @staticmethod
    def _provider_config() -> dict[str, Any]:
        settings = get_chat_settings()
        configured = settings.get("provider_config", {})
        config = dict(configured if isinstance(configured, dict) else {})
        config.setdefault("model", "claude-3-5-haiku-latest")
        config.setdefault("max_tokens", 1024)
        config.setdefault("timeout_seconds", 60)
        return config

    @classmethod
    def _build_async_client(cls) -> Any:
        config = cls._provider_config()
        from anthropic import AsyncAnthropic  # type: ignore[import-not-found]

        kwargs: dict[str, object] = {"timeout": float(config["timeout_seconds"])}
        api_key = config.get("api_key")
        if api_key:
            kwargs["api_key"] = str(api_key)
        return AsyncAnthropic(**kwargs)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[ChatEvent]:
        client = self._build_async_client()
        config = self._provider_config()
        stream = await client.messages.create(
            model=config["model"],
            max_tokens=int(config["max_tokens"]),
            stream=True,
            messages=[
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            tools=[
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ],
        )
        usage = TokenUsage()
        async for event in stream:
            event_type = getattr(event, "type", None)
            if event_type == "content_block_delta":
                text = get_attr(event, "delta", "text")
                if isinstance(text, str) and text:
                    yield TextChunkEvent(content=text)
            elif event_type == "content_block_start":
                block = getattr(event, "content_block", None)
                if getattr(block, "type", None) == "tool_use":
                    name = getattr(block, "name", None)
                    args = getattr(block, "input", {})
                    if isinstance(name, str) and isinstance(args, dict):
                        yield ToolCallEvent(
                            id=str(getattr(block, "id", name)),
                            name=name,
                            args=args,
                        )
            elif event_type == "message_delta":
                event_usage = getattr(event, "usage", None)
                if event_usage is not None:
                    usage = TokenUsage(
                        input_tokens=int(getattr(event_usage, "input_tokens", 0)),
                        output_tokens=int(getattr(event_usage, "output_tokens", 0)),
                    )
        yield DoneEvent(usage=usage)


__all__ = ["AnthropicDependencyImportError", "AnthropicProvider"]
