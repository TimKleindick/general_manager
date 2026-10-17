"""Strict, one-round adapter for planned-chat provider completions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
from math import isfinite
from typing import NoReturn

from general_manager.chat.planned.evidence import canonical_call_identity
from general_manager.chat.planned.schema_projection import compact_messages
from general_manager.chat.providers._shared import (
    closing_async_resource,
    completed_events,
    ProviderCompletionError,
)

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


class ProviderStreamError(RuntimeError):
    """Provider failure with observed usage, never local action validation."""

    public_reason = "provider_failed"

    def __init__(self, *, usage: TokenUsage | None = None) -> None:
        self.usage_observed = usage is not None
        self.usage = usage if usage is not None else TokenUsage()
        super().__init__("provider_failed")


class ProviderRoundTimeoutError(TimeoutError):
    """The existing deadline expired after any independently observed usage."""

    def __init__(self, *, usage: TokenUsage | None = None) -> None:
        self.usage_observed = usage is not None
        self.usage = usage if usage is not None else TokenUsage()
        super().__init__("deadline_exceeded")


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
    *,
    classify_provider_errors: bool = False,
    on_usage: Callable[[TokenUsage], Awaitable[None]] | None = None,
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
    usage_observed = False

    async def account_usage(received: TokenUsage) -> None:
        nonlocal usage, usage_observed
        usage = received
        usage_observed = True
        if on_usage is not None:
            await on_usage(received)

    deadline = asyncio.timeout(timeout)
    try:
        async with deadline:
            prepared_messages = compact_messages(messages)
            try:
                stream = aiter(
                    completed_events(
                        provider.complete(prepared_messages, tools),
                        on_usage=account_usage,
                    )
                )
            except (InvalidProviderRoundError, InvalidProviderRoundTimeoutError):
                raise
            except Exception as exc:
                if classify_provider_errors:
                    raise ProviderStreamError() from exc
                raise
            async with closing_async_resource(stream):
                while True:
                    try:
                        event = await anext(stream)
                    except StopAsyncIteration:
                        break
                    except ProviderCompletionError as exc:
                        if classify_provider_errors:
                            raise ProviderStreamError(
                                usage=usage if usage_observed else None
                            ) from exc
                        messages_by_code = {
                            "stream_without_completion": "a provider round must finish with one done event.",
                            "events_after_completion": "a done event must be terminal.",
                            "invalid_completion_usage": "a done event must carry token usage.",
                            "unsupported_provider_event": "a provider round emitted an unsupported event.",
                        }
                        _invalid(messages_by_code.get(exc.code, exc.code), usage)
                    except (
                        InvalidProviderRoundError,
                        InvalidProviderRoundTimeoutError,
                    ):
                        raise
                    except Exception as exc:
                        if classify_provider_errors or usage_observed:
                            raise ProviderStreamError(
                                usage=usage if usage_observed else None
                            ) from exc
                        raise
                    if isinstance(event, TextChunkEvent):
                        if tool_calls:
                            _invalid(
                                "a provider round cannot contain text and a tool call.",
                                usage,
                            )
                        text_parts.append(event.content)
                        continue
                    if isinstance(event, ToolCallEvent):
                        call = _snapshot_tool_call(event, usage)
                        if call.id in call_ids:
                            _invalid(
                                "tool call IDs must be unique within a provider round.",
                                usage,
                            )
                        if text_parts:
                            _invalid(
                                "a provider round cannot contain text and a tool call.",
                                usage,
                            )
                        tool_calls.append(call)
                        call_ids.add(call.id)
                        continue
                    if isinstance(event, DoneEvent):
                        done_count += 1
                        usage = event.usage
                        continue
                    _invalid("a provider round emitted an unsupported event.", usage)
    except TimeoutError as exc:
        if not deadline.expired():
            raise
        raise ProviderRoundTimeoutError(
            usage=usage if usage_observed else None
        ) from exc

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
    "ProviderRoundTimeoutError",
    "ProviderStreamError",
    "complete_provider_round",
]
