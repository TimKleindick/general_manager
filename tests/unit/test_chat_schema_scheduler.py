"""Selective schemas cannot bypass freshness or selected-evidence coverage."""

import asyncio

from general_manager.chat.planned.evidence_selection import covers_requirement
from general_manager.chat.planned.models import PlannedTask
from general_manager.chat.providers.base import ToolCallEvent
from tests.unit.test_chat_planned_tool_feedback import _runner
from tests.unit.test_chat_schema_requirements import (
    B,
    requirement,
    record,
    add_observed,
)


def test_selected_schema_fragments_must_cover_required_types():
    from general_manager.chat.planned.evidence import EvidenceStore

    req = requirement()
    store = EvidenceStore()
    f = record("filter")
    e = record("enum", names=("State",))
    add_observed(store, f, requirement=req)
    add_observed(store, e, requirement=req)
    assert not covers_requirement(store, "t", req, [f])
    assert covers_requirement(store, "t", req, [f, e])


def test_schema_calls_are_fresh_and_snapshot_aware_without_false_completion():
    req = requirement("overview", [])
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    outputs = [
        record("first", "overview").payload(),
        record("second", "overview", snapshot=B).payload(),
        {"status": "error", "code": "schema_snapshot_mismatch"},
        record("fresh", "overview", snapshot=B).payload(),
    ]
    calls = []
    runner = _runner(lambda *args: calls.append(args) or outputs.pop(0), tasks=[task])
    runtime = runner.runtimes["t"]

    async def run():
        for index in range(4):
            result, progress = await runner.execute_tool(
                runtime,
                ToolCallEvent(str(index), "get_manager_schema", {"manager": "Part"}),
            )
            if index == 0:
                assert runner.requirements_satisfied(runtime)
                assert len(runner.evidence.records) == 1
            elif index == 1:
                assert len(runner.evidence.records) == 2
                assert (
                    runner.evidence.for_requirement("t", req)[0].payload()["snapshot"]
                    == B
                )
                assert progress
            elif index == 2:
                assert result["status"] == "error"
                assert not runner.requirements_satisfied(runtime)
            else:
                assert runner.requirements_satisfied(runtime)

    asyncio.run(run())
    assert len(calls) == 4
    assert runner.call_cache == {}
    assert runner.evidence.records[-1].provenance["snapshot"] == B
    assert runner.evidence.records[-1].provenance["schema_view"] == "overview"


def test_extra_overview_does_not_complete_legacy_full_requirement():
    from general_manager.chat.planned.models import EvidenceRequirement

    req = EvidenceRequirement("s", "schema", "Inspect Part filters", None)
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    runner = _runner(lambda *_: record("overview", "overview").payload(), tasks=[task])
    runtime = runner.runtimes["t"]
    asyncio.run(
        runner.execute_tool(
            runtime, ToolCallEvent("call", "get_manager_schema", {"manager": "Part"})
        )
    )
    assert len(runner.evidence.records) == 1
    assert not runner.requirements_satisfied(runtime)
    assert runner.evidence.for_requirement("t", req) == ()


def test_failed_schema_capture_revokes_prior_schema_proof():
    req = requirement("overview", [])
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    pending = [
        record("ok", "overview").payload(),
        RuntimeError("invalid runtime default"),
    ]

    def tool(*_):
        value = pending.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    runner = _runner(tool, tasks=[task])
    runtime = runner.runtimes["t"]

    async def run():
        await runner.execute_tool(
            runtime, ToolCallEvent("first", "get_manager_schema", {"manager": "Part"})
        )
        assert runner.requirements_satisfied(runtime)
        result, _ = await runner.execute_tool(
            runtime, ToolCallEvent("failed", "get_manager_schema", {"manager": "Part"})
        )
        assert result == {"status": "error", "code": "tool_failed"}
        assert not runner.requirements_satisfied(runtime)

    asyncio.run(run())
