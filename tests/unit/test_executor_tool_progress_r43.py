"""Repeated successful reads report real evidence/link changes without blocking work."""

import asyncio
from dataclasses import replace

from general_manager.chat.planned.models import EvidenceRequirement, PlannedTask
from general_manager.chat.providers.base import ToolCallEvent
from tests.unit.test_chat_planned_tool_feedback import (
    _record_rounds,
    _reference,
    _runner,
)
from tests.unit.test_chat_schema_requirements import A, B, record, requirement


def _run_passes(runner, runtime, count):
    async def run():
        return [await runner._execute_one_pass(runtime, ()) for _ in range(count)]

    return asyncio.run(run())


def _changed(messages):
    return _reference(messages)["last_tool_round"]["evidence_or_links_changed"]


def test_duplicate_query_feedback_keeps_exact_payload_cache_and_completion(monkeypatch):
    executed = []
    rows = {"data": [], "complete": True, "has_more": False, "total_count": 0}
    runner = _runner(lambda *args: executed.append(args) or rows)
    runtime = runner.runtimes["task_1"]
    calls = _record_rounds(
        monkeypatch,
        [
            ToolCallEvent(
                "first", "query", {"manager": "PartManager", "fields": ["id"]}
            ),
            ToolCallEvent(
                "again", "query", {"manager": "PartManager", "fields": ["id"]}
            ),
            {"action": "complete", "evidence_ids": ["task_1:query:1"]},
        ],
    )
    assert _run_passes(runner, runtime, 3) == [None, None, None]
    assert "last_tool_round" not in _reference(calls[0][1])
    assert [_changed(c[1]) for c in calls[1:]] == [True, False]
    assert runtime.status == "resolved"
    assert len(executed) == len(runner.evidence.records) == 1
    assert runner.evidence.records[0].payload() == rows
    assert runner.prepared.budget.subtree_count("task_1") == 3
    instruction = " ".join(m.content for m in calls[-1][1] if m.role == "system")
    assert "last_tool_round" in instruction
    assert "bind_calculation" in instruction
    assert "completion authority" in instruction
    assert "last_tool_round" not in runtime.execution_state


def test_cached_result_linked_to_another_requirement_is_real_progress(monkeypatch):
    requirements = tuple(
        EvidenceRequirement(name, "query", "Read requested rows", None)
        for name in ("first", "second")
    )
    task = PlannedTask("t", "Read rows", (), requirements, ("first", "second"), ())
    executed = []
    runner = _runner(lambda *args: executed.append(args) or {"data": []}, tasks=[task])
    runtime = runner.runtimes["t"]
    args = {"manager": "PartManager", "fields": ["id"]}
    calls = _record_rounds(
        monkeypatch,
        [
            ToolCallEvent("first", "query", {**args, "requirement_id": "first"}),
            ToolCallEvent("second", "query", {**args, "requirement_id": "second"}),
            ToolCallEvent("repeat", "query", {**args, "requirement_id": "second"}),
            {"action": "complete", "evidence_ids": ["t:query:1"]},
        ],
    )
    assert _run_passes(runner, runtime, 4) == [None] * 4
    assert [_changed(c[1]) for c in calls[1:]] == [True, True, False]
    assert runtime.status == "resolved" and len(executed) == 1
    assert all(runner.evidence.for_requirement("t", req) for req in requirements)


def test_identical_schema_reads_recheck_snapshot_and_changed_snapshot_adds_evidence(
    monkeypatch,
):
    req = requirement("overview", [])
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    outputs = [
        record(str(i), "overview", snapshot=s).payload()
        for i, s in enumerate((A, A, B, B))
    ]
    executed = []
    runner = _runner(
        lambda *args: executed.append(args) or outputs.pop(0), tasks=[task]
    )
    runtime = runner.runtimes["t"]
    calls = _record_rounds(
        monkeypatch,
        [
            *(
                ToolCallEvent(str(i), "get_manager_schema", {"manager": "Part"})
                for i in range(4)
            ),
            {"action": "complete", "evidence_ids": ["t:schema:2"]},
        ],
    )
    assert _run_passes(runner, runtime, 5) == [None] * 5
    assert [_changed(c[1]) for c in calls[1:]] == [True, False, True, False]
    assert len(executed) == 4 and runner.call_cache == {}
    assert len(runner.evidence.records) == 2
    assert runner.evidence.for_requirement("t", req)[0].payload()["snapshot"] == B
    assert runtime.status == "resolved"


def test_repeat_feedback_does_not_bind_calculation_or_allow_unsupported_completion(
    monkeypatch,
):
    runner = _runner(lambda *_: {"data": [{"kind": "actual", "currency": "EUR"}]})
    runtime = runner.runtimes["task_1"]
    calc = EvidenceRequirement(
        "total",
        "calculation",
        "Calculate requested amount",
        "sum",
        binding_required=True,
    )
    runtime.task = replace(
        runtime.task,
        requirements=(*runtime.task.requirements, calc),
        completion_criteria=(*runtime.task.completion_criteria, "total"),
    )
    calls = _record_rounds(
        monkeypatch,
        [
            ToolCallEvent(
                str(i),
                "query",
                {"manager": "PartManager", "fields": ["kind", "currency"]},
            )
            for i in range(2)
        ]
        + [{"action": "complete", "evidence_ids": ["task_1:query:1"]}],
    )
    assert _run_passes(runner, runtime, 3) == [None, None, "provider_failed"]
    ref = _reference(calls[-1][1])
    assert _changed(calls[-1][1]) is False
    assert ref["deferred_calculations"][0]["binding_validated"] is False
    assert runtime.task.requirements[-1].binding is None
    assert runtime.status == "running"
    assert not any(r.kind == "calculation" for r in runner.evidence.records)


def test_changed_fields_after_repeated_discovery_are_new_evidence(monkeypatch):
    executed = []

    def execute(name, args, context):
        executed.append(args)
        return {"data": [{field: "observed" for field in args["fields"]}]}

    runner = _runner(execute)
    runtime = runner.runtimes["task_1"]
    calls = _record_rounds(
        monkeypatch,
        [
            *(
                ToolCallEvent(
                    str(i), "query", {"manager": "PartManager", "fields": fields}
                )
                for i, fields in enumerate((["kind"], ["kind"], ["amount", "bookedAt"]))
            ),
            {"action": "block", "reason": "manager_unresolved"},
        ],
    )
    assert _run_passes(runner, runtime, 4) == [None] * 4
    assert [_changed(c[1]) for c in calls[1:]] == [True, False, True]
    assert len(executed) == len(runner.evidence.records) == 2
    assert runner.evidence.records[-1].payload()["data"][0] == {
        "amount": "observed",
        "bookedAt": "observed",
    }


def test_shared_read_guidance_preserves_observed_date_scalar_for_utc_intervals():
    from general_manager.chat.planned.evidence import EvidenceStore
    from general_manager.chat.planned.scheduler import _executor_messages
    from general_manager.chat.tool_metadata import READ_TOOL_GUIDANCE
    from tests.unit.test_chat_planned_scheduler import _task

    messages = _executor_messages(
        "Read the requested calendar interval", _task("t"), EvidenceStore()
    )
    instruction = " ".join(m.content for m in messages if m.role == "system")
    assert READ_TOOL_GUIDANCE in instruction
    assert "Date uses YYYY-MM-DD" in instruction
    assert "DateTime uses an ISO 8601 timestamp" in instruction
    assert "UTC interval" in instruction
    assert "do not turn a Date into a DateTime" in instruction
