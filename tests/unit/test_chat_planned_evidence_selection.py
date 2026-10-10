"""Explicit completion selection controls downstream evidence visibility."""

import asyncio
from dataclasses import replace
import pytest
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    canonical_call_identity,
)
from general_manager.chat.planned.calculations import (
    CalculationOperand,
    calculate_evidence,
)
from general_manager.chat.planned.models import EvidenceRequirement
from general_manager.chat.planned.synthesis import _evidence_data
from tests.unit.test_chat_planned_tool_feedback import (
    _runner,
    _record_rounds,
    _reference,
)
from tests.unit.test_chat_planned_scheduler import _task


def query(runner, task_id, evidence_id, customer):
    record = EvidenceRecord.create(
        evidence_id,
        task_id,
        "query",
        canonical_call_identity(
            "query", {"manager": "Customer", "filters": {"code": customer}}
        ),
        {"manager": "Customer", "tool": "query"},
        {"data": [{"code": customer, "id": 314}], "complete": True},
    )
    runner.evidence.add(
        record, requirement=runner.runtimes[task_id].task.requirements[0]
    )
    return record


def test_complete_selection_excludes_earlier_different_scope(monkeypatch):
    runner = _runner(lambda *_: pytest.fail("no tools"))
    query(runner, "task_1", "old", "C02")
    selected = query(runner, "task_1", "selected", "C01")
    _record_rounds(monkeypatch, [{"action": "complete", "evidence_ids": ["selected"]}])
    asyncio.run(runner._execute_one_pass(runner.runtimes["task_1"], ()))
    assert runner.result().selected_evidence_ids == {"task_1": ("selected",)}
    assert runner.synthesis_evidence() == (selected,)
    assert len(runner.evidence.records) == 2
    assert _evidence_data((selected,))[0]["call_identity"] == selected.call_identity


def test_dependent_executor_sees_only_selected_declared_successful_predecessor(
    monkeypatch,
):
    first = _task("first")
    dependent = replace(
        _task("dependent"), depends_on=("first",), routing_features=("has_dependency",)
    )
    unrelated = _task("unrelated")
    runner = _runner(
        lambda *_: pytest.fail("no tools"), tasks=[first, dependent, unrelated]
    )
    query(runner, "first", "old", "C02")
    query(runner, "first", "chosen", "C01")
    query(runner, "unrelated", "private", "C03")
    calls = _record_rounds(
        monkeypatch,
        [
            {"action": "complete", "evidence_ids": ["chosen"]},
            {"action": "block", "reason": "manager_unresolved"},
        ],
    )
    asyncio.run(runner._execute_one_pass(runner.runtimes["first"], ()))
    asyncio.run(runner._execute_one_pass(runner.runtimes["dependent"], ()))
    reference = _reference(calls[1][1])
    assert reference["task_evidence"] == []
    assert [item["evidence_id"] for item in reference["dependency_evidence"]] == [
        "chosen"
    ]
    assert reference["dependency_evidence"][0]["task_id"] == "first"
    assert reference["dependency_evidence"][0]["payload"]["data"][0]["id"] == 314
    assert "call_identity" in reference["dependency_evidence"][0]


def test_selected_calculation_includes_exact_transitive_sources(monkeypatch):
    base = _task("task_1")
    calc_req = EvidenceRequirement("sum", "calculation", "sum", "sum")
    task = replace(
        base,
        requirements=(*base.requirements, calc_req),
        completion_criteria=(*base.completion_criteria, "sum"),
        routing_features=("requires_calculation",),
    )
    runner = _runner(lambda *_: None, tasks=[task])
    query(runner, "task_1", "old", "C02")
    query(runner, "task_1", "chosen", "C01")
    calc = calculate_evidence(
        "sum",
        "task_1",
        "sum",
        [CalculationOperand("chosen", ("data", 0, "id"))],
        runner.evidence,
        require_linked=True,
    )
    runner.evidence.add(calc, requirement=calc_req)
    _record_rounds(monkeypatch, [{"action": "complete", "evidence_ids": ["sum"]}])
    asyncio.run(runner._execute_one_pass(runner.runtimes["task_1"], ()))
    assert runner.runtimes["task_1"].status == "resolved"
    assert {r.evidence_id for r in runner.synthesis_evidence()} == {"sum", "chosen"}


def test_selection_must_cover_every_declared_requirement(monkeypatch):
    base = _task("task_1")
    schema = EvidenceRequirement("schema", "schema", "schema", None)
    task = replace(
        base,
        requirements=(*base.requirements, schema),
        completion_criteria=(*base.completion_criteria, "schema"),
    )
    runner = _runner(lambda *_: None, tasks=[task])
    query(runner, "task_1", "q", "C01")
    runner.evidence.add(
        EvidenceRecord.create("schema", "task_1", "schema", "schema", {}, {}),
        requirement=schema,
    )
    _record_rounds(monkeypatch, [{"action": "complete", "evidence_ids": ["q"]}])
    asyncio.run(runner._execute_one_pass(runner.runtimes["task_1"], ()))
    assert runner.runtimes["task_1"].status == "running"
    assert (
        runner.runtimes["task_1"].action_validation_error["code"]
        == "unsatisfied_requirements"
    )


def test_child_calculation_is_recomputed_with_parent_owned_sources():
    from general_manager.chat.planned.models import CalculationBinding
    from general_manager.chat.planned.scheduler import _TaskRuntime
    from general_manager.chat.planned.evidence_selection import selected_evidence

    binding = CalculationBinding(("query",), ("value",), (), None)
    requirements = (
        EvidenceRequirement("query", "query", "rows", None),
        EvidenceRequirement("sum", "calculation", "sum", "sum", binding),
    )
    root = replace(
        _task("root"),
        requirements=requirements,
        completion_criteria=("query", "sum"),
        routing_features=("requires_calculation",),
    )
    child = replace(root, task_id="child", parent_id="root")
    runner = _runner(lambda *_: None, tasks=[root])
    runtime = _TaskRuntime(
        child, "root", status="resolved", selected_evidence_ids=("child:sum",)
    )
    runner.runtimes["child"] = runtime
    source = EvidenceRecord.create(
        "child:q",
        "child",
        "query",
        "query",
        {},
        {"data": [{"value": 3}], "complete": True, "total_count": 1, "has_more": False},
    )
    runner.evidence.add(source, requirement=requirements[0])
    result = calculate_evidence(
        "child:sum",
        "child",
        "sum",
        [CalculationOperand("child:q", ("data", 0, "value"))],
        runner.evidence,
        require_linked=True,
        binding=binding,
    )
    runner.evidence.add(result, requirement=requirements[1])
    runner._adopt_child_evidence(runner.runtimes["root"], runtime)
    parent = runner.evidence.for_requirement("root", requirements[1])[0]
    assert parent.payload()["value"] == 3
    assert parent.payload()["operands"][0]["evidence_id"] == "root:child:child:q"
    assert {
        record.task_id
        for record in selected_evidence(runner.evidence, "root", [parent.evidence_id])
    } == {"root"}
    assert runner.evidence.get("child:sum") is result


@pytest.mark.parametrize("after_validation", [99.0, 100.0, 101.0])
def test_completion_rechecks_deadline_after_real_coverage_validation(
    monkeypatch, after_validation
):
    from general_manager.chat.planned import scheduler

    runner = _runner(lambda *_: pytest.fail("no tools"))
    runtime = runner.runtimes["task_1"]
    query(runner, "task_1", "selected", "C01")
    original = scheduler.covers_requirement
    checked = []

    def validate(*args):
        result = original(*args)
        checked.append(result)
        runner.clock = lambda: after_validation
        return result

    monkeypatch.setattr(scheduler, "covers_requirement", validate)
    _record_rounds(monkeypatch, [{"action": "complete", "evidence_ids": ["selected"]}])
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert checked == [True]
    if after_validation >= runner.deadline:
        assert runtime.status == "blocked"
        assert runtime.reason == "deadline_exceeded"
        assert runtime.selected_evidence_ids == ()
        assert runner.synthesis_evidence() == ()
    else:
        assert runtime.status == "resolved"
        assert runtime.selected_evidence_ids == ("selected",)
