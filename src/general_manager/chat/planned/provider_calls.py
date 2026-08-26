"""Strict, one-round adapter for planned-chat provider completions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from math import isfinite

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


def _invalid(detail: str, usage: TokenUsage) -> None:
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


def _validate_tool_call(event: ToolCallEvent, usage: TokenUsage) -> None:
    if not isinstance(event.id, str) or not event.id:
        _invalid("tool calls must have a non-empty ID.", usage)
    if not isinstance(event.name, str) or not event.name:
        _invalid("tool calls must have a non-empty name.", usage)
    if not isinstance(event.args, dict):
        _invalid("tool call arguments must be an object.", usage)


async def complete_provider_round(
    provider: BaseLLMProvider,
    messages: list[Message],
    tools: list[ToolDefinition],
    timeout_seconds: float,
) -> ProviderRoundResult:
    """Buffer one bounded provider stream without changing legacy iteration.

    Planned orchestration deliberately accepts either text *or* one tool call,
    never both.  The timeout is supplied by the calling stage after it caps the
    request to its remaining deadline.
    """
    timeout = _validate_timeout(timeout_seconds)
    text_parts: list[str] = []
    tool_call: ToolCallEvent | None = None
    done_count = 0
    usage = TokenUsage()

    async with asyncio.timeout(timeout):
        async for event in provider.complete(messages, tools):
            if done_count:
                _invalid("a done event must be terminal.", usage)
            if isinstance(event, TextChunkEvent):
                if tool_call is not None:
                    _invalid(
                        "a provider round cannot contain text and a tool call.", usage
                    )
                text_parts.append(event.content)
                continue
            if isinstance(event, ToolCallEvent):
                _validate_tool_call(event, usage)
                if tool_call is not None:
                    _invalid(
                        "a provider round may contain at most one tool call.", usage
                    )
                if text_parts:
                    _invalid(
                        "a provider round cannot contain text and a tool call.", usage
                    )
                tool_call = event
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
    if tool_call is None and not text.strip():
        _invalid("a provider round must produce text or one tool call.", usage)
    return ProviderRoundResult(text=text, tool_call=tool_call, usage=usage)


__all__ = [
    "InvalidProviderRoundError",
    "ProviderRoundResult",
    "complete_provider_round",
]
