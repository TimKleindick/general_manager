"""Pending JSON-action feedback survives evidence gathering without gaining authority."""

import asyncio
from dataclasses import replace

import pytest

from general_manager.chat.providers.base import ToolCallEvent
from tests.unit.test_chat_planned_deferred_binding import deferred
from tests.unit.test_chat_planned_tool_feedback import _record_rounds, _reference
from tests.unit.test_chat_planned_error_cycles import _complete, _text
from tests.unit.test_chat_planned_multi_tool import (
    _BatchProvider,
    _configured_runner,
    _query,
)
from general_manager.chat.providers.base import DoneEvent, TokenUsage


def test_rejected_binding_survives_schema_failed_query_and_cached_discovery(
    monkeypatch,
):
    runner, runtime, actions, binding = deferred()
    before = runner.evidence.records
    executed = []

    def tool(name, args, *_):
        executed.append((name, args))
        if name == "get_manager_schema":
            return {"manager": "PartManager", "fields": ["quantity", "year"]}
        if name == "search_managers":
            return [{"manager": "PartManager"}]
        return {"status": "error", "code": "invalid_graphql_request"}

    runner.callbacks = replace(runner.callbacks, execute_tool=tool)
    schema_args = {"manager": "PartManager", "view": "overview"}
    bad = {**binding, "value_path": ["nestedList", "items", "quantity"]}
    calls = _record_rounds(
        monkeypatch,
        [
            {"action": "bind_calculation", "requirement_id": "annual", "binding": bad},
            ToolCallEvent("schema-1", "get_manager_schema", schema_args),
            ToolCallEvent(
                "query-1", "query", {"manager": "PartManager", "fields": ["quantity"]}
            ),
            ToolCallEvent("schema-2", "get_manager_schema", schema_args),
            ToolCallEvent("search-1", "search_managers", {"query": "parts"}),
            ToolCallEvent("search-2", "search_managers", {"query": "parts"}),
            {
                "action": "bind_calculation",
                "requirement_id": "annual",
                "binding": binding,
            },
            {"action": "calculate_batch", "calculations": actions},
        ],
    )

    async def run():
        assert await runner._execute_one_pass(runtime, ()) == "provider_failed"
        feedback = dict(runtime.action_validation_error)
        assert feedback["code"] == "invalid_calculation_binding"
        for _ in range(5):
            await runner._execute_one_pass(runtime, ())
            assert runtime.task.requirements[-1].binding is None
            assert runner.evidence.records == before
        assert await runner._execute_one_pass(runtime, ()) is None
        assert runtime.task.requirements[-1].binding.as_mapping() == binding
        assert await runner._execute_one_pass(runtime, ()) is None
        return feedback

    feedback = asyncio.run(run())
    assert all(
        _reference(call[1])["action_validation_error"] == feedback
        for call in calls[1:7]
    )
    assert "action_validation_error" not in _reference(calls[7][1])
    assert [name for name, _ in executed] == [
        "get_manager_schema",
        "query",
        "get_manager_schema",
        "search_managers",
    ]
    assert [
        r.payload()["value"] for r in runner.evidence.records if r.kind == "calculation"
    ] == [100, 120, 140]
    assert runner.prepared.budget.global_count == 8


def test_new_rejected_action_replaces_pending_feedback(monkeypatch):
    runner, runtime, _, binding = deferred()
    calls = _record_rounds(
        monkeypatch,
        [
            {
                "action": "bind_calculation",
                "requirement_id": "annual",
                "binding": {**binding, "value_path": ["missing"]},
            },
            {"action": "block", "reason": "invented_reason"},
            {
                "action": "bind_calculation",
                "requirement_id": "annual",
                "binding": binding,
            },
        ],
    )
    for _ in range(3):
        asyncio.run(runner._execute_one_pass(runtime, ()))
    assert (
        _reference(calls[1][1])["action_validation_error"]["code"]
        == "invalid_calculation_binding"
    )
    assert (
        _reference(calls[2][1])["action_validation_error"]["code"]
        == "invalid_block_reason"
    )
    assert runtime.action_validation_error is None


@pytest.mark.parametrize("distinct", [False, True])
def test_pending_action_feedback_does_not_conflate_failed_tool_calls(
    monkeypatch, distinct
):
    provider = _BatchProvider(
        [
            _text({"action": "invented"}),
            [_query("bad-one", "missingOne"), DoneEvent(TokenUsage(2, 1))],
            [
                _query("bad-two", "missingTwo" if distinct else "missingOne"),
                DoneEvent(TokenUsage(2, 1)),
            ],
            [_query("valid", "id"), DoneEvent(TokenUsage(2, 1))],
            _complete(),
        ]
    )
    executed = []

    def tool(name, args, *_):
        executed.append(args)
        if args["fields"] == ["id"]:
            return {"data": []}
        return {"status": "error", "code": "invalid_field", "field": args["fields"][0]}

    runner = _configured_runner(monkeypatch, provider, tool)
    runtime = runner.runtimes["root"]
    asyncio.run(runner.run_task(runtime))
    assert runtime.status == ("resolved" if distinct else "blocked")
    assert (
        len(provider.seen)
        == runner.prepared.budget.global_count
        == (5 if distinct else 3)
    )
    assert len(executed) == (3 if distinct else 1)
    pending = _reference(provider.seen[1])["action_validation_error"]
    assert all(
        _reference(messages)["action_validation_error"] == pending
        for messages in provider.seen[1:]
    )
    if distinct:
        assert runtime.action_validation_error is None
    else:
        assert runtime.reason == "manager_unresolved"
        assert runner.evidence.records == ()
