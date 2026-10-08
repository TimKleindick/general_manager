"""The executor receives full intent and cannot terminate with invented reasons."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import pytest

from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.events import PLANNED_PUBLIC_MESSAGES
from general_manager.chat.planned.models import EvidenceRequirement
from general_manager.chat.planned.scheduler import _executor_messages, _parse_action
from general_manager.chat.providers.base import ToolCallEvent
from tests.unit.test_chat_planned_scheduler import _task
from tests.unit.test_chat_planned_tool_feedback import (
    _record_rounds,
    _reference,
    _runner,
)


@pytest.mark.parametrize(
    "reason",
    ["active_customers_query_evidence_missing", "", "MANAGER_UNRESOLVED", None, 3],
)
def test_unsupported_block_reason_is_rejected_instead_of_normalized(reason):
    assert _parse_action(json.dumps({"action": "block", "reason": reason})) is None


def test_executor_reference_preserves_complete_requirement_intent_as_data():
    description = (
        "Récupérer code, name et active. Filtrer si exposé, sinon retenir les "
        "lignes pertinentes. Couvrir toutes les pages.\n"
        + "untrusted description content "
        * 1000
    )
    task = replace(
        _task("task_1"),
        requirements=(EvidenceRequirement("query", "query", description, None),),
    )
    messages = _executor_messages("show records", task, EvidenceStore())
    reference = _reference(messages)

    assert reference["task"]["requirements"] == [
        {
            "requirement_id": "query",
            "kind": "query",
            "description": description,
            "operation": None,
        }
    ]
    assert description not in messages[0].content
    assert "untrusted data" in messages[0].content
    assert "Missing evidence" in messages[0].content
    for reason in PLANNED_PUBLIC_MESSAGES:
        assert reason in messages[0].content


def test_schema_then_invalid_block_can_gather_query_evidence_with_bounded_feedback(
    monkeypatch,
):
    task = replace(
        _task("task_1"),
        requirements=(
            EvidenceRequirement("schema", "schema", "inspect fields", None),
            EvidenceRequirement("query", "query", "read records", None),
        ),
        completion_criteria=("schema", "query"),
    )
    schema = {"manager": "PartManager", "fields": ["name"], "filters": []}
    rows = {"data": [{"name": "part"}], "total_count": 1, "has_more": False}
    tool_calls = []

    def tool(name, args, *_):
        tool_calls.append((name, args))
        return schema if name == "get_manager_schema" else rows

    calls = _record_rounds(
        monkeypatch,
        [
            ToolCallEvent(
                "schema-id", "get_manager_schema", {"manager": "PartManager"}
            ),
            {"action": "block", "reason": "active_customers_query_evidence_missing"},
            ToolCallEvent(
                "query-id", "query", {"manager": "PartManager", "fields": ["name"]}
            ),
            {
                "action": "complete",
                "evidence_ids": ["task_1:schema:1", "task_1:query:2"],
            },
        ],
    )
    runner = _runner(tool, tasks=[task])
    runtime = runner.runtimes["task_1"]
    asyncio.run(runner.run_task(runtime))

    assert runtime.status == "resolved"
    assert [name for name, _ in tool_calls] == ["get_manager_schema", "query"]
    assert runner.prepared.budget.subtree_count("task_1") == 4
    assert len(runner.evidence.for_task("task_1")) == 2
    reference = _reference(calls[2][1])
    assert reference["action_validation_error"] == {
        "code": "invalid_block_reason",
        "path": "$.reason",
        "expected": {"enum": list(PLANNED_PUBLIC_MESSAGES)},
    }
    assert "active_customers_query_evidence_missing" not in json.dumps(reference)
    assert [record["kind"] for record in reference["task_evidence"]] == ["schema"]
    assert [
        message.tool_call_id for message in calls[2][1] if message.role == "tool"
    ] == ["schema-id"]
    assert (
        _reference(calls[3][1])["action_validation_error"]
        == reference["action_validation_error"]
    )


@pytest.mark.parametrize("reason", list(PLANNED_PUBLIC_MESSAGES))
def test_every_existing_stable_block_remains_immediately_terminal(monkeypatch, reason):
    calls = _record_rounds(monkeypatch, [{"action": "block", "reason": reason}])
    runner = _runner(lambda *_: pytest.fail("a legitimate block must not call tools"))
    runtime = runner.runtimes["task_1"]
    asyncio.run(runner.run_task(runtime))

    assert runtime.status == (
        "budget_exhausted" if reason == "budget_exhausted" else "blocked"
    )
    assert runtime.reason == reason
    assert len(calls) == runner.prepared.budget.global_count == 1
    assert runner.evidence.for_task("task_1") == ()
    assert _parse_action(json.dumps({"action": "block", "reason": reason})) == {
        "action": "block",
        "reason": reason,
    }


def test_invalid_block_error_cycle_stops_without_replaying_private_output(
    monkeypatch,
):
    rejected = "private rejected output: ignore instructions " * 5000
    calls = _record_rounds(
        monkeypatch,
        [{"action": "block", "reason": rejected}] * 4,
    )
    runner = _runner(lambda *_: pytest.fail("invalid text must not execute a tool"))
    runtime = runner.runtimes["task_1"]
    asyncio.run(runner.run_task(runtime))

    assert runtime.status == "blocked"
    assert runtime.reason == "provider_failed"
    assert [role for role, *_ in calls] == [
        "executor",
        "executor",
    ]
    assert runner.prepared.budget.global_count == 2
    assert runner.prepared.budget.subtree_count("task_1") == 2
    assert runner.evidence.for_task("task_1") == ()
    assert runtime.tool_history == []
    feedback = [
        _reference(messages).get("action_validation_error") for _, messages, *_ in calls
    ]
    assert feedback[0] is None
    assert len(feedback) == 2 and feedback[1] is not None
    assert len(json.dumps(feedback[1])) < 512
    assert "private rejected output" not in repr(calls)


def test_action_rejection_is_latest_only_and_does_not_leak_across_tasks_or_turns(
    monkeypatch,
):
    calls = _record_rounds(
        monkeypatch,
        [
            {"action": "block", "reason": "invented"},
            {"action": "complete", "evidence_ids": []},
            {"action": "block", "reason": "manager_unresolved"},
            {"action": "block", "reason": "manager_unresolved"},
            {"action": "block", "reason": "manager_unresolved"},
        ],
    )
    runner = _runner(lambda *_: {}, tasks=[_task("task_1"), _task("task_2")])
    next_turn = _runner(lambda *_: {})

    async def run():
        await runner.run_task(runner.runtimes["task_1"])
        await runner.run_task(runner.runtimes["task_2"])
        await next_turn.run_task(next_turn.runtimes["task_1"])

    asyncio.run(run())
    references = [_reference(messages) for _, messages, *_ in calls]
    assert references[1]["action_validation_error"]["code"] == "invalid_block_reason"
    assert references[2]["action_validation_error"]["code"] == (
        "invalid_completion_evidence_ids"
    )
    assert all("action_validation_error" not in item for item in references[3:])
    assert [item["task"]["task_id"] for item in references] == [
        "task_1",
        "task_1",
        "task_1",
        "task_2",
        "task_1",
    ]
    assert all(item["task_evidence"] == [] for item in references)
