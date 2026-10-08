"""Executor feedback travels independently of evidence eligibility and progress."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import pytest

from general_manager.chat.planned.models import ValidatedPlan
from general_manager.chat.planned.provider_calls import ProviderRoundResult
from general_manager.chat.planned.scheduler import (
    PreparedPlannedTurn,
    SchedulerCallbacks,
    _Runner,
)
from general_manager.chat.providers.base import TokenUsage, ToolCallEvent
from tests.unit.test_chat_planned_scheduler import (
    _StableExactResolver,
    _role_settings,
    _task,
)


def _runner(execute_tool, *, tasks=None):
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", tuple(tasks or [_task("task_1")])),
        _role_settings(),
        user_text="show parts",
        resolver=_StableExactResolver(),
    )
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(
            execute_tool=execute_tool,
            emit_tool_called=lambda **_: None,
            enforce_rate_limit=None,
        ),
        100.0,
        lambda: 0.0,
    )
    for runtime in runner.runtimes.values():
        runtime.role = "executor"
        runtime.status = "running"
        runtime.candidates = ("PartManager",)
    return runner


def _record_rounds(monkeypatch, responses):
    pending = iter(responses)
    calls = []

    async def complete(provider, messages, tools, timeout):
        calls.append((provider.role, deepcopy(messages), tools, timeout))
        response = next(pending)
        return ProviderRoundResult(
            "" if isinstance(response, ToolCallEvent) else json.dumps(response),
            response if isinstance(response, ToolCallEvent) else None,
            TokenUsage(1, 1),
        )

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.complete_provider_round", complete
    )
    return calls


def _reference(messages):
    return json.loads(
        next(
            message.content
            for message in messages
            if message.role == "user" and message.content.startswith("REFERENCE_DATA=")
        ).removeprefix("REFERENCE_DATA=")
    )


def _pairs(messages):
    return messages[2:]


def test_discovery_schema_and_query_reach_next_executor_in_order_with_exact_ids(
    monkeypatch,
):
    discovery = [{"manager": "PartManager", "fields": ["id", "active"]}]
    schema = {
        "manager": "PartManager",
        "fields": [{"name": "active", "type": "Boolean"}],
    }
    rows = {"data": [{"id": 1, "active": True}], "total_count": 1, "has_more": False}
    outputs = {
        "search_managers": discovery,
        "get_manager_schema": schema,
        "query": rows,
    }
    runner = _runner(lambda name, *_: outputs[name])
    runtime = runner.runtimes["task_1"]
    events = [
        ToolCallEvent("search-id", "search_managers", {"query": "parts"}),
        ToolCallEvent("schema-id", "get_manager_schema", {"manager": "PartManager"}),
        ToolCallEvent(
            "query-id", "query", {"manager": "PartManager", "fields": ["id", "active"]}
        ),
    ]
    calls = _record_rounds(
        monkeypatch,
        [*events, {"action": "complete", "evidence_ids": ["task_1:query:1"]}],
    )

    async def run():
        for _ in range(4):
            await runner._execute_one_pass(runtime, ())

    asyncio.run(run())
    for round_index, (_, messages, _, _) in enumerate(calls):
        history = _pairs(messages)
        assert len(history) == round_index * 2
        for index, call in enumerate(events[:round_index]):
            assistant, tool = history[index * 2 : index * 2 + 2]
            assert assistant.role == "assistant" and assistant.tool_calls == (call,)
            assert tool.role == "tool"
            assert (tool.tool_call_id, tool.tool_name) == (call.id, call.name)
            expected = (
                {"evidence_ref": "task_1:query:1"}
                if call.name == "query"
                else outputs[call.name]
            )
            assert tool.tool_result == expected
            assert json.loads(tool.content) == expected
            if call.name == "query":
                assert _reference(messages)["task_evidence"][0]["payload"] == rows
        if round_index < 3:
            assert _reference(messages)["task_evidence"] == []
    assert runtime.status == "resolved"
    assert [record.kind for record in runner.evidence.records] == ["query"]
    assert runner.messages == []


def test_cached_results_keep_each_call_id_without_new_execution_or_progress(
    monkeypatch,
):
    executed = []
    payload = [{"manager": "PartManager"}]
    runner = _runner(lambda *args: executed.append(args) or payload)
    runtime = runner.runtimes["task_1"]
    requests = [
        ToolCallEvent(f"search-{index}", "search_managers", {"query": "parts"})
        for index in range(4)
    ]
    calls = _record_rounds(
        monkeypatch, [*requests, {"action": "block", "reason": "manager_unresolved"}]
    )
    asyncio.run(runner.run_task(runtime))
    assert [role for role, *_ in calls] == ["executor"] * 5
    assert len(executed) == 1
    assert [
        message.tool_call_id
        for message in _pairs(calls[-1][1])
        if message.role == "tool"
    ] == ["search-0", "search-1", "search-2", "search-3"]
    assert all(
        message.tool_result == payload
        for message in _pairs(calls[-1][1])
        if message.role == "tool"
    )
    assert runtime.reason == "manager_unresolved"
    assert runner.prepared.budget.subtree_count("task_1") == 5
    assert runner.evidence.records == ()


@pytest.mark.parametrize("failure", ["exception", "returned_error", "unadvertised"])
def test_failed_calls_are_visible_but_never_evidence_or_private_exception_text(
    monkeypatch, failure
):
    callbacks = []
    private_detail = "PRIVATE BACKEND DETAIL"

    def execute(*args):
        callbacks.append(args)
        if failure == "exception":
            raise RuntimeError(private_detail)
        return {"status": "error", "code": "invalid_filter"}

    runner = _runner(execute)
    runtime = runner.runtimes["task_1"]
    call = ToolCallEvent(
        "bad-id",
        "mutate" if failure == "unadvertised" else "query",
        {"manager": "PartManager"},
    )
    calls = _record_rounds(
        monkeypatch, [call, {"action": "complete", "evidence_ids": ["bad-id"]}]
    )

    async def run():
        await runner._execute_one_pass(runtime, ())
        return await runner._execute_one_pass(runtime, ())

    assert asyncio.run(run()) == "provider_failed"
    history = _pairs(calls[1][1])
    assert history[0].tool_calls == (call,)
    assert history[1].tool_call_id == call.id
    assert history[1].tool_result == {
        "status": "error",
        "code": {
            "exception": "tool_failed",
            "returned_error": "invalid_filter",
            "unadvertised": "invalid_tool_call",
        }[failure],
    }
    assert "PRIVATE BACKEND DETAIL" not in history[1].content
    assert runner.evidence.records == ()
    if failure == "unadvertised":
        assert callbacks == [] and runner.events.empty()


@pytest.mark.parametrize("bad_value", [float("nan"), object()])
def test_non_json_arguments_get_explicit_rejection_without_native_fabrication(
    monkeypatch, bad_value
):
    callbacks = []
    runner = _runner(lambda *args: callbacks.append(args) or {"data": []})
    runtime = runner.runtimes["task_1"]
    calls = _record_rounds(
        monkeypatch,
        [
            ToolCallEvent("invalid-id", "query", {"bad": bad_value}),
            ToolCallEvent(
                "valid-id", "query", {"manager": "PartManager", "fields": ["id"]}
            ),
            {"action": "complete", "evidence_ids": ["task_1:query:1"]},
        ],
    )

    async def run():
        await runner._execute_one_pass(runtime, ())
        assert runner.evidence.records == () and runner.events.empty()
        await runner._execute_one_pass(runtime, ())
        await runner._execute_one_pass(runtime, ())

    asyncio.run(run())
    rejection = _pairs(calls[1][1])
    assert len(rejection) == 1 and rejection[0].role == "user"
    detail = json.loads(rejection[0].content.removeprefix("REJECTED_TOOL_CALL="))
    assert detail == {
        "id": "invalid-id",
        "name": "query",
        "arguments_rejected": True,
        "result": {"status": "error", "code": "invalid_tool_call"},
    }
    assert rejection[0].tool_calls == ()
    assert len(callbacks) == 1
    assert [
        message.tool_call_id
        for message in _pairs(calls[2][1])
        if message.role == "tool"
    ] == ["valid-id"]
    assert runtime.status == "resolved"


def test_tool_snapshots_survive_mutation_and_never_cross_tasks_or_turns(monkeypatch):
    payload = {"fields": [{"name": "active"}]}
    runner = _runner(lambda *_: payload, tasks=[_task("task_1"), _task("task_2")])
    first, second = runner.runtimes.values()
    call = ToolCallEvent("schema-id", "get_manager_schema", {"manager": "PartManager"})
    calls = _record_rounds(
        monkeypatch,
        [
            call,
            {"action": "block", "reason": "manager_unresolved"},
            {"action": "block", "reason": "manager_unresolved"},
            {"action": "block", "reason": "manager_unresolved"},
        ],
    )

    async def run():
        await runner._execute_one_pass(first, ())
        payload["fields"][0]["name"] = "changed"
        call.args["manager"] = "ChangedManager"
        await runner._execute_one_pass(first, ())
        await runner._execute_one_pass(second, ())
        next_turn = _runner(lambda *_: payload)
        await next_turn._execute_one_pass(next_turn.runtimes["task_1"], ())

    asyncio.run(run())
    history = _pairs(calls[1][1])
    assert history[0].tool_calls[0].args == {"manager": "PartManager"}
    assert history[1].tool_result == {"fields": [{"name": "active"}]}
    assert _pairs(calls[2][1]) == []
    assert _pairs(calls[3][1]) == []


def test_forwarded_large_history_still_hits_existing_siwc_guard_before_transport(
    monkeypatch,
):
    from experiments.siwc_eval.errors import EvalError
    from experiments.siwc_eval.provider import Provider

    runner = _runner(lambda *_: {"description": "x" * 200_001})
    runtime = runner.runtimes["task_1"]
    calls = _record_rounds(
        monkeypatch,
        [
            ToolCallEvent("large", "get_manager_schema", {"manager": "PartManager"}),
            {"action": "block", "reason": "manager_unresolved"},
        ],
    )

    class NeverTransport:
        calls = 0

        async def stream(self, body):
            self.calls += 1
            yield {"type": "response.completed", "response": {"output": []}}

    transport = NeverTransport()
    provider = Provider("offline", transport, max_requests=1)

    async def run():
        await runner._execute_one_pass(runtime, ())
        await runner._execute_one_pass(runtime, ())
        with pytest.raises(EvalError, match="input_budget_exhausted"):
            _ = [event async for event in provider.complete(calls[1][1], [])]

    asyncio.run(run())
    assert len(_pairs(calls[1][1])[1].tool_result["description"]) == 200_001
    assert transport.calls == provider.requests == 0


def test_native_contract_error_reaches_executor_without_becoming_evidence():
    from general_manager.chat.graphql_contract import ChatReadContractError

    def fail(*_):
        message = "Unknown GraphQL field 'is_active'; inspect get_manager_schema (contract 2)."
        raise ChatReadContractError(message)

    runner = _runner(fail)
    runtime = runner.runtimes["task_1"]
    result, valid = asyncio.run(
        runner.execute_tool(
            runtime,
            ToolCallEvent(
                "bad-native",
                "query",
                {"manager": "PartManager", "fields": ["is_active"]},
            ),
        )
    )
    assert result["code"] == "invalid_graphql_request"
    assert "contract 2" in result["message"]
    assert not valid
    assert runner.evidence.for_task("task_1") == ()
