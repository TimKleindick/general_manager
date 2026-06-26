"""Shared helpers for chat provider implementations."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any

from general_manager.chat.providers.base import (
    ChatEvent,
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
    TokenUsage,
    ToolDefinition,
)


def parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    """Normalize provider tool arguments into a dict payload."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class StreamingToolCallBuilder:
    """Accumulate streamed tool-call name and JSON argument fragments."""

    def __init__(self, *, call_id: str) -> None:
        """Initialize an accumulator for one provider tool-call stream."""
        self.call_id = call_id
        self.name_parts: list[str] = []
        self.argument_parts: list[str] = []

    def append(self, *, name: Any = None, arguments: Any = None) -> None:
        """Append streamed name and argument fragments from a provider chunk."""
        if isinstance(name, str) and name:
            self.name_parts.append(name)
        if isinstance(arguments, str) and arguments:
            self.argument_parts.append(arguments)
        elif isinstance(arguments, dict):
            self.argument_parts = [json.dumps(arguments)]

    def build(self) -> ToolCallEvent | None:
        """Build a tool-call event when enough stream fragments were received."""
        name = "".join(self.name_parts).strip()
        if not name:
            return None
        args = parse_tool_arguments("".join(self.argument_parts))
        return ToolCallEvent(id=self.call_id, name=name, args=args)


def get_attr(value: Any, *path: str) -> Any:
    """Resolve a nested attribute path, returning None when absent."""
    current = value
    for part in path:
        if current is None:
            return None
        current = getattr(current, part, None)
    return current


__all__ = [
    "AsyncIterator",
    "ChatEvent",
    "DoneEvent",
    "Message",
    "StreamingToolCallBuilder",
    "TextChunkEvent",
    "TokenUsage",
    "ToolCallEvent",
    "ToolDefinition",
    "get_attr",
    "parse_tool_arguments",
]
