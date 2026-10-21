"""Real quota admission and cancellation accounting at planned stage boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.cache import cache
from django.test import override_settings

from general_manager.chat.errors import public_chat_error
from general_manager.chat.planned.models import ValidatedPlan
from general_manager.chat.planned.scheduler import (
    PreparedPlannedTurn,
    SchedulerCallbacks,
    _Runner,
    iter_planned_read_events,
    prepare_planned_turn,
)
from general_manager.chat.providers.base import (
    ChatEvent,
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from general_manager.chat.rate_limits import enforce_chat_rate_limit
from tests.unit.test_chat_planned_planner import _plan
from tests.unit.test_chat_planned_scheduler import (
    _Executor,
    _collect,
    _settings,
    _task,
)


def _quota(tokens: int) -> override_settings:
    return override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "planned-quota-regressions",
            }
        },
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "rate_limit": {"tokens": tokens, "requests": 1, "window_seconds": 60},
            }
        },
    )


def _prepared() -> PreparedPlannedTurn:
    _Executor.calls = []
    _Executor.responses_by_task = {
        "task_1": [
            ToolCallEvent(
                "query", "query", {"manager": "PartManager", "fields": ["name"]}
            ),
            {"action": "complete", "evidence_ids": ["task_1:query:1"]},
        ]
    }
    _Executor.responses = [{"answer": "No parts.", "evidence_ids": ["task_1:query:1"]}]
    return PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (_task("task_1"),)), _settings(), user_text="show parts"
    )


@pytest.mark.parametrize("tokens", [1, 2])
def test_exhausted_first_executor_response_admits_no_tool_or_new_provider(
    tokens: int,
) -> None:
    """Ignoring denial, or missing exact-boundary admission, executes the query."""
    prepared = _prepared()
    tools: list[str] = []
    scope = {"user": SimpleNamespace(pk="quota-boundary")}
    with _quota(tokens):
        cache.clear()
        assert enforce_chat_rate_limit(scope) is None
        events = asyncio.run(
            _collect(
                iter_planned_read_events(
                    prepared,
                    scope=scope,
                    conversation=None,
                    messages=[],
                    callbacks=SchedulerCallbacks(
                        execute_tool=lambda name, *_args: tools.append(name)
                        or {"status": "success", "data": []},
                        enforce_rate_limit=enforce_chat_rate_limit,
                    ),
                )
            )
        )
        assert (
            cache.get("general_manager:chat_rate_limit:user:quota-boundary:tokens") == 2
        )
        assert (
            cache.get("general_manager:chat_rate_limit:user:quota-boundary:requests")
            == 1
        )
    assert len(_Executor.calls) == 1
    assert tools == []
    assert events[-1]["code"] == "rate_limited"
    assert events[-1]["retry_after_seconds"] == 60


def test_already_exhausted_executor_starts_no_provider_and_charges_no_round() -> None:
    prepared = _prepared()
    scope = {"user": SimpleNamespace(pk="quota-preexisting")}
    with _quota(1):
        cache.clear()
        enforce_chat_rate_limit(scope, input_tokens=1, count_request=False)
        events = asyncio.run(
            _collect(
                iter_planned_read_events(
                    prepared,
                    scope=scope,
                    conversation=None,
                    messages=[],
                    callbacks=SchedulerCallbacks(
                        enforce_rate_limit=enforce_chat_rate_limit
                    ),
                )
            )
        )
    assert _Executor.calls == []
    assert prepared.budget.global_used == 0
    assert events[-1]["code"] == "rate_limited"


@pytest.mark.parametrize("tokens, expected_calls", [(4, 2), (6, 3)])
def test_synthesis_and_its_fallback_require_fresh_quota(
    tokens: int, expected_calls: int
) -> None:
    prepared = _prepared()
    _Executor.responses = [
        {"answer": "invalid", "evidence_ids": ["missing"]},
        {"answer": "No parts.", "evidence_ids": ["task_1:query:1"]},
    ]
    with _quota(tokens):
        cache.clear()
        events = asyncio.run(
            _collect(
                iter_planned_read_events(
                    prepared,
                    scope={},
                    conversation=None,
                    messages=[],
                    callbacks=SchedulerCallbacks(
                        execute_tool=lambda *_args: {"status": "success", "data": []},
                        enforce_rate_limit=enforce_chat_rate_limit,
                    ),
                )
            )
        )
        assert (
            cache.get("general_manager:chat_rate_limit:anonymous:tokens")
            == expected_calls * 2
        )
    assert len(_Executor.calls) == expected_calls
    assert events[-1]["code"] == "rate_limited"
    assert prepared.result is not None
    assert prepared.result.coverage.resolved == 1


def test_planner_correction_does_not_start_after_reported_usage_exhausts_quota() -> (
    None
):
    _Executor.calls = []
    _Executor.responses_by_task = {}
    _Executor.responses = [{}, {}, {}]
    with _quota(1):
        cache.clear()
        with pytest.raises(Exception) as caught:
            asyncio.run(
                prepare_planned_turn(
                    "show parts",
                    [],
                    _settings(),
                    {},
                    callbacks=SchedulerCallbacks(
                        enforce_rate_limit=enforce_chat_rate_limit
                    ),
                    scope={},
                )
            )
        assert cache.get("general_manager:chat_rate_limit:anonymous:tokens") == 2
    assert len(_Executor.calls) == 1
    assert public_chat_error(caught.value).code == "rate_limited"
    assert public_chat_error(caught.value).retry_after_seconds == 60


@pytest.mark.parametrize("intent", ["read", "mutation"])
def test_known_planner_denial_survives_success_and_later_callback_outage(
    intent: str,
) -> None:
    _prepared()
    response = _plan() if intent == "read" else {"intent": "mutation", "tasks": []}
    _Executor.responses.insert(0, response)
    denied = False
    bookings: list[tuple[int, int]] = []
    escaped_intents: list[str] = []
    tools: list[str] = []

    def limiter(scope: dict[str, Any], **kwargs: Any) -> dict[str, Any] | None:
        nonlocal denied
        if denied:
            raise RuntimeError("callback_unavailable")
        if "input_tokens" in kwargs:
            bookings.append((kwargs["input_tokens"], kwargs["output_tokens"]))
        result = enforce_chat_rate_limit(scope, **kwargs)
        denied = result is not None
        return result

    async def scenario() -> None:
        callbacks = SchedulerCallbacks(
            enforce_rate_limit=limiter,
            execute_tool=lambda name, *_args: tools.append(name)
            or {"status": "success", "data": []},
        )
        prepared = await prepare_planned_turn(
            "show parts" if intent == "read" else "delete the part",
            [],
            _settings(),
            {},
            callbacks=callbacks,
            scope={},
        )
        escaped_intents.append(prepared.plan.intent)
        if intent == "read":
            await _collect(
                iter_planned_read_events(
                    prepared,
                    scope={},
                    conversation=None,
                    messages=[],
                    callbacks=callbacks,
                )
            )

    with _quota(1):
        cache.clear()
        with pytest.raises(RuntimeError, match="rate_limited") as caught:
            asyncio.run(scenario())
        assert cache.get("general_manager:chat_rate_limit:anonymous:tokens") == 2
    assert denied
    assert bookings == [(1, 1)]
    assert len(_Executor.calls) == 1
    assert escaped_intents == []
    assert tools == []
    assert public_chat_error(caught.value).code == "rate_limited"
    assert public_chat_error(caught.value).retry_after_seconds == 60


@pytest.mark.parametrize("cancel_during_booking", [False, True])
def test_synthesis_known_usage_survives_cancellation_once(
    monkeypatch: pytest.MonkeyPatch,
    cancel_during_booking: bool,
) -> None:
    """A cancelled fallback/booking retains the first attempt's known 11+13 tokens."""
    prepared = _prepared()
    bookings: list[tuple[int, int]] = []

    async def scenario() -> None:
        reached = asyncio.Event()
        release_booking = asyncio.Event()
        attempts = 0

        class SynthesisProvider:
            async def complete(
                self, _messages: object, _tools: object
            ) -> AsyncIterator[ChatEvent]:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    yield TextChunkEvent('{"answer":"bad","evidence_ids":["missing"]}')
                    yield DoneEvent(TokenUsage(11, 13))
                else:
                    reached.set()
                    await asyncio.Event().wait()

        monkeypatch.setattr(
            "general_manager.chat.planned.synthesis.build_profile_provider",
            lambda _profile: SynthesisProvider(),
        )

        def limit(_scope: object, **kwargs: Any) -> None:
            if "input_tokens" in kwargs:
                bookings.append((kwargs["input_tokens"], kwargs["output_tokens"]))

        async def run_sync(
            fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> Any:
            if cancel_during_booking and kwargs.get("input_tokens") == 11:
                reached.set()
                await release_booking.wait()
            return fn(*args, **kwargs)

        task = asyncio.create_task(
            _collect(
                iter_planned_read_events(
                    prepared,
                    scope={},
                    conversation=None,
                    messages=[],
                    callbacks=SchedulerCallbacks(
                        execute_tool=lambda *_args: {"status": "success", "data": []},
                        enforce_rate_limit=limit,
                        run_sync=run_sync,
                    ),
                )
            )
        )
        await asyncio.wait_for(reached.wait(), timeout=2)
        task.cancel()
        await asyncio.sleep(0)
        release_booking.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert attempts == (1 if cancel_during_booking else 2)

    asyncio.run(scenario())
    assert bookings == [(1, 1), (1, 1), (11, 13)]
    assert prepared.interrupted_result is not None
    assert prepared.interrupted_result.usage == TokenUsage(13, 15)


def test_planner_known_usage_survives_cancellation_before_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bookings: list[tuple[int, int]] = []

    async def scenario() -> None:
        correction = asyncio.Event()
        attempts = 0

        class PlannerProvider:
            async def complete(
                self, _messages: object, _tools: object
            ) -> AsyncIterator[ChatEvent]:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    yield TextChunkEvent("{}")
                    yield DoneEvent(TokenUsage(11, 13))
                else:
                    correction.set()
                    await asyncio.Event().wait()

        monkeypatch.setattr(
            "general_manager.chat.planned.planner.build_profile_provider",
            lambda _profile: PlannerProvider(),
        )

        def limit(_scope: object, **kwargs: Any) -> None:
            if "input_tokens" in kwargs:
                bookings.append((kwargs["input_tokens"], kwargs["output_tokens"]))

        task = asyncio.create_task(
            prepare_planned_turn(
                "show parts",
                [],
                _settings(),
                {},
                callbacks=SchedulerCallbacks(enforce_rate_limit=limit),
                scope={},
            )
        )
        await asyncio.wait_for(correction.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert bookings == [(11, 13)]


def test_in_flight_responses_are_booked_but_waiting_roots_start_no_new_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown concurrent usage can overshoot; a queued third root cannot follow."""
    provider_calls: list[object] = []
    tool_calls: list[str] = []

    async def scenario() -> None:
        both_started = asyncio.Event()

        class Provider:
            async def complete(
                self, messages: object, _tools: object
            ) -> AsyncIterator[ChatEvent]:
                provider_calls.append(messages)
                if len(provider_calls) == 2:
                    both_started.set()
                await both_started.wait()
                yield ToolCallEvent(
                    "q", "query", {"manager": "PartManager", "fields": ["name"]}
                )
                yield DoneEvent(TokenUsage(1, 1))

        monkeypatch.setattr(
            "general_manager.chat.planned.scheduler.build_profile_provider",
            lambda _profile: Provider(),
        )
        prepared = PreparedPlannedTurn.for_plan(
            ValidatedPlan("read", tuple(_task(f"task_{i}") for i in range(3))),
            replace(_settings(max_concurrent_tasks=2), evidence_timeout_seconds=0.5),
            user_text="show parts",
        )
        events = await _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(
                    execute_tool=lambda name, *_args: tool_calls.append(name)
                    or {"status": "success", "data": []},
                    enforce_rate_limit=enforce_chat_rate_limit,
                ),
            )
        )
        assert events[-1]["code"] == "rate_limited"

    with _quota(1):
        cache.clear()
        asyncio.run(scenario())
        assert cache.get("general_manager:chat_rate_limit:anonymous:tokens") == 4
    assert len(provider_calls) == 2
    assert tool_calls == []


def test_tool_waiting_for_backend_slot_rechecks_quota_before_execution() -> None:
    tool_calls: list[str] = []

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        runner = _Runner(
            _prepared(),
            {},
            None,
            [],
            SchedulerCallbacks(
                execute_tool=lambda name, *_args: tool_calls.append(name)
                or {"status": "success", "data": []},
                enforce_rate_limit=enforce_chat_rate_limit,
            ),
            loop.time() + 10,
            loop.time,
        )
        runtime = runner.runtimes["task_1"]
        runtime.status = "running"
        await runner.tool_semaphore.acquire()
        pending = asyncio.create_task(
            runner.execute_tool(
                runtime,
                ToolCallEvent(
                    "q", "query", {"manager": "PartManager", "fields": ["name"]}
                ),
            )
        )
        await asyncio.wait_for(runner.events.get(), timeout=2)
        # Usage from a different already in-flight root arrives while queued.
        enforce_chat_rate_limit({}, input_tokens=2, count_request=False)
        runner.tool_semaphore.release()
        result, progressed = await pending
        assert result == {"status": "error", "code": "rate_limited"}
        assert not progressed
        assert runtime.reason == "rate_limited"
        assert runner.call_cache == {}
        paired_result = await asyncio.wait_for(runner.events.get(), timeout=2)
        assert paired_result["type"] == "tool_result"

    with _quota(1):
        cache.clear()
        asyncio.run(scenario())
    assert tool_calls == []


def test_synthesis_admission_wait_remains_inside_its_existing_stage_deadline() -> None:
    from general_manager.chat.planned.budget import RoundBudget
    from general_manager.chat.planned.synthesis import (
        SynthesisFailedError,
        synthesize_answer,
    )
    from tests.unit.test_chat_planned_synthesis import (
        _SynthesisProvider,
        _settings as synthesis_settings,
        _store,
    )

    async def blocked_admission() -> None:
        await asyncio.Event().wait()

    async def scenario() -> None:
        _SynthesisProvider.calls = []
        budget = RoundBudget(())
        with pytest.raises(SynthesisFailedError, match="deadline_exceeded"):
            await asyncio.wait_for(
                synthesize_answer(
                    "show parts",
                    _store(),
                    {},
                    replace(synthesis_settings(), synthesis_timeout_seconds=0.02),
                    budget,
                    before_attempt=blocked_admission,
                ),
                timeout=0.2,
            )
        assert _SynthesisProvider.calls == []
        assert budget.global_used == 0

    asyncio.run(scenario())
