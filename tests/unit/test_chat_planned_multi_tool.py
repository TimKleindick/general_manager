"""Real provider-round and scheduler batch contracts, using offline providers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from copy import deepcopy
from dataclasses import replace
import json
from typing import Any, Self, cast

import pytest

from general_manager.chat.planned.provider_calls import complete_provider_round
from general_manager.chat.planned.scheduler import _Runner, _TaskRuntime
from general_manager.chat.providers.base import (
    ChatEvent,
    DoneEvent,
    Message,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
    ToolDefinition,
)
from tests.unit.test_chat_planned_action_feedback import _reference, _runner, _task


class _BatchProvider:
    """An explicit event stream; the real buffering/validation code still runs."""

    def __init__(self, rounds: list[list[ChatEvent]]) -> None:
        self.rounds = list(rounds)
        self.seen: list[list[Message]] = []

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> Self:
        detail = "Tests explicitly inject their offline provider"
        raise AssertionError(detail)

    @property
    def provider_config(self) -> Mapping[str, Any]:
        return {}

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[ChatEvent]:
        self.seen.append(deepcopy(messages))
        for event in self.rounds.pop(0):
            yield event


def _calls() -> tuple[ToolCallEvent, ToolCallEvent]:
    return (
        ToolCallEvent("schema-id", "get_manager_schema", {"manager": "PartManager"}),
        ToolCallEvent(
            "query-id", "query", {"manager": "PartManager", "fields": ["id"]}
        ),
    )


def _query(call_id: str, field: str = "id") -> ToolCallEvent:
    return ToolCallEvent(
        call_id, "query", {"manager": "PartManager", "fields": [field]}
    )


def _configured_runner(
    monkeypatch: pytest.MonkeyPatch,
    provider: _BatchProvider,
    tool: Callable[[str, Mapping[str, Any], object], object],
) -> _Runner:
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.build_profile_provider",
        lambda _: provider,
    )
    runner = _runner()
    runner.callbacks = replace(runner.callbacks, execute_tool=tool)
    return runner


def _tool_messages(runtime: _TaskRuntime) -> list[Message]:
    return [message for message in runtime.tool_history if message.role == "tool"]


def _assert_paired_history(
    runtime: _TaskRuntime, calls: list[ToolCallEvent] | tuple[ToolCallEvent, ...]
) -> None:
    declarations = [
        call
        for message in runtime.tool_history
        if message.role == "assistant"
        for call in message.tool_calls
    ]
    results = _tool_messages(runtime)
    assert declarations == list(calls)
    assert [message.tool_call_id for message in results] == [call.id for call in calls]
    assert [message.tool_name for message in results] == [call.name for call in calls]


def _assert_history_replayed(
    messages: list[Message], history: list[Message], evidence: list[dict[str, Any]]
) -> None:
    assert len(messages) == len(history)
    for observed, saved in zip(messages, history, strict=True):
        if (
            observed.role == "tool"
            and isinstance(observed.tool_result, dict)
            and set(observed.tool_result) == {"evidence_ref"}
        ):
            payload = next(
                item["payload"]
                for item in evidence
                if item["evidence_id"] == observed.tool_result["evidence_ref"]
            )
            assert json.loads(observed.content) == observed.tool_result
            assert payload == saved.tool_result
            observed = replace(observed, content=saved.content, tool_result=payload)
        assert (
            observed.role,
            observed.content,
            observed.tool_calls,
            observed.tool_call_id,
            observed.tool_name,
        ) == (
            saved.role,
            saved.content,
            saved.tool_calls,
            saved.tool_call_id,
            saved.tool_name,
        )
        if saved.role == "tool":
            assert observed.tool_result == saved.tool_result


def test_real_provider_round_accepts_two_ordered_calls_with_one_usage() -> None:
    calls = _calls()
    usage = TokenUsage(7, 3)
    provider = _BatchProvider([[*calls, DoneEvent(usage)]])
    result = asyncio.run(complete_provider_round(provider, [], [], 1.0))

    assert result.tool_calls == calls
    assert result.tool_call == calls[0]
    assert result.text == "" and result.usage == usage
    assert len(provider.seen) == 1


def test_real_round_scheduler_executes_and_pairs_two_calls_for_one_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _calls()
    provider = _BatchProvider([[*calls, DoneEvent(TokenUsage(7, 3))]])
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.build_profile_provider",
        lambda _: provider,
    )
    executed: list[str] = []
    payloads = {
        "get_manager_schema": {"manager": "PartManager", "fields": ["id"]},
        "query": {"data": [{"id": 3}], "total_count": 1, "has_more": False},
    }

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        executed.append(name)
        return payloads[name]

    runner = _runner()
    runner.callbacks = replace(runner.callbacks, execute_tool=tool)
    runtime = runner.runtimes["root"]
    outcome = asyncio.run(runner._execute_one_pass(runtime, ()))

    assert outcome is None
    assert executed == ["get_manager_schema", "query"]
    assert runner.prepared.budget.subtree_count("root") == 1
    assert runner.prepared.budget.global_count == len(provider.seen) == 1
    assert runner.usage == TokenUsage(7, 3)
    assert [
        message.tool_call_id
        for message in runtime.tool_history
        if message.role == "tool"
    ] == [call.id for call in calls]
    assert [
        message.tool_result
        for message in runtime.tool_history
        if message.role == "tool"
    ] == list(payloads.values())
    assert [record.kind for record in runner.evidence.records] == ["query"]
    events = [runner.events.get_nowait() for _ in range(runner.events.qsize())]
    assert [(event["type"], event["id"]) for event in events] == [
        ("tool_call", "schema-id"),
        ("tool_result", "schema-id"),
        ("tool_call", "query-id"),
        ("tool_result", "query-id"),
    ]


@pytest.mark.parametrize("schema_first", [True, False])
def test_batch_progress_aggregates_operational_schema_and_query(
    monkeypatch: pytest.MonkeyPatch, schema_first: bool
) -> None:
    calls = _calls()
    ordered = calls if schema_first else tuple(reversed(calls))
    provider = _BatchProvider([[*ordered, DoneEvent(TokenUsage(11, 5))]])
    executed: list[str] = []

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        executed.append(name)
        return (
            {"fields": ["id"]}
            if name == "get_manager_schema"
            else {"data": [{"id": 3}]}
        )

    runner = _configured_runner(monkeypatch, provider, tool)
    runtime = runner.runtimes["root"]
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert executed == [call.name for call in ordered]
    assert [record.kind for record in runner.evidence.records] == ["query"]
    assert runner.prepared.budget.subtree_count("root") == 1
    assert runner.usage == TokenUsage(11, 5)
    _assert_paired_history(runtime, ordered)


def test_same_arguments_with_different_ids_reuse_cache_and_keep_each_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = (_query("first"), _query("cached"))
    provider = _BatchProvider([[*calls, DoneEvent(TokenUsage(11, 5))]])
    executions: list[dict[str, Any]] = []
    payload = {"data": [{"id": 3}]}

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        executions.append(deepcopy(dict(args)))
        return payload

    runner = _configured_runner(monkeypatch, provider, tool)
    runtime = runner.runtimes["root"]
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert executions == [calls[0].args]
    assert len(runner.evidence.records) == 1
    assert len(runner.call_cache) == 1
    assert runner.prepared.budget.global_count == len(provider.seen) == 1
    assert runner.usage == TokenUsage(11, 5)
    _assert_paired_history(runtime, calls)
    assert [message.tool_result for message in _tool_messages(runtime)] == [
        payload,
        payload,
    ]
    assert runtime.tool_history[0].tool_calls == calls


@pytest.mark.parametrize("forbidden_first", [True, False])
def test_forbidden_mutation_cannot_execute_or_discard_a_legitimate_call(
    monkeypatch: pytest.MonkeyPatch, forbidden_first: bool
) -> None:
    forbidden = ToolCallEvent("forbidden", "mutate", {"manager": "PartManager"})
    legitimate = _query("allowed")
    calls = (forbidden, legitimate) if forbidden_first else (legitimate, forbidden)
    provider = _BatchProvider([[*calls, DoneEvent(TokenUsage(3, 2))]])
    executed: list[str] = []

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        executed.append(name)
        return {"data": [{"id": 3}]}

    runner = _configured_runner(monkeypatch, provider, tool)
    runtime = runner.runtimes["root"]
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert executed == ["query"]
    assert [record.kind for record in runner.evidence.records] == ["query"]
    _assert_paired_history(runtime, calls)
    results = {
        message.tool_call_id: message.tool_result for message in _tool_messages(runtime)
    }
    assert results["forbidden"] == {"status": "error", "code": "invalid_tool_call"}
    assert results["allowed"] == {"data": [{"id": 3}]}
    events = [runner.events.get_nowait() for _ in range(runner.events.qsize())]
    assert [event["id"] for event in events] == ["allowed", "allowed"]


@pytest.mark.parametrize("failure_first", [True, False])
def test_ordinary_tool_failure_retains_another_calls_progress(
    monkeypatch: pytest.MonkeyPatch, failure_first: bool
) -> None:
    failed = _query("failed", "unknown")
    successful = _query("successful")
    calls = (failed, successful) if failure_first else (successful, failed)
    provider = _BatchProvider([[*calls, DoneEvent(TokenUsage(3, 2))]])
    executed: list[str] = []

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        field = cast(list[str], args["fields"])[0]
        executed.append(field)
        if field == "unknown":
            raise LookupError(field)
        return {"data": [{"id": 3}]}

    runner = _configured_runner(monkeypatch, provider, tool)
    runtime = runner.runtimes["root"]
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert executed == [call.args["fields"][0] for call in calls]
    _assert_paired_history(runtime, calls)
    results = {
        message.tool_call_id: message.tool_result for message in _tool_messages(runtime)
    }
    assert results["failed"] == {"status": "error", "code": "tool_failed"}
    assert results["successful"] == {"data": [{"id": 3}]}
    assert len(runner.evidence.records) == 1


def _invalid_batch(case: str) -> list[ChatEvent]:
    first, second = _query("first"), _query("second", "name")
    done = DoneEvent(TokenUsage(3, 2))
    batches: dict[str, list[ChatEvent]] = {
        "duplicate_ids": [first, replace(second, id=first.id), done],
        "text_before": [TextChunkEvent("text"), first, second, done],
        "text_between": [first, TextChunkEvent("text"), second, done],
        "text_after": [first, second, TextChunkEvent("text"), done],
        "missing_done": [first, second],
        "duplicate_done": [first, second, done, done],
        "call_after_done": [first, done, second],
        "missing_id": [first, replace(second, id=""), done],
        "missing_name": [first, replace(second, name=""), done],
        "non_object_args": [
            first,
            replace(second, args=cast(dict[str, Any], [])),
            done,
        ],
        "nonfinite_args": [first, replace(second, args={"value": float("nan")}), done],
    }
    return batches[case]


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_ids",
        "text_before",
        "text_between",
        "text_after",
        "missing_done",
        "duplicate_done",
        "call_after_done",
        "missing_id",
        "missing_name",
        "non_object_args",
        "nonfinite_args",
    ],
)
def test_whole_invalid_batch_is_rejected_before_any_tool_effect(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    provider = _BatchProvider([_invalid_batch(case)])
    executed: list[str] = []

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        executed.append(name)
        return {"data": [{"id": 3}]}

    runner = _configured_runner(monkeypatch, provider, tool)
    runtime = runner.runtimes["root"]
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert executed == []
    assert runtime.tool_history == []
    assert runner.evidence.records == ()
    assert runner.call_cache == {}
    assert runner.events.empty()
    assert runner.prepared.budget.global_count == len(provider.seen) == 1
    assert runtime.action_validation_error is not None
    assert runtime.action_validation_error["code"] == "invalid_provider_round"


@pytest.mark.parametrize("count", [3, 17])
def test_provider_does_not_replace_the_removed_rule_with_a_two_call_cap(
    count: int,
) -> None:
    calls = tuple(_query(f"query-{index}") for index in range(count))
    provider = _BatchProvider([[*calls, DoneEvent(TokenUsage(8, 4))]])
    result = asyncio.run(complete_provider_round(provider, [], [], 1.0))
    assert result.tool_calls == calls
    assert result.tool_call == calls[0]
    assert result.usage == TokenUsage(8, 4)
    assert len(provider.seen) == 1


@pytest.mark.parametrize("second_cached", [True, False])
def test_deadline_between_calls_stops_execution_and_cache_acknowledgement(
    monkeypatch: pytest.MonkeyPatch, second_cached: bool
) -> None:
    calls = (_query("first"), _query("second", "id" if second_cached else "name"))
    provider = _BatchProvider([[*calls, DoneEvent(TokenUsage(8, 4))]])
    now = [0.0]
    executed: list[str] = []

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        executed.append(name)
        now[0] = 5.0
        return {"data": [{"id": 3}]}

    runner = _configured_runner(monkeypatch, provider, tool)
    runner.deadline = 5.0
    runner.clock = lambda: now[0]
    runtime = runner.runtimes["root"]
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert runtime.status == "blocked" and runtime.reason == "deadline_exceeded"
    assert executed == ["query"]
    _assert_paired_history(runtime, calls[:1])
    assert len(runner.evidence.records) == 1
    events = [runner.events.get_nowait() for _ in range(runner.events.qsize())]
    assert [event["id"] for event in events] == ["first", "first"]
    assert runner.usage == TokenUsage(8, 4)
    assert runner.prepared.budget.global_count == len(provider.seen) == 1


@pytest.mark.parametrize("second_cached", [True, False])
def test_cancellation_between_calls_stops_execution_and_cache_acknowledgement(
    monkeypatch: pytest.MonkeyPatch, second_cached: bool
) -> None:
    calls = (_query("first"), _query("second", "id" if second_cached else "name"))
    provider = _BatchProvider([[*calls, DoneEvent(TokenUsage(8, 4))]])
    executed: list[str] = []

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        executed.append(name)
        return {"data": [{"id": 3}]}

    runner = _configured_runner(monkeypatch, provider, tool)
    execute_tool = runner.execute_tool

    async def cancel_after_completed_call(
        runtime: _TaskRuntime, call: ToolCallEvent
    ) -> tuple[Any, bool]:
        result = await execute_tool(runtime, call)
        if call.id == "first":
            current = asyncio.current_task()
            assert current is not None
            current.cancel()
        return result

    monkeypatch.setattr(runner, "execute_tool", cancel_after_completed_call)
    runtime = runner.runtimes["root"]

    async def run_cancelled() -> None:
        execution = asyncio.create_task(runner._execute_one_pass(runtime, ()))
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(run_cancelled())
    assert executed == ["query"]
    _assert_paired_history(runtime, calls[:1])
    assert len(runner.evidence.records) == 1
    events = [runner.events.get_nowait() for _ in range(runner.events.qsize())]
    assert [event["id"] for event in events] == ["first", "first"]
    assert runner.usage == TokenUsage(8, 4)
    assert runner.prepared.budget.global_count == len(provider.seen) == 1


def test_three_calls_execute_in_order_without_an_extra_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = tuple(_query(f"query-{field}", field) for field in ("id", "name", "active"))
    provider = _BatchProvider([[*calls, DoneEvent(TokenUsage(8, 4))]])
    executed: list[list[str]] = []

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        fields = cast(list[str], args["fields"])
        executed.append(list(fields))
        return {"data": [{fields[0]: len(executed)}]}

    runner = _configured_runner(monkeypatch, provider, tool)
    runtime = runner.runtimes["root"]
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert executed == [["id"], ["name"], ["active"]]
    _assert_paired_history(runtime, calls)
    assert [message.tool_result for message in _tool_messages(runtime)] == [
        {"data": [{"id": 1}]},
        {"data": [{"name": 2}]},
        {"data": [{"active": 3}]},
    ]
    assert runner.prepared.budget.global_count == len(provider.seen) == 1
    assert runner.usage == TokenUsage(8, 4)


def test_operational_batch_feedback_does_not_become_evidence_or_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = _calls()[0]
    forbidden = ToolCallEvent("forbidden", "mutate", {"manager": "PartManager"})
    calls = (schema, forbidden)
    provider = _BatchProvider([[*calls, DoneEvent(TokenUsage(3, 2))]])
    runner = _configured_runner(monkeypatch, provider, lambda *_: {"fields": ["id"]})
    runtime = runner.runtimes["root"]
    before = runner._progress_signature(runtime)
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    _assert_paired_history(runtime, calls)
    assert runner.evidence.records == ()
    assert runner._progress_signature(runtime) == before
    assert not runner.requirements_satisfied(runtime)


def test_next_request_keeps_detached_batch_arguments_results_and_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_args: dict[str, Any] = {"manager": "PartManager", "fields": ["id"]}
    calls = (
        ToolCallEvent("first", "query", shared_args),
        ToolCallEvent("cached", "query", shared_args),
    )
    expected_calls = deepcopy(calls)
    payload: dict[str, Any] = {"data": [{"id": 3}]}
    expected_payload = deepcopy(payload)
    provider = _BatchProvider(
        [
            [*calls, DoneEvent(TokenUsage(3, 2))],
            [
                TextChunkEvent('{"action":"complete","evidence_ids":["root:query:1"]}'),
                DoneEvent(TokenUsage(2, 1)),
            ],
        ]
    )
    executions: list[dict[str, Any]] = []

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        executions.append(deepcopy(dict(args)))
        # A callback cannot alter the separately buffered second call.
        cast(list[str], args["fields"]).append("callback mutation")
        return payload

    runner = _configured_runner(monkeypatch, provider, tool)
    runtime = runner.runtimes["root"]
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert executions == [expected_calls[0].args]
    shared_args["fields"].append("later provider mutation")
    payload["data"][0]["id"] = 999
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert runtime.status == "resolved"
    _assert_paired_history(runtime, expected_calls)
    assert [message.tool_result for message in _tool_messages(runtime)] == [
        expected_payload,
        expected_payload,
    ]
    _assert_history_replayed(
        provider.seen[1][2:],
        runtime.tool_history,
        _reference(provider.seen[1])["task_evidence"],
    )
    assert _reference(provider.seen[1])["task_evidence"] == [
        {
            "evidence_id": "root:query:1",
            "kind": "query",
            "payload": expected_payload,
            "requirement_ids": ["query"],
        }
    ]
    assert runner.evidence.records[0].payload() == expected_payload


def test_batches_keep_history_and_evidence_task_local_and_cache_turn_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _calls()
    provider = _BatchProvider(
        [
            [*calls, DoneEvent(TokenUsage(3, 2))],
            [
                TextChunkEvent(
                    json.dumps(
                        {"action": "complete", "evidence_ids": ["first:query:1"]}
                    )
                ),
                DoneEvent(TokenUsage(2, 1)),
            ],
            [
                TextChunkEvent(
                    json.dumps(
                        {"action": "complete", "evidence_ids": ["first:query:1"]}
                    )
                ),
                DoneEvent(TokenUsage(2, 1)),
            ],
            [
                replace(calls[0], id="second-schema"),
                replace(calls[1], id="second-query"),
                DoneEvent(TokenUsage(3, 2)),
            ],
            [*calls, DoneEvent(TokenUsage(3, 2))],
        ]
    )
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.build_profile_provider",
        lambda _: provider,
    )
    executed: list[str] = []

    def tool(name: str, args: Mapping[str, Any], context: object) -> object:
        executed.append(name)
        return (
            {"fields": ["id"]}
            if name == "get_manager_schema"
            else {"data": [{"id": 3}]}
        )

    runner = _runner(_task("first"), _task("second"))
    runner.callbacks = replace(runner.callbacks, execute_tool=tool)
    first, second = runner.runtimes["first"], runner.runtimes["second"]
    assert asyncio.run(runner._execute_one_pass(first, ())) is None
    assert asyncio.run(runner._execute_one_pass(first, ())) is None
    assert first.status == "resolved"
    _assert_history_replayed(
        provider.seen[1][2:],
        first.tool_history,
        _reference(provider.seen[1])["task_evidence"],
    )
    assert asyncio.run(runner._execute_one_pass(second, ())) == "provider_failed"
    assert second.status == "running"
    assert provider.seen[2][2:] == []
    assert _reference(provider.seen[2])["task_evidence"] == []
    assert runner.evidence.for_task("second") == ()
    assert asyncio.run(runner._execute_one_pass(second, ())) is None
    assert [record.task_id for record in runner.evidence.for_task("second")] == [
        "second"
    ]
    assert [message.tool_call_id for message in _tool_messages(second)] == [
        "second-schema",
        "second-query",
    ]
    _assert_paired_history(first, calls)
    # Query results may be shared within a turn; schema observations stay fresh.
    assert executed == ["get_manager_schema", "query", "get_manager_schema"]
    next_turn = _runner(_task("first"))
    next_turn.callbacks = replace(next_turn.callbacks, execute_tool=tool)
    assert (
        asyncio.run(next_turn._execute_one_pass(next_turn.runtimes["first"], ()))
        is None
    )
    assert provider.seen[4][2:] == []
    assert _reference(provider.seen[4])["task_evidence"] == []
    assert executed == [
        "get_manager_schema",
        "query",
        "get_manager_schema",
        "get_manager_schema",
        "query",
    ]
    assert next_turn.prepared.budget.global_count == 1
    assert next_turn.usage == TokenUsage(3, 2)
