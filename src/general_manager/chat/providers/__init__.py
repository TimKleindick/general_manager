"""Built-in chat providers."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from importlib.util import find_spec
from typing import Any

from general_manager.chat.providers.base import (
    BaseLLMProvider,
    ChatEvent,
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
    TokenUsage,
    ToolDefinition,
)
from general_manager.chat.settings import get_chat_settings


PROVIDER_EXTRA = {
    "OllamaProvider": "chat-ollama",
    "OpenAIProvider": "chat-openai",
    "AnthropicProvider": "chat-anthropic",
    "GeminiProvider": "chat-google",
    "GoogleProvider": "chat-google",
}


class OllamaDependencyImportError(ImportError):
    """Raised when the optional Ollama dependency is unavailable."""

    def __init__(self) -> None:
        super().__init__("ollama package is not installed")


class OllamaBaseUrlError(ValueError):
    """Raised when the configured Ollama base URL uses an unsupported scheme."""

    def __init__(self, base_url: str) -> None:
        super().__init__(f"Ollama base_url must use http or https: {base_url}")


class OpenAIDependencyImportError(ImportError):
    """Raised when the optional OpenAI dependency is unavailable."""

    def __init__(self) -> None:
        super().__init__("openai package is not installed")


class AnthropicDependencyImportError(ImportError):
    """Raised when the optional Anthropic dependency is unavailable."""

    def __init__(self) -> None:
        super().__init__("anthropic package is not installed")


class GoogleDependencyImportError(ImportError):
    """Raised when the optional Google GenAI dependency is unavailable."""

    def __init__(self) -> None:
        super().__init__("google-genai package is not installed")


class OllamaProvider(BaseLLMProvider):
    """Streaming provider backed by the official Ollama Python client."""

    @classmethod
    def check_configuration(cls) -> None:
        if find_spec("ollama") is None:
            raise OllamaDependencyImportError()

    @staticmethod
    def _provider_config() -> dict[str, Any]:
        settings = get_chat_settings()
        configured = settings.get("provider_config", {})
        config = dict(configured if isinstance(configured, dict) else {})
        config.setdefault("model", "gemma4:e4b")
        config.setdefault("base_url", "http://127.0.0.1:11434")
        config.setdefault("timeout_seconds", 60)
        return config

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
        base_url = str(config["base_url"]).rstrip("/")
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            raise OllamaBaseUrlError(base_url)
        from ollama import AsyncClient  # type: ignore[import-not-found]

        return AsyncClient(host=base_url, timeout=float(config["timeout_seconds"]))

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


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _get_attr(value: Any, *path: str) -> Any:
    current = value
    for part in path:
        if current is None:
            return None
        current = getattr(current, part, None)
    return current


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
        async for chunk in stream:
            content = _get_attr(chunk, "choices")
            if isinstance(content, list):
                for index, choice in enumerate(content):
                    delta = getattr(choice, "delta", None)
                    text = getattr(delta, "content", None)
                    if isinstance(text, str) and text:
                        yield TextChunkEvent(content=text)
                    tool_calls = getattr(delta, "tool_calls", None)
                    if isinstance(tool_calls, list):
                        for tool_index, tool_call in enumerate(tool_calls):
                            function = getattr(tool_call, "function", None)
                            name = getattr(function, "name", None)
                            args = _parse_tool_arguments(
                                getattr(function, "arguments", None)
                            )
                            if isinstance(name, str):
                                yield ToolCallEvent(
                                    id=f"openai-tool-{index}-{tool_index}",
                                    name=name,
                                    args=args,
                                )
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = TokenUsage(
                    input_tokens=int(getattr(chunk_usage, "prompt_tokens", 0)),
                    output_tokens=int(getattr(chunk_usage, "completion_tokens", 0)),
                )
        yield DoneEvent(usage=usage)


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
                text = _get_attr(event, "delta", "text")
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


class GeminiProvider(BaseLLMProvider):
    """Streaming provider backed by the Google GenAI Python SDK."""

    required_extra = "chat-google"

    @classmethod
    def check_configuration(cls) -> None:
        if find_spec("google.genai") is None:
            raise GoogleDependencyImportError()

    @staticmethod
    def _provider_config() -> dict[str, Any]:
        settings = get_chat_settings()
        configured = settings.get("provider_config", {})
        config = dict(configured if isinstance(configured, dict) else {})
        config.setdefault("model", "gemini-2.5-flash")
        return config

    @classmethod
    def _build_async_client(cls) -> Any:
        config = cls._provider_config()
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
        client = self._build_async_client()
        config = self._provider_config()
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
            tool_calls = getattr(chunk, "tool_calls", None)
            if isinstance(tool_calls, list):
                for index, tool_call in enumerate(tool_calls):
                    name = (
                        tool_call.get("name") if isinstance(tool_call, dict) else None
                    )
                    args = (
                        tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
                    )
                    if isinstance(name, str) and isinstance(args, dict):
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
