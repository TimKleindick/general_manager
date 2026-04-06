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
    "TextChunkEvent",
    "TokenUsage",
    "ToolCallEvent",
    "ToolDefinition",
    "get_attr",
    "parse_tool_arguments",
]
