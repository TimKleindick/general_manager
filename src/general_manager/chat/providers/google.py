"""Google GenAI chat providers."""

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
)
from general_manager.chat.providers.base import BaseLLMProvider
from general_manager.chat.settings import get_chat_settings


class GoogleDependencyImportError(ImportError):
    """Raised when the optional Google GenAI dependency is unavailable."""

    def __init__(self) -> None:
        super().__init__("google-genai package is not installed")


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

__all__ = ["GeminiProvider", "GoogleDependencyImportError", "GoogleProvider"]
