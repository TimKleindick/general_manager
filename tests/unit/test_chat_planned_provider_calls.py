"""Contract tests for one bounded planned-provider completion round."""

from __future__ import annotations

import asyncio

import pytest

from general_manager.chat.planned.provider_calls import (
    InvalidProviderRoundError,
    InvalidProviderRoundTimeoutError,
    complete_provider_round,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)


class _Provider:
    def __init__(self, events: list[object]) -> None:
        self.events = events

    async def complete(self, _messages: list[object], _tools: list[object]):
        for event in self.events:
            yield event


class _StallingProvider:
    async def complete(self, _messages: list[object], _tools: list[object]):
        await asyncio.sleep(10)
        yield DoneEvent(usage=TokenUsage())


class _CancellationProvider:
    async def complete(self, _messages: list[object], _tools: list[object]):
        raise asyncio.CancelledError()
        yield DoneEvent(usage=TokenUsage())  # pragma: no cover


def test_provider_round_aggregates_text_and_usage() -> None:
    result = asyncio.run(
        complete_provider_round(
            _Provider(
                [
                    TextChunkEvent("one "),
                    TextChunkEvent("two"),
                    DoneEvent(TokenUsage(input_tokens=3, output_tokens=4)),
                ]
            ),
            [],
            [],
            1.0,
        )
    )

    assert result.text == "one two"
    assert result.tool_call is None
    assert result.usage == TokenUsage(input_tokens=3, output_tokens=4)


def test_provider_round_allows_at_most_one_tool_call() -> None:
    provider = _Provider(
        [
            ToolCallEvent("one", "schema", {}),
            ToolCallEvent("two", "query", {}),
            DoneEvent(TokenUsage()),
        ]
    )

    with pytest.raises(InvalidProviderRoundError):
        asyncio.run(complete_provider_round(provider, [], [], 1.0))


def test_provider_round_rejects_text_and_tool_ambiguity() -> None:
    provider = _Provider(
        [
            TextChunkEvent("I will query"),
            ToolCallEvent("one", "query", {}),
            DoneEvent(TokenUsage()),
        ]
    )

    with pytest.raises(InvalidProviderRoundError):
        asyncio.run(complete_provider_round(provider, [], [], 1.0))


def test_provider_round_rejects_empty_text_chunk_before_a_tool_call() -> None:
    provider = _Provider(
        [TextChunkEvent(""), ToolCallEvent("one", "query", {}), DoneEvent(TokenUsage())]
    )

    with pytest.raises(InvalidProviderRoundError):
        asyncio.run(complete_provider_round(provider, [], [], 1.0))


def test_provider_round_caps_timeout_to_stage_remaining() -> None:
    with pytest.raises(TimeoutError):
        asyncio.run(complete_provider_round(_StallingProvider(), [], [], 0.01))


@pytest.mark.parametrize("timeout_seconds", [float("nan"), float("inf"), float("-inf")])
def test_provider_round_rejects_non_finite_timeout(timeout_seconds: float) -> None:
    with pytest.raises(InvalidProviderRoundTimeoutError):
        asyncio.run(
            complete_provider_round(
                _Provider([TextChunkEvent("answer"), DoneEvent(TokenUsage())]),
                [],
                [],
                timeout_seconds,
            )
        )


def test_provider_round_rejects_no_usable_output_and_duplicate_done() -> None:
    with pytest.raises(InvalidProviderRoundError):
        asyncio.run(
            complete_provider_round(_Provider([DoneEvent(TokenUsage())]), [], [], 1.0)
        )


def test_provider_round_accepts_one_tool_call_and_rejects_events_after_done() -> None:
    tool = ToolCallEvent("one", "query", {})
    result = asyncio.run(
        complete_provider_round(_Provider([tool, DoneEvent(TokenUsage())]), [], [], 1.0)
    )

    assert result.tool_call == tool
    for event in (TextChunkEvent("late"), tool):
        with pytest.raises(InvalidProviderRoundError):
            asyncio.run(
                complete_provider_round(
                    _Provider([DoneEvent(TokenUsage()), event]), [], [], 1.0
                )
            )


def test_provider_round_rejects_a_stream_without_events() -> None:
    with pytest.raises(InvalidProviderRoundError):
        asyncio.run(complete_provider_round(_Provider([]), [], [], 1.0))
    with pytest.raises(InvalidProviderRoundError):
        asyncio.run(
            complete_provider_round(
                _Provider([DoneEvent(TokenUsage()), DoneEvent(TokenUsage())]),
                [],
                [],
                1.0,
            )
        )


def test_provider_round_propagates_cancellation() -> None:
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(complete_provider_round(_CancellationProvider(), [], [], 1.0))
