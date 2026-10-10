"""Corrected queries and pages remain immutable, usable task evidence."""

import asyncio
import json
from dataclasses import replace

from general_manager.chat.planned.models import EvidenceRequirement
from general_manager.chat.planned.scheduler import _executor_messages, _tool_definitions
from general_manager.chat.providers.base import ToolCallEvent
from tests.unit.test_chat_planned_tool_feedback import (
    _runner,
    _reference,
    _record_rounds,
)
from tests.unit.test_chat_planned_scheduler import _task


def test_corrected_query_and_page_survive_empty_result_and_complete(monkeypatch):
    outputs = [
        {"data": [], "total_count": 0, "has_more": False},
        {"data": [{"code": "M02"}], "total_count": 2, "has_more": True},
        {"data": [{"code": "M04"}], "total_count": 2, "has_more": False},
    ]
    pending = iter(outputs)
    runner = _runner(lambda *_: next(pending))
    runtime = runner.runtimes["task_1"]
    calls = [
        ToolCallEvent(
            str(i), "query", {"manager": "PartManager", "fields": ["code"], "offset": i}
        )
        for i in range(3)
    ]
    rounds = _record_rounds(
        monkeypatch,
        [
            *calls,
            {
                "action": "complete",
                "evidence_ids": ["task_1:query:2", "task_1:query:3"],
            },
        ],
    )

    async def run():
        for _ in range(4):
            await runner._execute_one_pass(runtime, ())

    asyncio.run(run())
    records = runner.evidence.for_task("task_1")
    assert [record.payload() for record in records] == outputs
    assert runtime.status == "resolved"
    from general_manager.chat.planned.synthesis import synthesize_answer
    from general_manager.chat.planned.budget import RoundBudget
    from tests.unit.test_chat_planned_synthesis import _settings, _SynthesisProvider

    _SynthesisProvider.calls = []
    _SynthesisProvider.responses = [
        json.dumps(
            {
                "answer": "M02 and M04",
                "evidence_ids": [records[1].evidence_id, records[2].evidence_id],
            }
        )
    ]
    result = asyncio.run(
        synthesize_answer(
            "read",
            runner.evidence,
            {"resolved_task_ids": ["task_1"]},
            _settings(),
            RoundBudget(()),
        )
    )
    assert result.evidence_ids == (records[1].evidence_id, records[2].evidence_id)
    content = " ".join(message.content for message in _SynthesisProvider.calls[-1])
    assert "M02" in content and "M04" in content and records[1].evidence_id in content
    assert [
        row["payload"] for row in _reference(rounds[-1][1])["task_evidence"]
    ] == outputs
    assert [
        record.evidence_id
        for record in runner.evidence.for_requirement(
            "task_1", runtime.task.requirements[0]
        )
    ] == [record.evidence_id for record in records]
    assert asyncio.run(runner.execute_tool(runtime, calls[1]))[1] is False
    assert len(runner.evidence.records) == 3
    outputs[1]["data"].clear()
    assert records[1].payload()["data"] == [{"code": "M02"}]


def test_explicit_requirement_link_keeps_backend_cache_and_task_scope():
    requirements = tuple(
        EvidenceRequirement(name, "query", name, None) for name in ("first", "second")
    )
    task = replace(
        _task("task_1"),
        requirements=requirements,
        completion_criteria=("first", "second"),
    )
    executed = []
    runner = _runner(
        lambda _name, args, _context: executed.append(args) or {"data": []},
        tasks=[task],
    )
    runtime = runner.runtimes["task_1"]
    args = {"manager": "PartManager", "fields": ["code"]}

    async def run():
        await runner.execute_tool(runtime, ToolCallEvent("unlinked", "query", args))
        assert not runner.requirements_satisfied(runtime)
        for name in ("second", "first"):
            _, progress = await runner.execute_tool(
                runtime, ToolCallEvent(name, "query", {**args, "requirement_id": name})
            )
            assert progress
        bad, progress = await runner.execute_tool(
            runtime,
            ToolCallEvent("bad", "query", {**args, "requirement_id": "foreign"}),
        )
        assert bad["code"] == "invalid_requirement_id" and not progress

    asyncio.run(run())
    assert executed == [args]
    assert len(runner.evidence.records) == 1
    assert runner.requirements_satisfied(runtime)
    metadata = _reference(_executor_messages("read", task, runner.evidence))[
        "task_evidence"
    ][0]
    assert metadata["requirement_ids"] == ["first", "second"]
    query = next(tool for tool in _tool_definitions() if tool.name == "query")
    assert "requirement_id" in query.input_schema["properties"]
