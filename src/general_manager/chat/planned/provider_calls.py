"""Strict, one-round adapter for planned-chat provider completions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from math import isfinite
from typing import NoReturn

from general_manager.chat.planned.evidence import canonical_call_identity
from general_manager.chat.planned.schema_projection import compact_messages

from general_manager.chat.providers.base import (
    BaseLLMProvider,
    DoneEvent,
    Message,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
    ToolDefinition,
)


class InvalidProviderRoundError(ValueError):
    """Raised when a provider stream cannot represent one planned round."""

    def __init__(self, detail: str, *, usage: TokenUsage | None = None) -> None:
        self.usage = usage if usage is not None else TokenUsage()
        super().__init__(detail)


class InvalidProviderRoundTimeoutError(ValueError):
    """Raised when a planned round receives an invalid deadline value."""


@dataclass(frozen=True)
class ProviderRoundResult:
    """The only valid outcomes of one planned provider request."""

    text: str
    tool_call: ToolCallEvent | None
    usage: TokenUsage
    tool_calls: tuple[ToolCallEvent, ...] = ()

    def __post_init__(self) -> None:
        # Keep the original three positional arguments and singular accessor.
        calls = tuple(self.tool_calls)
        if not calls and self.tool_call is not None:
            calls = (self.tool_call,)
        if calls:
            if self.tool_call is not None and self.tool_call != calls[0]:
                _invalid("tool_call must match the first tool_calls item.", self.usage)
            object.__setattr__(self, "tool_call", calls[0])
        object.__setattr__(self, "tool_calls", calls)


def _invalid(detail: str, usage: TokenUsage) -> NoReturn:
    raise InvalidProviderRoundError(detail, usage=usage)


def _validate_timeout(timeout_seconds: float) -> float:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise InvalidProviderRoundTimeoutError(  # noqa: TRY003
            "timeout_seconds must be a positive number."
        )
    return float(timeout_seconds)


def _snapshot_tool_call(event: ToolCallEvent, usage: TokenUsage) -> ToolCallEvent:
    if not isinstance(event.id, str) or not event.id:
        _invalid("tool calls must have a non-empty ID.", usage)
    if not isinstance(event.name, str) or not event.name:
        _invalid("tool calls must have a non-empty name.", usage)
    if not isinstance(event.args, dict):
        _invalid("tool call arguments must be an object.", usage)
    try:
        arguments = json.loads(canonical_call_identity(event.name, event.args))["args"]
    except (TypeError, ValueError, RecursionError):
        _invalid("tool call arguments must contain finite JSON values.", usage)
    return ToolCallEvent(event.id, event.name, arguments)


async def complete_provider_round(
    provider: BaseLLMProvider,
    messages: list[Message],
    tools: list[ToolDefinition],
    timeout_seconds: float,
) -> ProviderRoundResult:
    """Buffer one bounded provider stream without changing legacy iteration.

    Planned orchestration accepts either text *or* ordered tool calls,
    never both.  The timeout is supplied by the calling stage after it caps the
    request to its remaining deadline.
    """
    timeout = _validate_timeout(timeout_seconds)
    text_parts: list[str] = []
    tool_calls: list[ToolCallEvent] = []
    call_ids: set[str] = set()
    done_count = 0
    usage = TokenUsage()

    async with asyncio.timeout(timeout):
        async for event in provider.complete(compact_messages(messages), tools):
            if done_count:
                _invalid("a done event must be terminal.", usage)
            if isinstance(event, TextChunkEvent):
                if tool_calls:
                    _invalid(
                        "a provider round cannot contain text and a tool call.", usage
                    )
                text_parts.append(event.content)
                continue
            if isinstance(event, ToolCallEvent):
                call = _snapshot_tool_call(event, usage)
                if call.id in call_ids:
                    _invalid(
                        "tool call IDs must be unique within a provider round.", usage
                    )
                if text_parts:
                    _invalid(
                        "a provider round cannot contain text and a tool call.", usage
                    )
                tool_calls.append(call)
                call_ids.add(call.id)
                continue
            if isinstance(event, DoneEvent):
                done_count += 1
                if done_count > 1:
                    _invalid("a provider round may contain only one done event.", usage)
                if not isinstance(event.usage, TokenUsage):
                    _invalid("a done event must carry token usage.", usage)
                usage = event.usage
                continue
            _invalid("a provider round emitted an unsupported event.", usage)

    if done_count != 1:
        _invalid("a provider round must finish with one done event.", usage)
    text = "".join(text_parts)
    if not tool_calls and not text.strip():
        _invalid("a provider round must produce text or tool calls.", usage)
    return ProviderRoundResult(
        text=text, tool_call=None, usage=usage, tool_calls=tuple(tool_calls)
    )


__all__ = [
    "InvalidProviderRoundError",
    "ProviderRoundResult",
    "complete_provider_round",
]
