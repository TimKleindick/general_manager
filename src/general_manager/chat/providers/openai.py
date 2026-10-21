"""OpenAI chat provider."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from importlib.util import find_spec
import json
from types import MappingProxyType
from typing import Any, Self

from general_manager.chat.providers._shared import (
    AsyncIterator,
    ChatEvent,
    DoneEvent,
    Message,
    ProviderCompletionError,
    closing_async_resource,
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
        """Validate that the OpenAI SDK is available before use."""
        del config
        if find_spec("openai") is None:
            raise OpenAIDependencyImportError()

    @staticmethod
    def _provider_config(configured: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if configured is None:
            settings = get_chat_settings()
            configured = settings.get("provider_config", {})
        config = dict(configured if isinstance(configured, Mapping) else {})
        config.setdefault("model", "gpt-4.1-mini")
        config.setdefault("timeout_seconds", 60)
        return config

    @classmethod
    def _build_async_client(cls, config: Mapping[str, Any] | None = None) -> Any:
        config = cls._provider_config(config)
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {"timeout": float(config["timeout_seconds"])}
        base_url = config.get("base_url")
        if base_url:
            kwargs["base_url"] = str(base_url)
        api_key = config.get("api_key")
        if api_key:
            kwargs["api_key"] = str(api_key)
        return AsyncOpenAI(**kwargs)

    @staticmethod
    def _build_messages(messages: list[Message]) -> list[dict[str, Any]]:
        """Encode neutral text and tool history for Chat Completions."""
        provider_messages: list[dict[str, Any]] = []
        for message in messages:
            provider_message: dict[str, Any] = {
                "role": message.role,
                "content": message.content,
            }
            if message.tool_calls:
                provider_message["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.args),
                        },
                    }
                    for call in message.tool_calls
                ]
            if message.role == "tool" and message.tool_call_id is not None:
                provider_message["tool_call_id"] = message.tool_call_id
            provider_messages.append(provider_message)
        return provider_messages

    def complete_structured(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        *,
        response_schema: Mapping[str, Any],
    ) -> AsyncIterator[ChatEvent]:
        """Opt into best-effort schema guidance without changing legacy calls."""
        if self.provider_config.get("structured_outputs") is True:
            return self.complete(messages, tools, response_schema=response_schema)
        return self.complete(messages, tools)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        *,
        response_schema: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """Stream OpenAI text, tool calls, and usage events for one chat turn."""
        config = self.provider_config
        schema_guidance = (
            response_schema is not None and config.get("structured_outputs") is True
        )
        response_options: dict[str, Any] = {}
        if schema_guidance:
            # Existing phase schemas exceed OpenAI's strict subset. Keep their
            # semantics intact and let the phase validators admit the result.
            response_options["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "planned_response",
                    "schema": deepcopy(dict(response_schema or {})),
                    "strict": False,
                },
            }
        client = self._build_async_client(config)
        async with closing_async_resource(client):
            stream = await client.chat.completions.create(
                model=config["model"],
                stream=True,
                stream_options={"include_usage": True},
                messages=self._build_messages(messages),
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
                **response_options,
            )
            usage = TokenUsage()
            tool_call_builders: dict[tuple[int, int], StreamingToolCallBuilder] = {}
            seen_choices: set[int] = set()
            completed_choices: set[int] = set()
            completion_failure: str | None = None
            async with closing_async_resource(stream):
                async for chunk in stream:
                    content = get_attr(chunk, "choices")
                    if isinstance(content, list):
                        for index, choice in enumerate(content):
                            choice_index = getattr(choice, "index", index)
                            if not isinstance(choice_index, int):
                                choice_index = index
                            seen_choices.add(choice_index)
                            reason = getattr(choice, "finish_reason", None)
                            if reason in {"stop", "tool_calls"}:
                                completed_choices.add(choice_index)
                            elif reason is not None:
                                if not schema_guidance:
                                    raise ProviderCompletionError(
                                        "provider_response_incomplete"
                                    )
                                completion_failure = "provider_response_incomplete"
                            delta = getattr(choice, "delta", None)
                            if schema_guidance and getattr(delta, "refusal", None):
                                completion_failure = "provider_refusal"
                            if completion_failure is not None:
                                continue
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
                                        fallback_id = (
                                            f"openai-tool-{choice_index}-{call_index}"
                                        )
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
                                    key
                                    for key in tool_call_builders
                                    if key[0] == choice_index
                                ]:
                                    event = tool_call_builders.pop(key).build()
                                    if event is not None:
                                        yield event
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None:
                        usage = TokenUsage(
                            input_tokens=int(getattr(chunk_usage, "prompt_tokens", 0)),
                            output_tokens=int(
                                getattr(chunk_usage, "completion_tokens", 0)
                            ),
                        )
                if not seen_choices or seen_choices != completed_choices:
                    if not schema_guidance:
                        raise ProviderCompletionError()
                    completion_failure = (
                        completion_failure or "provider_response_incomplete"
                    )
                if completion_failure is not None:
                    # completed_events observes usage immediately but admits
                    # Done only after EOF, so this failure cannot become success.
                    yield DoneEvent(usage=usage)
                    raise ProviderCompletionError(completion_failure)
                for key in sorted(tool_call_builders):
                    event = tool_call_builders[key].build()
                    if event is not None:
                        yield event
                yield DoneEvent(usage=usage)


__all__ = ["OpenAIDependencyImportError", "OpenAIProvider"]
