"""Optional round caps must not end otherwise valid executor work early."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Collection, Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

from general_manager.chat.planned.budget import RoundBudget, RoundBudgetExhausted
from general_manager.chat.planned.config import PlannedChatSettings, ProviderProfile
from general_manager.chat.planned.models import ValidatedPlan
from general_manager.chat.planned.planner import PlanningResult
from general_manager.chat.planned.resolver import ManagerResolver
from general_manager.chat.planned.scheduler import prepare_planned_turn
from general_manager.chat.providers.base import (
    ChatEvent,
    DoneEvent,
    Message,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
    ToolDefinition,
)
import pytest

from tests.unit.test_chat_planned_action_feedback import _runner, _task
from tests.unit.test_chat_planned_multi_tool import _BatchProvider
from tests.unit.test_chat_planned_scheduler import _StableExactResolver, _role_settings


def test_unlimited_budget_counts_beyond_prior_global_and_subtree_caps() -> None:
    budget = RoundBudget(["root"], enforce_limits=False)
    for _ in range(160):
        budget.consume_global()
        budget.consume_subtree("root")

    assert budget.global_count == budget.global_used == budget.count() == 320
    assert budget.subtree_count("root") == budget.count("root") == 160
    assert budget.subtree_counts == budget.subtree_used == {"root": 160}
    assert budget.global_limit is None
    assert budget.subtree_limit is None
    assert budget.global_remaining is budget.remaining_global is None
    assert budget.subtree_remaining["root"] is budget.subtree_remaining("root") is None
    assert budget.remaining == {"global": None, "root": None}


def _long_successful_script() -> list[list[ChatEvent]]:
    rounds: list[list[ChatEvent]] = []
    for index in range(83):
        call = (
            ToolCallEvent(
                f"search-{index}", "search_managers", {"query": f"parts {index}"}
            )
            if index % 2 == 0
            else ToolCallEvent(
                f"schema-{index}", "get_manager_schema", {"manager": "PartManager"}
            )
        )
        rounds.append([call, DoneEvent(TokenUsage(2, 1))])
    rounds.extend(
        [
            [
                ToolCallEvent(
                    "query", "query", {"manager": "PartManager", "fields": ["id"]}
                ),
                DoneEvent(TokenUsage(2, 1)),
            ],
            [
                TextChunkEvent('{"action":"complete","evidence_ids":["root:query:1"]}'),
                DoneEvent(TokenUsage(2, 1)),
            ],
        ]
    )
    return rounds


def _successful_tool(name: str, args: Mapping[str, Any], context: object) -> object:
    return {
        "search_managers": [{"manager": "PartManager"}],
        "get_manager_schema": {"manager": "PartManager", "fields": ["id"]},
        "query": {"data": [{"id": 3}]},
    }[name]


def test_normal_scheduler_finishes_85_finite_rounds_without_no_progress_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _BatchProvider(_long_successful_script())
    roles: list[str] = []

    def build(profile: ProviderProfile) -> _BatchProvider:
        roles.append(profile.name)
        return provider

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.build_profile_provider", build
    )
    executed: list[str] = []

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        executed.append(name)
        return _successful_tool(name, args, context)

    # The static clock is safe because the provider has a finite 85-response script.
    runner = _runner()
    runner.callbacks = replace(runner.callbacks, execute_tool=tool)
    runtime = runner.runtimes["root"]
    asyncio.run(runner.run_task(runtime))

    assert runtime.status == "resolved", (runtime.status, runtime.reason, roles)
    assert runtime.reason is None
    assert "fallback" not in roles
    assert not runtime.fallback_used
    assert len(provider.seen) == len(roles) == runtime.local_passes == 85
    assert provider.rounds == []
    assert runner.prepared.budget.global_count == 85
    assert runner.prepared.budget.subtree_count("root") == 85
    assert runner.prepared.budget.global_remaining is None
    assert runner.usage == TokenUsage(170, 85)
    assert [record.kind for record in runner.evidence.records] == ["query"]
    assert executed.count("query") == 1


def test_unknown_root_rejection_is_atomic_in_unlimited_mode() -> None:
    budget = RoundBudget(["root"], enforce_limits=False)
    for _ in range(160):
        budget.consume_subtree("root")
    before = (budget.global_count, budget.subtree_counts, budget.remaining)
    with pytest.raises(KeyError):
        budget.consume_subtree("unknown")
    with pytest.raises(KeyError):
        budget.consume("unknown")
    with pytest.raises(KeyError):
        budget.subtree_count("unknown")
    with pytest.raises(KeyError):
        budget.subtree_remaining("unknown")
    assert (budget.global_count, budget.subtree_counts, budget.remaining) == before


@pytest.mark.parametrize(
    ("root_ids", "expected_error"),
    [
        ("root", TypeError),
        (b"root", TypeError),
        (["root", "root"], ValueError),
        ([""], ValueError),
        (["   "], ValueError),
        ([None], ValueError),
        ([False], ValueError),
    ],
)
def test_unlimited_mode_keeps_root_identity_validation(
    root_ids: object, expected_error: type[Exception]
) -> None:
    with pytest.raises(expected_error):
        RoundBudget(cast(Collection[str], root_ids), enforce_limits=False)


@pytest.mark.parametrize("explicit_flag", [False, True])
def test_direct_bounded_budget_api_preserves_original_admission_and_counters(
    explicit_flag: bool,
) -> None:
    budget = (
        RoundBudget(["first", "second"], enforce_limits=True)
        if explicit_flag
        else RoundBudget(["first", "second"])
    )
    assert budget.global_limit == 31 and budget.subtree_limit == 15
    for _ in range(15):
        budget.consume_subtree("first")
    with pytest.raises(RoundBudgetExhausted):
        budget.consume_subtree("first")
    assert budget.global_count == budget.subtree_count("first") == 15
    assert budget.global_remaining == 16 and budget.subtree_remaining("first") == 0
    for _ in range(16):
        budget.consume_global()
    with pytest.raises(RoundBudgetExhausted):
        budget.consume_subtree("second")
    assert budget.global_count == 31 and budget.subtree_count("second") == 0


@pytest.mark.parametrize("limit_kind", ["global", "subtree"])
def test_explicit_numeric_override_remains_effective_in_unlimited_budget(
    limit_kind: str,
) -> None:
    budget = RoundBudget(["root"], enforce_limits=False)
    if limit_kind == "global":
        budget.global_limit = 2
    else:
        budget.subtree_limit = 2
    budget.consume_subtree("root")
    budget.consume_subtree("root")
    with pytest.raises(RoundBudgetExhausted):
        budget.consume_subtree("root")
    assert budget.global_count == budget.subtree_count("root") == 2
    assert budget.global_remaining == (0 if limit_kind == "global" else None)
    assert budget.subtree_remaining("root") == (0 if limit_kind == "subtree" else None)


def test_normal_preparation_preserves_planner_charges_and_usage_in_unlimited_ledger() -> (
    None
):
    plan = ValidatedPlan("read", (_task(),))
    attempts = (TokenUsage(2, 1),) * 3

    async def planner(
        user_text: str,
        messages: list[Message],
        settings: PlannedChatSettings,
        budget: RoundBudget,
        catalog: object,
    ) -> PlanningResult:
        for _ in attempts:
            budget.consume_global()
        return PlanningResult(plan, TokenUsage(6, 3), attempts)

    prepared = asyncio.run(
        prepare_planned_turn(
            "Read records",
            [],
            _role_settings(),
            {},
            planner=planner,
            resolver=cast(ManagerResolver, _StableExactResolver()),
            clock=lambda: 0.0,
        )
    )
    assert prepared.budget.global_limit is None
    assert prepared.budget.subtree_limit is None
    assert prepared.budget.global_count == 3
    assert prepared.budget.subtree_count("root") == 0
    assert prepared.usage == TokenUsage(6, 3)
    assert prepared.attempt_usages == attempts
    for _ in range(160):
        prepared.budget.consume_subtree("root")
    assert prepared.budget.global_count == 163
    assert prepared.budget.subtree_count("root") == 160


def test_explicit_bounded_ledger_still_stops_scheduler_at_its_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _BatchProvider(_long_successful_script())
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.build_profile_provider",
        lambda _: provider,
    )
    runner = _runner()
    runner.prepared.budget = RoundBudget(["root"])
    runner.callbacks = replace(runner.callbacks, execute_tool=_successful_tool)
    runtime = runner.runtimes["root"]
    asyncio.run(runner.run_task(runtime))
    assert (runtime.status, runtime.reason) == ("budget_exhausted", "budget_exhausted")
    assert len(provider.seen) == runner.prepared.budget.global_count == 15
    assert runner.prepared.budget.subtree_count("root") == 15
    assert runner.usage == TokenUsage(30, 15)
    assert not runtime.fallback_used


class _TransportFailureProvider(_BatchProvider):
    """Fail once; a finite terminal response makes an accidental retry observable."""

    def __init__(self) -> None:
        super().__init__(
            [
                [
                    TextChunkEvent('{"action":"block","reason":"manager_unresolved"}'),
                    DoneEvent(TokenUsage(1, 1)),
                ]
            ]
        )

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[ChatEvent]:
        if not self.seen:
            self.seen.append(deepcopy(messages))
            detail = "offline simulated transport failure"
            raise ConnectionError(detail)
        async for event in super().complete(messages, tools):
            yield event


def test_transport_failure_ends_task_without_automatic_provider_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _TransportFailureProvider()
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.build_profile_provider",
        lambda _: provider,
    )
    runner = _runner()
    runtime = runner.runtimes["root"]
    asyncio.run(runner.run_task(runtime))
    assert (runtime.status, runtime.reason) == ("blocked", "provider_failed")
    assert len(provider.seen) == runner.prepared.budget.global_count == 1
    assert runner.prepared.budget.subtree_count("root") == 1
    assert runner.usage == TokenUsage()
    assert runner.evidence.records == ()
    assert not runtime.fallback_used


def test_deadline_still_ends_unlimited_work_and_retains_completed_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _BatchProvider(_long_successful_script()[-2:])
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.build_profile_provider",
        lambda _: provider,
    )
    runner = _runner()
    now = [0.0]
    runner.clock = lambda: now[0]
    runner.deadline = 2.0

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        now[0] = 2.0
        return {"data": [{"id": 3}]}

    runner.callbacks = replace(runner.callbacks, execute_tool=tool)
    runtime = runner.runtimes["root"]
    asyncio.run(runner.run_task(runtime))
    assert (runtime.status, runtime.reason) == ("blocked", "deadline_exceeded")
    assert len(provider.seen) == runner.prepared.budget.global_count == 1
    assert runner.usage == TokenUsage(2, 1)
    assert [record.payload() for record in runner.evidence.records] == [
        {"data": [{"id": 3}]}
    ]
    assert not runtime.fallback_used


class _CancellableProvider(_BatchProvider):
    """One in-flight request with explicit cancellation observation."""

    def __init__(self) -> None:
        super().__init__([])
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[ChatEvent]:
        self.seen.append(deepcopy(messages))
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield DoneEvent(TokenUsage(1, 1))


def test_cancellation_still_stops_unlimited_provider_work_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _CancellableProvider()
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.build_profile_provider",
        lambda _: provider,
    )
    runner = _runner()

    async def cancel_request() -> None:
        loop = asyncio.get_running_loop()
        runner.clock = loop.time
        runner.deadline = loop.time() + 10.0
        execution = asyncio.create_task(runner.run_task(runner.runtimes["root"]))
        try:
            await asyncio.wait_for(provider.entered.wait(), 1.0)
        finally:
            execution.cancel()
            with pytest.raises(asyncio.CancelledError):
                await execution

    asyncio.run(cancel_request())
    assert provider.cancelled
    assert len(provider.seen) == runner.prepared.budget.global_count == 1
    assert runner.usage == TokenUsage()
    assert runner.evidence.records == ()
    assert not runner.runtimes["root"].fallback_used
