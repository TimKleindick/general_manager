"""OpenAI chat provider."""

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


class OpenAIDependencyImportError(ImportError):
    """Raised when the optional OpenAI dependency is unavailable."""

    def __init__(self) -> None:
        super().__init__("openai package is not installed")


class OpenAIProvider(BaseLLMProvider):
    """Streaming provider backed by the OpenAI Python SDK."""

    required_extra = "chat-openai"

    @classmethod
    def check_configuration(cls) -> None:
        if find_spec("openai") is None:
            raise OpenAIDependencyImportError()

    @staticmethod
    def _provider_config() -> dict[str, Any]:
        settings = get_chat_settings()
        configured = settings.get("provider_config", {})
        config = dict(configured if isinstance(configured, dict) else {})
        config.setdefault("model", "gpt-4.1-mini")
        config.setdefault("timeout_seconds", 60)
        return config

    @classmethod
    def _build_async_client(cls) -> Any:
        config = cls._provider_config()
        from openai import AsyncOpenAI  # type: ignore[import-not-found]

        kwargs: dict[str, object] = {"timeout": float(config["timeout_seconds"])}
        base_url = config.get("base_url")
        if base_url:
            kwargs["base_url"] = str(base_url)
        api_key = config.get("api_key")
        if api_key:
            kwargs["api_key"] = str(api_key)
        return AsyncOpenAI(**kwargs)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[ChatEvent]:
        client = self._build_async_client()
        config = self._provider_config()
        stream = await client.chat.completions.create(
            model=config["model"],
            stream=True,
            stream_options={"include_usage": True},
            messages=[
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            tools=[
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
        )
        usage = TokenUsage()
        tool_call_builders: dict[tuple[int, int], StreamingToolCallBuilder] = {}
        async for chunk in stream:
            content = get_attr(chunk, "choices")
            if isinstance(content, list):
                for index, choice in enumerate(content):
                    choice_index = getattr(choice, "index", index)
                    if not isinstance(choice_index, int):
                        choice_index = index
                    delta = getattr(choice, "delta", None)
                    text = getattr(delta, "content", None)
                    if isinstance(text, str) and text:
                        yield TextChunkEvent(content=text)
                    tool_calls = getattr(delta, "tool_calls", None)
                    if isinstance(tool_calls, list):
                        for tool_index, tool_call in enumerate(tool_calls):
                            call_index = getattr(tool_call, "index", tool_index)
                            if not isinstance(call_index, int):
                                call_index = tool_index
                            key = (choice_index, call_index)
                            call_id = getattr(tool_call, "id", None)
                            builder = tool_call_builders.get(key)
                            if builder is None:
                                fallback_id = f"openai-tool-{choice_index}-{call_index}"
                                builder = StreamingToolCallBuilder(
                                    call_id=call_id
                                    if isinstance(call_id, str) and call_id
                                    else fallback_id
                                )
                                tool_call_builders[key] = builder
                            elif isinstance(call_id, str) and call_id:
                                builder.call_id = call_id
                            function = getattr(tool_call, "function", None)
                            builder.append(
                                name=getattr(function, "name", None),
                                arguments=getattr(function, "arguments", None),
                            )
                    if getattr(choice, "finish_reason", None) == "tool_calls":
                        for key in [
                            key for key in tool_call_builders if key[0] == choice_index
                        ]:
                            event = tool_call_builders.pop(key).build()
                            if event is not None:
                                yield event
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = TokenUsage(
                    input_tokens=int(getattr(chunk_usage, "prompt_tokens", 0)),
                    output_tokens=int(getattr(chunk_usage, "completion_tokens", 0)),
                )
        for key in sorted(tool_call_builders):
            event = tool_call_builders[key].build()
            if event is not None:
                yield event
        yield DoneEvent(usage=usage)


__all__ = ["OpenAIDependencyImportError", "OpenAIProvider"]
