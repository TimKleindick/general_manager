"""Provider protocol and event types for chat."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class TextChunkEvent:
    content: str


@dataclass(frozen=True)
class ToolCallEvent:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class DoneEvent:
    usage: TokenUsage


ChatEvent = TextChunkEvent | ToolCallEvent | DoneEvent


class BaseLLMProvider(Protocol):
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[ChatEvent]: ...
