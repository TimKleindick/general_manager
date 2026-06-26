"""Provider protocol and event types for chat."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Message:
    """One chat message sent to or returned from an LLM provider."""

    role: str
    content: str


@dataclass(frozen=True)
class ToolDefinition:
    """Provider-agnostic tool schema exposed to an LLM provider."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting returned by a provider for one completion."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class TextChunkEvent:
    """Streaming assistant text emitted by a provider."""

    content: str


@dataclass(frozen=True)
class ToolCallEvent:
    """Provider request to execute one configured chat tool."""

    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class DoneEvent:
    """Terminal provider event carrying optional usage metadata."""

    usage: TokenUsage


ChatEvent = TextChunkEvent | ToolCallEvent | DoneEvent


class BaseLLMProvider(Protocol):
    """Minimal streaming protocol implemented by chat LLM adapters."""

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[ChatEvent]:
        """Stream text, tool calls, and completion metadata for one turn."""
        ...
