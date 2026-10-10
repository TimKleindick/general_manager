"""One executor round can admit an atomic batch of declared calculations."""

import asyncio
from dataclasses import replace
import pytest
from general_manager.chat.planned.models import EvidenceRequirement, CalculationBinding
from general_manager.chat.planned.evidence import EvidenceRecord
from tests.unit.test_chat_planned_tool_feedback import _runner, _record_rounds
from tests.unit.test_chat_planned_scheduler import _task


def setup():
    base = _task("task_1")
    binding = CalculationBinding(
        (base.requirements[0].requirement_id,), ("quantity",), (("year",),), None
    )
    req = EvidenceRequirement("annual", "calculation", "year totals", "sum", binding)
    task = replace(
        base,
        requirements=(*base.requirements, req),
        completion_criteria=(*base.completion_criteria, "annual"),
        routing_features=("requires_calculation",),
    )
    runner = _runner(lambda *_: None, tasks=[task])
    query = EvidenceRecord.create(
        "q",
        "task_1",
        "query",
        "query",
        {},
        {
            "data": [
                {"year": 2023, "quantity": 100},
                {"year": 2024, "quantity": 120},
                {"year": 2025, "quantity": 140},
            ],
            "complete": True,
            "total_count": 3,
            "has_more": False,
        },
    )
    runner.evidence.add(query, requirement=task.requirements[0])
    actions = [
        {
            "action": "calculate",
            "requirement_id": "annual",
            "operation": "sum",
            "operands": [{"evidence_id": "q", "path": ["data", i, "quantity"]}],
        }
        for i in range(3)
    ]
    return runner, actions


def test_three_group_totals_use_one_round(monkeypatch):
    runner, actions = setup()
    calls = _record_rounds(
        monkeypatch, [{"action": "calculate_batch", "calculations": actions}]
    )
    assert asyncio.run(runner._execute_one_pass(runner.runtimes["task_1"], ())) is None
    assert len(calls) == 1
    assert [
        r.payload()["value"] for r in runner.evidence.records if r.kind == "calculation"
    ] == [100, 120, 140]


def test_invalid_later_group_rejects_entire_batch(monkeypatch):
    runner, actions = setup()
    before = runner.evidence.records
    actions[-1]["operands"][0]["path"] = ["data", 99, "quantity"]
    _record_rounds(
        monkeypatch, [{"action": "calculate_batch", "calculations": actions}]
    )
    assert (
        asyncio.run(runner._execute_one_pass(runner.runtimes["task_1"], ()))
        == "provider_failed"
    )
    assert runner.evidence.records == before


@pytest.mark.parametrize(
    "value",
    [
        [],
        [{"action": "block", "reason": "provider_failed"}],
        [{"action": "calculate_batch", "calculations": []}],
    ],
)
def test_batch_rejects_non_calculation_or_empty_contents(value):
    import json
    from general_manager.chat.planned.scheduler import _parse_action

    assert (
        _parse_action(json.dumps({"action": "calculate_batch", "calculations": value}))
        is None
    )


def test_one_valid_group_does_not_complete_the_grouped_requirement(monkeypatch):
    runner, actions = setup()
    _record_rounds(
        monkeypatch,
        [actions[0], {"action": "complete", "evidence_ids": ["task_1:calculation:2"]}],
    )
    runtime = runner.runtimes["task_1"]
    asyncio.run(runner._execute_one_pass(runtime, ()))
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert runtime.status == "running"


def test_all_groups_can_complete_without_reincluding_unselected_queries(monkeypatch):
    runner, actions = setup()
    _record_rounds(
        monkeypatch,
        [
            {"action": "calculate_batch", "calculations": actions},
            {
                "action": "complete",
                "evidence_ids": [f"task_1:calculation:{i}" for i in (2, 3, 4)],
            },
        ],
    )
    runtime = runner.runtimes["task_1"]
    asyncio.run(runner._execute_one_pass(runtime, ()))
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert runtime.status == "resolved"


def test_deadline_expiring_during_last_calculation_discards_entire_batch(monkeypatch):
    runner, actions = setup()
    runtime = runner.runtimes["task_1"]
    before = runner.evidence.records
    original = runner._calculate_action
    calls = []

    def calculate(current, action, staged):
        accepted = original(current, action, staged)
        calls.append(action)
        if len(calls) == len(actions):
            runner.clock = lambda: 101.0
        return accepted

    monkeypatch.setattr(runner, "_calculate_action", calculate)
    _record_rounds(
        monkeypatch, [{"action": "calculate_batch", "calculations": actions}]
    )
    asyncio.run(runner._execute_one_pass(runtime, ()))
    assert runner.evidence.records == before
    assert runtime.reason == "deadline_exceeded"


@pytest.mark.parametrize("use_delta", [False, True])
def test_growth_requires_direct_operands_from_every_declared_source(
    monkeypatch, use_delta
):
    runner, annuals = setup()
    runtime = runner.runtimes["task_1"]
    delta = EvidenceRequirement(
        "delta",
        "calculation",
        "annual difference",
        "difference",
        CalculationBinding(("annual",), None, (), None),
    )
    growth = EvidenceRequirement(
        "growth",
        "calculation",
        "growth percent",
        "percentage",
        CalculationBinding(("delta", "annual"), None, (), None),
    )
    runtime.task = replace(
        runtime.task,
        requirements=(*runtime.task.requirements, delta, growth),
        completion_criteria=(*runtime.task.completion_criteria, "delta", "growth"),
    )

    def operand(index):
        return {"evidence_id": f"task_1:calculation:{index}", "path": ["value"]}

    _record_rounds(
        monkeypatch,
        [
            {"action": "calculate_batch", "calculations": annuals},
            {
                "action": "calculate",
                "requirement_id": "delta",
                "operation": "difference",
                "operands": [operand(3), operand(2)],
            },
            {
                "action": "calculate",
                "requirement_id": "growth",
                "operation": "percentage",
                "operands": [operand(5 if use_delta else 3), operand(2)],
            },
            {
                "action": "complete",
                "evidence_ids": [
                    f"task_1:calculation:{i}" for i in range(2, 7 if use_delta else 6)
                ],
            },
        ],
    )

    async def run():
        assert await runner._execute_one_pass(runtime, ()) is None
        # Two operands from the same annual requirement remain valid.
        assert await runner._execute_one_pass(runtime, ()) is None
        assert runner.evidence.get("task_1:calculation:5").payload()["value"] == 20
        before = runner.evidence.records
        result = await runner._execute_one_pass(runtime, ())
        if use_delta:
            assert result is None
            assert runner.evidence.get("task_1:calculation:6").payload()["value"] == 20
        else:
            assert result == "provider_failed"
            assert runtime.action_validation_error["code"] == "invalid_calculation"
            assert runner.evidence.records == before
        completion = await runner._execute_one_pass(runtime, ())
        assert completion == (None if use_delta else "provider_failed")
        assert runtime.status == ("resolved" if use_delta else "running")
        if not use_delta:
            assert runtime.action_validation_error["code"] == "unsatisfied_requirements"
            assert runtime.selected_evidence_ids == ()

    asyncio.run(run())
