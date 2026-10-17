"""Provider-only failures retain their origin without changing planner attempts."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import pytest
from django.test.utils import override_settings

from general_manager.chat.errors import public_chat_error
from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.planner import InvalidPlanError, plan_request
from general_manager.chat.planned.provider_calls import (
    InvalidProviderRoundError,
    ProviderStreamError,
    complete_provider_round,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from tests.unit.test_chat_planned_planner import _plan, _settings


class _Provider:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = 0

    async def complete(self, _messages, _tools):
        self.calls += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        yield TextChunkEvent(outcome)
        yield DoneEvent(TokenUsage(2, 3))


def _use_provider(monkeypatch, provider):
    monkeypatch.setattr(
        "general_manager.chat.planned.planner.build_profile_provider",
        lambda _profile: provider,
    )


def test_latched_transport_failure_has_provider_provenance_without_new_attempts(
    monkeypatch,
):
    private = "private transport details never sent to the user"

    class LatchedTransport:
        def __init__(self):
            self.calls = 0
            self.simulated_posts = []
            self.stopped = False

        async def complete(self, _messages, _tools):
            self.calls += 1
            self.simulated_posts.append(0 if self.stopped else 1)
            if self.stopped:
                raise RuntimeError(private)
            self.stopped = True
            raise TimeoutError(private)
            yield DoneEvent(TokenUsage())  # pragma: no cover

    provider = LatchedTransport()
    _use_provider(monkeypatch, provider)
    audit = []
    budget = RoundBudget(())
    with (
        override_settings(
            GENERAL_MANAGER={
                "CHAT": {
                    "audit": {
                        "enabled": True,
                        "level": "tool_calls",
                        "logger": audit.append,
                    }
                }
            }
        ),
        pytest.raises(InvalidPlanError) as caught,
    ):
        asyncio.run(
            plan_request(
                "show parts", [], _settings(), budget, {}, configured_context=""
            )
        )

    # E100's observed attempt shape: one transport start, then two local STOPs.
    assert provider.simulated_posts == [1, 0, 0]
    assert provider.calls == budget.global_used == 3
    assert provider.stopped is True
    error = public_chat_error(caught.value)
    assert error.code == "provider_failed"
    assert error.as_event() == {
        "type": "error",
        "code": "provider_failed",
        "message": "The provider could not complete the request.",
    }
    assert caught.value.usage == TokenUsage()
    assert caught.value.attempt_usages == ()
    terminals = [event for event in audit if event["event_type"] == "planned_terminal"]
    assert terminals == [
        {
            "event_type": "planned_terminal",
            "terminal_reason": "provider_failed",
            "reason_origin": "provider_exception",
        }
    ]
    assert private not in json.dumps(audit)
    assert private not in str(caught.value)


@pytest.mark.parametrize(
    "outcomes, expected_usage",
    [
        (["not json"] * 3, TokenUsage(6, 9)),
        (
            ["not json", TimeoutError("private"), RuntimeError("private")],
            TokenUsage(2, 3),
        ),
        (
            [TimeoutError("private"), "not json", RuntimeError("private")],
            TokenUsage(2, 3),
        ),
        (
            [
                InvalidProviderRoundError("private", usage=TokenUsage(5, 7)),
                TimeoutError("private"),
                RuntimeError("private"),
            ],
            TokenUsage(5, 7),
        ),
    ],
)
def test_invalid_response_or_stream_retains_invalid_plan(
    monkeypatch, outcomes, expected_usage
):
    provider = _Provider(outcomes)
    _use_provider(monkeypatch, provider)
    budget = RoundBudget(())
    with pytest.raises(InvalidPlanError) as caught:
        asyncio.run(
            plan_request(
                "show parts", [], _settings(), budget, {}, configured_context=""
            )
        )
    assert public_chat_error(caught.value).code == "invalid_plan"
    assert str(caught.value) == "invalid_plan"
    assert caught.value.usage == expected_usage
    assert provider.calls == budget.global_used == 3


def test_provider_recovery_still_returns_the_plan(monkeypatch):
    provider = _Provider([TimeoutError("private"), json.dumps(_plan())])
    _use_provider(monkeypatch, provider)
    budget = RoundBudget(())
    result = asyncio.run(
        plan_request("show parts", [], _settings(), budget, {}, configured_context="")
    )
    assert result.plan.tasks[0].task_id == "task_1"
    assert result.usage == TokenUsage(2, 3)
    assert provider.calls == budget.global_used == 2


def test_cancellation_still_propagates_without_fallback(monkeypatch):
    provider = _Provider([asyncio.CancelledError()])
    _use_provider(monkeypatch, provider)
    budget = RoundBudget(())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            plan_request(
                "show parts", [], _settings(), budget, {}, configured_context=""
            )
        )
    assert provider.calls == budget.global_used == 1


def test_provider_construction_failure_is_not_claimed_to_be_a_stream_exception(
    monkeypatch,
):
    def broken_configuration(_profile):
        private = "private setup failure"
        raise RuntimeError(private)

    monkeypatch.setattr(
        "general_manager.chat.planned.planner.build_profile_provider",
        broken_configuration,
    )
    with pytest.raises(InvalidPlanError) as caught:
        asyncio.run(
            plan_request(
                "show parts",
                [],
                _settings(),
                RoundBudget(()),
                {},
                configured_context="",
            )
        )
    assert public_chat_error(caught.value).code == "invalid_plan"


def test_reference_compaction_failure_is_not_a_provider_exception(monkeypatch):
    provider = _Provider([])
    _use_provider(monkeypatch, provider)

    def invalid_reference(_messages):
        private = "private invalid reference binding"
        raise ValueError(private)

    monkeypatch.setattr(
        "general_manager.chat.planned.provider_calls.compact_messages",
        invalid_reference,
    )
    budget = RoundBudget(())
    with pytest.raises(InvalidPlanError) as caught:
        asyncio.run(
            plan_request(
                "show parts", [], _settings(), budget, {}, configured_context=""
            )
        )
    assert provider.calls == 0
    assert budget.global_used == 3
    assert public_chat_error(caught.value).code == "invalid_plan"


@pytest.mark.parametrize("timeout", [0, float("inf")])
def test_invalid_round_timeout_is_not_a_provider_stream_exception(monkeypatch, timeout):
    provider = _Provider([])
    _use_provider(monkeypatch, provider)
    budget = RoundBudget(())
    with pytest.raises(InvalidPlanError) as caught:
        asyncio.run(
            plan_request(
                "show parts",
                [],
                replace(_settings(), evidence_timeout_seconds=timeout),
                budget,
                {},
                configured_context="",
            )
        )
    assert public_chat_error(caught.value).code == "invalid_plan"
    assert provider.calls == 0
    assert budget.global_used == 3


def test_prepared_turn_preserves_provider_failure_classification(monkeypatch):
    from general_manager.chat.planned.scheduler import prepare_planned_turn

    provider = _Provider([TimeoutError("private")] * 3)
    _use_provider(monkeypatch, provider)
    with pytest.raises(InvalidPlanError) as caught:
        asyncio.run(prepare_planned_turn("show parts", [], _settings(), {}))
    assert public_chat_error(caught.value).code == "provider_failed"
    assert caught.value.usage == TokenUsage()
    assert caught.value.attempt_usages == ()
    assert provider.calls == 3


@pytest.mark.parametrize("classify", [False, True])
@pytest.mark.parametrize("stage", ["open", "read"])
def test_stream_exception_classification_is_opt_in_and_sanitized(classify, stage):
    private = ConnectionError("private provider diagnostic")

    class FailingProvider:
        def complete(self, _messages, _tools):
            if stage == "open":
                raise private
            return self.events()

        async def events(self):
            yield TextChunkEvent("partial uncompleted answer")
            raise private

    expected = ProviderStreamError if classify else ConnectionError
    with pytest.raises(expected) as caught:
        asyncio.run(
            complete_provider_round(
                FailingProvider(), [], [], 1, classify_provider_errors=classify
            )
        )
    if classify:
        assert str(caught.value) == "provider_failed"
        assert caught.value.__cause__ is private
    else:
        assert caught.value is private


def test_opted_in_stream_validation_remains_native_invalid_round():
    class AmbiguousProvider:
        async def complete(self, _messages, _tools):
            yield TextChunkEvent("answer and tool")
            yield ToolCallEvent("call", "query", {})
            yield DoneEvent(TokenUsage())

    with pytest.raises(InvalidProviderRoundError):
        asyncio.run(
            complete_provider_round(
                AmbiguousProvider(), [], [], 1, classify_provider_errors=True
            )
        )


@pytest.mark.parametrize("classify", [False, True])
def test_adapter_deadline_is_not_reclassified_as_a_provider_exception(classify):
    class StallingProvider:
        cancelled = False

        async def complete(self, _messages, _tools):
            try:
                await asyncio.Future()
            finally:
                self.cancelled = True
            yield DoneEvent(TokenUsage())  # pragma: no cover

    provider = StallingProvider()
    with pytest.raises(TimeoutError):
        asyncio.run(
            complete_provider_round(
                provider, [], [], 0.01, classify_provider_errors=classify
            )
        )
    assert provider.cancelled is True
