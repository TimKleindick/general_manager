"""Shared helpers for chat provider implementations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from inspect import isawaitable
import json
from typing import Any, TypeVar

from general_manager.chat.providers.base import (
    ChatEvent,
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
    TokenUsage,
    ToolDefinition,
)


async def close_async_resource(resource: Any) -> None:
    """Close an owned SDK stream or iterator through its supported public closer."""
    close = getattr(resource, "aclose", None)
    if not callable(close):
        close = getattr(resource, "close", None)
    if callable(close):
        result = close()
        if isawaitable(result):
            await result


_Resource = TypeVar("_Resource")


@asynccontextmanager
async def closing_async_resource(resource: _Resource) -> AsyncIterator[_Resource]:
    """Await owned-stream cleanup on every exit, retaining a primary failure."""
    try:
        yield resource
    except BaseException as exc:
        with suppress(Exception):
            await close_async_resource(resource)
        # An inner generator's failing finally may replace the CancelledError
        # it received. A pending task cancellation still owns the outcome.
        task = asyncio.current_task()
        if isinstance(exc, Exception) and task is not None and task.cancelling():
            raise asyncio.CancelledError() from exc
        raise
    else:
        await close_async_resource(resource)
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError()


class ProviderCompletionError(ValueError):
    """A response cannot prove successful, terminal protocol completion."""

    public_reason = "provider_failed"

    def __init__(self, code: str = "stream_without_completion") -> None:
        self.code = code
        super().__init__(code)


async def completed_events(
    events: AsyncIterator[ChatEvent],
    *,
    on_usage: Callable[[TokenUsage], Awaitable[None]] | None = None,
) -> AsyncIterator[ChatEvent]:
    """Require exactly one terminal DoneEvent and close the owned iterator.

    Buffer the terminal until EOF so consumers cannot execute tools or declare
    success before late events or stream failures have been checked. Adapters
    prove vendor completion; callers retain deadlines, validation and STOP policy.
    """
    done = None
    async with closing_async_resource(events):
        async for event in events:
            if done is not None:
                raise ProviderCompletionError("events_after_completion")
            if isinstance(event, DoneEvent):
                if not isinstance(event.usage, TokenUsage):
                    raise ProviderCompletionError("invalid_completion_usage")
                done = event
                # Accounting is an observation, not admission of a successful
                # response. Retain known usage even if later validation fails.
                if on_usage is not None:
                    await on_usage(event.usage)
            elif isinstance(event, (TextChunkEvent, ToolCallEvent)):
                yield event
            else:
                raise ProviderCompletionError("unsupported_provider_event")
        if done is None:
            raise ProviderCompletionError()
    # Cleanup is part of completion admission; do not require a consumer's next
    # anext/aclose to surface an SDK close failure after receiving success.
    yield done


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
    "ProviderCompletionError",
    "StreamingToolCallBuilder",
    "TextChunkEvent",
    "TokenUsage",
    "ToolCallEvent",
    "ToolDefinition",
    "close_async_resource",
    "completed_events",
    "get_attr",
    "parse_tool_arguments",
]
