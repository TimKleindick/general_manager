"""Provider protocol and event types for chat."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol, Self, TYPE_CHECKING

if TYPE_CHECKING:
    from general_manager.chat.planned.schema_projection import (
        HistoricalSchema,
        ReferenceBinding,
        SchemaSlot,
        SchemaObservation,
    )


TOOL_RESULT_MISSING: Final = object()


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
class Message:
    """Provider-neutral chat history, including structured tool exchanges."""

    role: str
    content: str
    tool_calls: tuple[ToolCallEvent, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_result: Any = TOOL_RESULT_MISSING
    schema_slots: tuple[SchemaSlot, ...] = ()
    schema_reference_sha256: str | None = None
    historical_schema: HistoricalSchema | None = None
    logical_content: str | None = None
    projection_receipt: dict[str, Any] | None = None
    clarification_metadata: dict[str, Any] | None = None
    reference_binding: ReferenceBinding | None = None
    schema_observation: SchemaObservation | None = None


@dataclass(frozen=True)
class DoneEvent:
    """Terminal provider event carrying optional usage metadata."""

    usage: TokenUsage


ChatEvent = TextChunkEvent | ToolCallEvent | DoneEvent


class BaseLLMProvider(Protocol):
    """Minimal streaming protocol implemented by chat LLM adapters."""

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> Self:
        """Construct a provider using an instance-scoped configuration."""
        ...

    @property
    def provider_config(self) -> Mapping[str, Any]:
        """Return the read-only configuration used by this provider instance."""
        ...

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[ChatEvent]:
        """Stream text, tool calls, and completion metadata for one turn."""
        ...
