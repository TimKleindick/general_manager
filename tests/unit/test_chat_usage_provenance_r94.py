"""Absent provider usage must not become a synthetic zero accounting event."""

import asyncio

import pytest

from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.models import ValidatedPlan
from general_manager.chat.planned.planner import InvalidPlanError, plan_request
from general_manager.chat.planned.provider_calls import (
    InvalidProviderRoundError,
    ProviderStreamError,
    complete_provider_round,
)
from general_manager.chat.planned.scheduler import (
    PreparedPlannedTurn,
    SchedulerCallbacks,
    _Runner,
)
from general_manager.chat.planned.synthesis import (
    SynthesisFailedError,
    synthesize_answer,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from general_manager.chat.providers.ollama import OllamaProvider
from tests.unit.test_chat_planned_planner import _settings as _planner_settings
from tests.unit.test_chat_planned_provider_calls import _Provider
from tests.unit.test_chat_planned_scheduler import (
    _settings as _executor_settings,
    _task,
)
from tests.unit.test_chat_planned_synthesis import (
    _settings as _synthesis_settings,
    _store,
)
from tests.unit.test_chat_provider_usage_r94 import _chunks, _provider


_USAGES = [None, TokenUsage(0, 0), TokenUsage(2, 3)]


@pytest.mark.parametrize("usage", _USAGES)
def test_invalid_round_error_distinguishes_absent_from_explicit_zero_usage(usage):
    error = InvalidProviderRoundError("invalid round", usage=usage)
    assert error.usage_observed is (usage is not None)
    if usage is not None:
        assert error.usage == usage


@pytest.mark.parametrize("usage", _USAGES)
def test_forwarded_invalid_round_keeps_identity_and_real_observation_provenance(usage):
    failure = InvalidProviderRoundError("invalid custom round")

    class Provider:
        async def complete(self, messages, tools):
            if usage is not None:
                yield DoneEvent(usage)
            raise failure

    observed = []

    async def record(received):
        observed.append(received)

    with pytest.raises(InvalidProviderRoundError) as caught:
        asyncio.run(complete_provider_round(Provider(), [], [], 1, on_usage=record))
    assert caught.value is failure
    assert failure.usage_observed is (usage is not None)
    assert observed == ([] if usage is None else [usage])
    if usage is not None:
        assert failure.usage == usage


@pytest.mark.parametrize("usage", _USAGES)
@pytest.mark.parametrize("classified", [False, True])
def test_real_rejected_sdk_round_keeps_usage_provenance(monkeypatch, usage, classified):
    chunks = _chunks(OllamaProvider, rejected=True, known=usage is not None)
    if usage is not None:
        chunks[0]["prompt_eval_count"] = usage.input_tokens
        chunks[0]["eval_count"] = usage.output_tokens
    provider, transport = _provider(monkeypatch, OllamaProvider, chunks)
    observed = []

    async def record(received):
        observed.append(received)

    error_type = ProviderStreamError if classified else InvalidProviderRoundError
    with pytest.raises(error_type) as caught:
        asyncio.run(
            complete_provider_round(
                provider,
                [],
                [],
                1,
                classify_provider_errors=classified,
                on_usage=record,
            )
        )
    assert caught.value.usage_observed is (usage is not None)
    assert observed == ([] if usage is None else [usage])
    if usage is not None:
        assert caught.value.usage == usage
    assert transport.closed == ["stream", "client"]
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("events", "expected_usage"),
    [
        ([], None),
        ([TextChunkEvent("partial")], None),
        ([object()], None),
        ([DoneEvent(None)], None),
        ([ToolCallEvent("", "query", {})], None),
        ([ToolCallEvent("call", "", {})], None),
        ([ToolCallEvent("call", "query", [])], None),
        ([ToolCallEvent("call", "query", {"value": object()})], None),
        (
            [TextChunkEvent("partial"), ToolCallEvent("call", "query", {})],
            None,
        ),
        (
            [ToolCallEvent("call", "query", {}), ToolCallEvent("call", "query", {})],
            None,
        ),
        ([DoneEvent(TokenUsage(0, 0))], TokenUsage(0, 0)),
        ([DoneEvent(TokenUsage(2, 3))], TokenUsage(2, 3)),
        (
            [DoneEvent(TokenUsage(0, 0)), TextChunkEvent("late")],
            TokenUsage(0, 0),
        ),
        (
            [DoneEvent(TokenUsage(2, 3)), DoneEvent(TokenUsage(2, 3))],
            TokenUsage(2, 3),
        ),
    ],
)
def test_invalid_round_provenance_comes_from_real_done_observation(
    events, expected_usage
):
    observed = []

    async def record(usage):
        observed.append(usage)

    with pytest.raises(InvalidProviderRoundError) as caught:
        asyncio.run(
            complete_provider_round(_Provider(events), [], [], 1, on_usage=record)
        )
    assert caught.value.usage_observed is (expected_usage is not None)
    assert observed == ([] if expected_usage is None else [expected_usage])
    if expected_usage is not None:
        assert caught.value.usage == expected_usage


@pytest.mark.parametrize("usage", _USAGES)
def test_real_executor_books_only_observed_rejected_sdk_usage(monkeypatch, usage):
    async def run():
        chunks = _chunks(OllamaProvider, rejected=True, known=usage is not None)
        if usage is not None:
            chunks[0]["prompt_eval_count"] = usage.input_tokens
            chunks[0]["eval_count"] = usage.output_tokens
        provider, transport = _provider(monkeypatch, OllamaProvider, chunks)
        monkeypatch.setattr(
            "general_manager.chat.planned.scheduler.build_profile_provider",
            lambda _profile: provider,
        )
        prepared = PreparedPlannedTurn.for_plan(
            ValidatedPlan("read", (_task("task_1"),)),
            _executor_settings(),
            user_text="show parts",
        )
        bookings, tools = [], []
        loop = asyncio.get_running_loop()
        runner = _Runner(
            prepared,
            {},
            None,
            [],
            SchedulerCallbacks(
                execute_tool=lambda *args: tools.append(args),
                enforce_rate_limit=lambda _scope, **kwargs: bookings.append(
                    dict(kwargs)
                ),
            ),
            loop.time() + 10,
            loop.time,
        )
        runtime = runner.runtimes["task_1"]
        runtime.role = "executor"
        runtime.status = "running"
        assert await runner._execute_one_pass(runtime, ()) == "provider_failed"
        assert [entry for entry in bookings if "input_tokens" in entry] == (
            []
            if usage is None
            else [
                {
                    "count_request": False,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                }
            ]
        )
        assert [entry for entry in bookings if "input_tokens" not in entry] == [
            {"count_request": False},
            {"count_request": False},
        ]
        assert runtime.action_validation_error["code"] == "invalid_provider_round"
        assert tools == []
        assert transport.closed == ["stream", "client"]
        assert len(transport.requests) == 1

    asyncio.run(run())


class _InvalidRoundProvider:
    def __init__(self, mode, usage):
        self.mode = mode
        self.usage = usage
        self.calls = 0
        self.closed = 0

    async def complete(self, messages, tools):
        self.calls += 1
        try:
            if self.mode == "provider_error":
                detail = "invalid custom round"
                raise InvalidProviderRoundError(detail, usage=self.usage)
            if self.usage is None:
                yield ToolCallEvent("", "query", {})
            else:
                # EOF after a real Done with no usable output is a local invalid
                # round. Its usage callback must not be booked again by fallback.
                yield DoneEvent(self.usage)
        finally:
            self.closed += 1


@pytest.mark.parametrize("stage", ["planner", "synthesis"])
@pytest.mark.parametrize("mode", ["provider_error", "local_invalid"])
@pytest.mark.parametrize("usage", _USAGES)
def test_stage_fallbacks_preserve_unknown_and_known_usage_without_duplicates(
    monkeypatch, stage, mode, usage
):
    provider = _InvalidRoundProvider(mode, usage)
    module = "planner" if stage == "planner" else "synthesis"
    monkeypatch.setattr(
        f"general_manager.chat.planned.{module}.build_profile_provider",
        lambda _profile: provider,
    )
    observed = []

    async def record(received):
        observed.append(received)

    async def run():
        if stage == "planner":
            return await plan_request(
                "show parts",
                [],
                _planner_settings(),
                RoundBudget(()),
                {},
                on_usage=record,
            )
        return await synthesize_answer(
            "show parts",
            _store(),
            {},
            _synthesis_settings(),
            RoundBudget(()),
            on_usage=record,
        )

    error_type = InvalidPlanError if stage == "planner" else SynthesisFailedError
    attempt_count = 3 if stage == "planner" else 2
    with pytest.raises(error_type) as caught:
        asyncio.run(run())
    expected = [] if usage is None else [usage] * attempt_count
    assert observed == expected
    assert caught.value.attempt_usages == tuple(expected)
    assert provider.calls == attempt_count
    assert provider.closed == attempt_count
