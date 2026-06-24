"""Anthropic chat provider."""

from __future__ import annotations

from importlib.util import find_spec
from typing import Any

from general_manager.chat.providers._shared import (
    AsyncIterator,
    ChatEvent,
    DoneEvent,
    Message,
    StreamingToolCallBuilder,
    TextChunkEvent,
    TokenUsage,
    ToolDefinition,
    get_attr,
)
from general_manager.chat.providers.base import BaseLLMProvider
from general_manager.chat.settings import get_chat_settings


def _merge_usage(current: TokenUsage, event_usage: Any) -> TokenUsage:
    input_tokens = getattr(event_usage, "input_tokens", None)
    output_tokens = getattr(event_usage, "output_tokens", None)
    return TokenUsage(
        input_tokens=current.input_tokens
        if input_tokens is None
        else int(input_tokens),
        output_tokens=current.output_tokens
        if output_tokens is None
        else int(output_tokens),
    )


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
        tool_call_builders: dict[int, StreamingToolCallBuilder] = {}
        async for event in stream:
            event_type = getattr(event, "type", None)
            event_index = getattr(event, "index", 0)
            block_index = event_index if isinstance(event_index, int) else 0
            if event_type == "content_block_delta":
                delta = getattr(event, "delta", None)
                if getattr(delta, "type", None) == "input_json_delta":
                    builder = tool_call_builders.get(block_index)
                    if builder is not None:
                        builder.append(arguments=getattr(delta, "partial_json", None))
                else:
                    text = get_attr(event, "delta", "text")
                    if isinstance(text, str) and text:
                        yield TextChunkEvent(content=text)
            elif event_type == "content_block_start":
                block = getattr(event, "content_block", None)
                if getattr(block, "type", None) == "tool_use":
                    name = getattr(block, "name", None)
                    call_id = getattr(block, "id", None)
                    fallback_id = (
                        name
                        if isinstance(name, str) and name
                        else f"anthropic-tool-{block_index}"
                    )
                    builder = StreamingToolCallBuilder(
                        call_id=call_id
                        if isinstance(call_id, str) and call_id
                        else fallback_id
                    )
                    block_input = getattr(block, "input", None)
                    builder.append(
                        name=name,
                        arguments=block_input
                        if not (isinstance(block_input, dict) and not block_input)
                        else None,
                    )
                    tool_call_builders[block_index] = builder
            elif event_type == "content_block_stop":
                builder = tool_call_builders.pop(block_index, None)
                if builder is not None:
                    tool_event = builder.build()
                    if tool_event is not None:
                        yield tool_event
            elif event_type == "message_start":
                event_usage = get_attr(event, "message", "usage")
                if event_usage is not None:
                    usage = _merge_usage(usage, event_usage)
            elif event_type == "message_delta":
                event_usage = getattr(event, "usage", None)
                if event_usage is not None:
                    usage = _merge_usage(usage, event_usage)
        for block_index in sorted(tool_call_builders):
            event = tool_call_builders[block_index].build()
            if event is not None:
                yield event
        yield DoneEvent(usage=usage)


__all__ = ["AnthropicDependencyImportError", "AnthropicProvider"]
