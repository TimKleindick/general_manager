"""Rejected completion exposes verified missing coverage and permits repair."""

import asyncio
from dataclasses import replace
import json
import pytest

from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.calculations import (
    CalculationOperand,
    calculate_evidence,
)
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    canonical_call_identity,
)
from general_manager.chat.planned.models import CalculationBinding, EvidenceRequirement
from general_manager.chat.planned.synthesis import synthesize_answer
from tests.unit.test_chat_planned_scheduler import _task
from tests.unit.test_chat_planned_synthesis import _SynthesisProvider, _settings
from tests.unit.test_chat_planned_tool_feedback import (
    _runner,
    _record_rounds,
    _reference,
)


def fixture(groups=(1, 2, 3, 4, 5, 6, 7, 8, 9)):
    query_req = EvidenceRequirement("shipments", "query", "Read shipments", None)
    binding = CalculationBinding(
        ("shipments",), ("quantity",), (("projectId",),), ("unit",)
    )
    calc_req = EvidenceRequirement(
        "totals", "calculation", "Project totals", "sum", binding
    )
    task = replace(
        _task("task_1"),
        requirements=(query_req, calc_req),
        completion_criteria=("shipments", "totals"),
        routing_features=("requires_calculation",),
    )
    runner = _runner(lambda *_: pytest.fail("no tools"), tasks=[task])
    rows = [
        {
            "projectId": group,
            "quantity": group * 2,
            "unit": "pieces",
            "customer": "C01",
            "year": 2025,
        }
        for group in groups
    ]
    source = EvidenceRecord.create(
        "q",
        "task_1",
        "query",
        canonical_call_identity(
            "query", {"manager": "Shipment", "filters": {"year": 2025}}
        ),
        {"tool": "query"},
        {"data": rows, "complete": True, "has_more": False, "total_count": len(rows)},
    )
    runner.evidence.add(source, requirement=query_req)
    return runner, binding, calc_req


def add_total(runner, binding, requirement, group, *, name=None):
    source = runner.evidence.get("q")
    operands = [
        CalculationOperand("q", ("data", index, "quantity"))
        for index, row in enumerate(source.payload()["data"])
        if row["projectId"] == group
    ]
    result = calculate_evidence(
        name or f"total-{group}",
        "task_1",
        "sum",
        operands,
        runner.evidence,
        require_linked=True,
        binding=binding,
    )
    runner.evidence.add(result, requirement=requirement)
    return result


def reject(monkeypatch, runner, ids):
    _record_rounds(monkeypatch, [{"action": "complete", "evidence_ids": ids}])
    reason = asyncio.run(runner._execute_one_pass(runner.runtimes["task_1"], ()))
    assert reason == "provider_failed"
    assert runner.runtimes["task_1"].status == "running"
    assert runner.synthesis_evidence() == ()
    return runner.runtimes["task_1"].action_validation_error


def test_missing_groups_feedback_then_corrected_completion_and_synthesis(monkeypatch):
    runner, binding, req = fixture()
    selected = [
        add_total(runner, binding, req, group).evidence_id
        for group in [1, 3, 4, 5, 6, 7]
    ]
    before = runner.evidence.records
    feedback = reject(monkeypatch, runner, selected)
    assert feedback["code"] == "unsatisfied_requirements"
    missing = json.loads(feedback["detail"])["requirements"]
    assert missing == [
        {
            "requirement_id": "totals",
            "kind": "calculation",
            "code": "incomplete_group_coverage",
            "query_evidence_id": "q",
            "missing_groups": [[2], [8], [9]],
            "duplicate_groups": [],
            "unexpected_groups": [],
        }
    ]
    assert runner.evidence.records == before
    selected += [
        add_total(runner, binding, req, group).evidence_id for group in [2, 8, 9]
    ]
    calls = _record_rounds(
        monkeypatch, [{"action": "complete", "evidence_ids": selected}]
    )
    assert asyncio.run(runner._execute_one_pass(runner.runtimes["task_1"], ())) is None
    assert _reference(calls[0][1])["action_validation_error"] == feedback
    assert runner.runtimes["task_1"].status == "resolved"
    assert {record.evidence_id for record in runner.synthesis_evidence()} == {
        "q",
        *selected,
    }
    _SynthesisProvider.responses = [
        json.dumps(
            {"answer": "All project groups are covered.", "evidence_ids": selected}
        )
    ]
    result = asyncio.run(
        synthesize_answer(
            "Project totals",
            runner.synthesis_evidence(),
            {"resolved": 1, "total": 1},
            _settings(),
            RoundBudget(()),
        )
    )
    assert result.evidence_ids == tuple(selected)


def test_missing_requirement_is_named_without_invented_groups(monkeypatch):
    runner, _, _ = fixture()
    feedback = reject(monkeypatch, runner, ["q"])
    assert json.loads(feedback["detail"]) == {
        "requirements": [
            {
                "requirement_id": "totals",
                "kind": "calculation",
                "code": "missing_selected_evidence",
            }
        ]
    }


def test_duplicate_group_does_not_mask_missing_group(monkeypatch):
    runner, binding, req = fixture([1, 2])
    one = add_total(runner, binding, req, 1)
    duplicate = add_total(runner, binding, req, 1, name="duplicate-one")
    feedback = reject(monkeypatch, runner, [one.evidence_id, duplicate.evidence_id])
    detail = json.loads(feedback["detail"])["requirements"][0]
    assert detail["missing_groups"] == [[2]]
    assert detail["duplicate_groups"] == [[1]]


def test_other_query_population_is_not_combined_with_selected_source(monkeypatch):
    runner, binding, req = fixture([1, 2])
    one = add_total(runner, binding, req, 1)
    q = runner.evidence.get("q")
    other = EvidenceRecord.create(
        "other", "task_1", "query", "other-query", {}, q.payload()
    )
    runner.evidence.add(
        other, requirement=runner.runtimes["task_1"].task.requirements[0]
    )
    calc = calculate_evidence(
        "other-total",
        "task_1",
        "sum",
        [CalculationOperand("other", ("data", 1, "quantity"))],
        runner.evidence,
        require_linked=True,
        binding=binding,
    )
    runner.evidence.add(calc, requirement=req)
    feedback = reject(monkeypatch, runner, [one.evidence_id, calc.evidence_id])
    assert (
        json.loads(feedback["detail"])["requirements"][0]["code"]
        == "incompatible_query_populations"
    )


@pytest.mark.parametrize(
    "forgery", ["groups", "population_complete", "query_evidence_id", "value"]
)
def test_untrusted_calculation_metadata_produces_no_coverage_claim(
    monkeypatch, forgery
):
    runner, binding, req = fixture([1, 2])
    calc = add_total(runner, binding, req, 1)
    payload = calc.payload()
    if forgery == "groups":
        payload["scope"]["groups"] = [[1], [2]]
    elif forgery == "population_complete":
        payload["scope"]["population_complete"] = False
    elif forgery == "query_evidence_id":
        payload["scope"]["query_evidence_id"] = "invented"
    else:
        payload["value"] = 1000
    forged = EvidenceRecord.create(
        "forged", "task_1", "calculation", calc.call_identity, calc.provenance, payload
    )
    runner.evidence.add(forged, requirement=req)
    feedback = reject(monkeypatch, runner, ["forged"])
    assert feedback["code"] == "invalid_completion_evidence"
    assert "detail" not in feedback


def test_relation_rows_use_declared_root_population_only(monkeypatch):
    runner, binding, req = fixture([1, 2])
    source = runner.evidence.get("q")
    payload = source.payload()
    for row in payload["data"]:
        row["children"] = [{"projectId": 99, "quantity": 900, "unit": "pieces"}]
    runner.evidence = type(runner.evidence)()
    runner.evidence.add(
        EvidenceRecord.create(
            "q", "task_1", "query", source.call_identity, source.provenance, payload
        ),
        requirement=runner.runtimes["task_1"].task.requirements[0],
    )
    selected = add_total(runner, binding, req, 1)
    feedback = reject(monkeypatch, runner, [selected.evidence_id])
    assert json.loads(feedback["detail"])["requirements"][0]["missing_groups"] == [[2]]
    assert "99" not in feedback["detail"]


def test_oversized_coverage_feedback_keeps_existing_diagnostic_bound(monkeypatch):
    runner, binding, req = fixture(range(1000))
    selected = add_total(runner, binding, req, 0)
    feedback = reject(monkeypatch, runner, [selected.evidence_id])
    assert feedback["truncated"] is True
    assert len(feedback["detail"]) == 1024
    assert feedback["detail"].startswith(
        '{"requirements": [{"requirement_id": "totals"'
    )
