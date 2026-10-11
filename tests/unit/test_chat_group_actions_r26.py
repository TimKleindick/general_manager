"""Explicit complete-group sums retain atomic admission and numerical checks."""

import asyncio
from dataclasses import replace
import json

import pytest

from general_manager.chat.planned.action_contract import executor_action_schema
from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.evidence import EvidenceRecord
from general_manager.chat.planned.models import (
    CalculationBinding,
    ConversionBinding,
    CrossQueryBinding,
    CrossQuerySourceBinding,
    EvidenceRequirement,
)
from general_manager.chat.planned.scheduler import _parse_action_with_feedback
from general_manager.chat.planned.synthesis import synthesize_answer
from tests.unit.test_chat_planned_scheduler import _task
from tests.unit.test_chat_planned_synthesis import _settings, _SynthesisProvider
from tests.unit.test_chat_planned_tool_feedback import _runner, _record_rounds


def _setup(*, rows=None, binding=None, payload_updates=None):
    rows = (
        rows
        if rows is not None
        else [
            {"year": 2024, "quantity": 4, "unit": "pieces"},
            {"year": 2023, "quantity": 10, "unit": "kg"},
            {"year": 2024, "quantity": 6, "unit": "pieces"},
            {"year": 2025, "quantity": 20, "unit": "kg"},
        ]
    )
    binding = (
        binding
        if binding is not None
        else CalculationBinding(("query",), ("quantity",), (("year",),), ("unit",))
    )
    base = _task("task_1")
    annual = EvidenceRequirement("annual", "calculation", "Annual sums", "sum", binding)
    task = replace(
        base,
        requirements=(*base.requirements, annual),
        completion_criteria=("query", "annual"),
        routing_features=("requires_calculation",),
    )
    runner = _runner(lambda *_: None, tasks=[task])
    query = EvidenceRecord.create(
        "q",
        "task_1",
        "query",
        "original-query",
        {},
        {
            "data": rows,
            "complete": True,
            "has_more": False,
            "total_count": len(rows),
            **(payload_updates or {}),
        },
    )
    runner.evidence.add(query, requirement=task.requirements[0])
    return runner


def _action(*requests):
    return {
        "action": "calculate_groups",
        "requests": list(requests)
        or [{"requirement_id": "annual", "query_evidence_id": "q"}],
    }


def _execute(runner, monkeypatch, action):
    _record_rounds(monkeypatch, [action])
    return asyncio.run(runner._execute_one_pass(runner.runtimes["task_1"], ()))


def _calculations(runner):
    return [
        record for record in runner.evidence.records if record.kind == "calculation"
    ]


def test_action_schema_and_parser_accept_exact_explicit_group_requests():
    action = _action()
    assert _parse_action_with_feedback(json.dumps(action)) == (action, None)
    branch = next(
        item
        for item in executor_action_schema()["oneOf"]
        if item["properties"]["action"]["const"] == "calculate_groups"
    )
    request = branch["properties"]["requests"]["items"]
    assert request["additionalProperties"] is False
    assert set(request["required"]) == {"requirement_id", "query_evidence_id"}


@pytest.mark.parametrize(
    "requests",
    [
        [],
        None,
        {},
        [None],
        [{"requirement_id": "annual"}],
        [{"requirement_id": "annual", "query_evidence_id": "q", "operation": "sum"}],
        [{"requirement_id": " ", "query_evidence_id": "q"}],
        [{"requirement_id": "annual", "query_evidence_id": True}],
        [{"requirement_id": "annual", "query_evidence_id": " "}],
        [{"requirement_id": "annual", "query_evidence_id": "q"}] * 2,
        [
            {"requirement_id": "annual", "query_evidence_id": "q"},
            {"requirement_id": "annual", "query_evidence_id": "different"},
        ],
    ],
)
def test_group_request_parser_rejects_empty_extra_untyped_and_duplicate_requests(
    requests,
):
    action, error = _parse_action_with_feedback(
        json.dumps(
            {
                "action": "calculate_groups",
                "requests": requests,
            }
        )
    )
    assert action is None
    assert error["code"] == "invalid_calculation_groups"
    assert str(error["path"]).startswith("$.requests")


def test_all_groups_are_generated_once_in_first_observed_order_with_exact_units(
    monkeypatch,
):
    runner = _setup()
    assert _execute(runner, monkeypatch, _action()) is None
    records = _calculations(runner)
    assert [record.payload()["value"] for record in records] == [10, 10, 20]
    assert [record.payload()["scope"]["groups"] for record in records] == [
        [[2024]],
        [[2023]],
        [[2025]],
    ]
    assert [record.payload()["scope"]["unit"] for record in records] == [
        "pieces",
        "kg",
        "kg",
    ]
    assert [record.payload()["scope"]["leaf_paths"] for record in records] == [
        [["data", 0, "quantity"], ["data", 2, "quantity"]],
        [["data", 1, "quantity"]],
        [["data", 3, "quantity"]],
    ]
    assert all(
        record.payload()["scope"]["query_evidence_id"] == "q" for record in records
    )
    assert all(
        record.payload()["scope"]["query_call_identity"] == "original-query"
        for record in records
    )
    assert all(
        runner.evidence.is_linked_to("task_1", "annual", record.evidence_id)
        for record in records
    )


def test_typed_group_values_and_null_are_not_collapsed(monkeypatch):
    runner = _setup(
        rows=[
            {"year": year, "quantity": quantity, "unit": "pieces"}
            for year, quantity in [(True, 1), (1, 2), ("1", 3), (None, 4)]
        ]
    )
    assert _execute(runner, monkeypatch, _action()) is None
    assert [
        record.payload()["scope"]["groups"] for record in _calculations(runner)
    ] == [
        [[True]],
        [[1]],
        [["1"]],
        [[None]],
    ]
    assert [record.payload()["value"] for record in _calculations(runner)] == [
        1,
        2,
        3,
        4,
    ]


def test_utc_year_nested_paths_use_existing_group_and_numeric_rules(monkeypatch):
    binding = CalculationBinding(
        ("query",), ("nested", "quantity"), (), None, (("nested", "date"),)
    )
    runner = _setup(
        binding=binding,
        rows=[
            {"nested": {"date": "2023-12-31T23:30:00-02:00", "quantity": 5}},
            {"nested": {"date": "2024-04-01", "quantity": 6}},
        ],
    )
    assert _execute(runner, monkeypatch, _action()) is None
    record = _calculations(runner)[0]
    assert record.payload()["value"] == 11
    assert record.payload()["scope"]["groups"] == [[2024]]
    assert record.payload()["scope"]["unit"] is None
    assert record.payload()["scope"]["leaf_paths"] == [
        ["data", 0, "nested", "quantity"],
        ["data", 1, "nested", "quantity"],
    ]


def test_explicit_query_choice_does_not_merge_other_linked_populations(monkeypatch):
    runner = _setup()
    query = EvidenceRecord.create(
        "other",
        "task_1",
        "query",
        "other-query",
        {},
        {
            "data": [{"year": 2026, "quantity": 999, "unit": "pieces"}],
            "complete": True,
            "has_more": False,
            "total_count": 1,
        },
    )
    runner.evidence.add(
        query, requirement=runner.runtimes["task_1"].task.requirements[0]
    )
    assert _execute(runner, monkeypatch, _action()) is None
    assert [record.payload()["value"] for record in _calculations(runner)] == [
        10,
        10,
        20,
    ]
    assert all(
        record.payload()["scope"]["query_evidence_id"] == "q"
        for record in _calculations(runner)
    )


def test_multiple_explicit_requirements_and_queries_are_one_atomic_action(monkeypatch):
    runner = _setup()
    runtime = runner.runtimes["task_1"]
    query_req = EvidenceRequirement("plans", "query", "Existing plans", None)
    plan_req = EvidenceRequirement(
        "planned_annual",
        "calculation",
        "Annual plan sums",
        "sum",
        CalculationBinding(("plans",), ("planned",), (("year",),), ("unit",)),
    )
    outlook_req = EvidenceRequirement("outlooks", "query", "Existing outlooks", None)
    outlook_sum = EvidenceRequirement(
        "outlook_annual",
        "calculation",
        "Annual outlook sums",
        "sum",
        CalculationBinding(("outlooks",), ("outlook",), (("year",),), ("unit",)),
    )
    runtime.task = replace(
        runtime.task,
        requirements=(
            *runtime.task.requirements,
            query_req,
            plan_req,
            outlook_req,
            outlook_sum,
        ),
        completion_criteria=(
            *runtime.task.completion_criteria,
            "plans",
            "planned_annual",
            "outlooks",
            "outlook_annual",
        ),
    )
    runner.evidence.add(
        EvidenceRecord.create(
            "p",
            "task_1",
            "query",
            "plan-query",
            {},
            {
                "data": [
                    {"year": 2023, "planned": 12, "unit": "kg"},
                    {"year": 2024, "planned": 15, "unit": "pieces"},
                    {"year": 2025, "planned": 25, "unit": "kg"},
                ],
                "complete": True,
                "has_more": False,
                "total_count": 3,
            },
        ),
        requirement=query_req,
    )
    runner.evidence.add(
        EvidenceRecord.create(
            "o",
            "task_1",
            "query",
            "outlook-query",
            {},
            {
                "data": [
                    {"year": year, "outlook": value, "unit": "pieces"}
                    for year, value in [(2023, 13), (2024, 16), (2025, 26)]
                ],
                "complete": True,
                "has_more": False,
                "total_count": 3,
            },
        ),
        requirement=outlook_req,
    )
    calls = _record_rounds(
        monkeypatch,
        [
            _action(
                {"requirement_id": "annual", "query_evidence_id": "q"},
                {"requirement_id": "planned_annual", "query_evidence_id": "p"},
                {"requirement_id": "outlook_annual", "query_evidence_id": "o"},
            )
        ],
    )
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert len(calls) == 1
    assert [record.payload()["value"] for record in _calculations(runner)] == [
        10,
        10,
        20,
        12,
        15,
        25,
        13,
        16,
        26,
    ]


def test_later_request_arithmetic_failure_discards_earlier_staged_request(monkeypatch):
    runner = _setup()
    runtime = runner.runtimes["task_1"]
    query_req = EvidenceRequirement("plans", "query", "Existing plans", None)
    sum_req = EvidenceRequirement(
        "plan_sums",
        "calculation",
        "Annual plan sums",
        "sum",
        CalculationBinding(("plans",), ("planned",), (("year",),), ("unit",)),
    )
    runtime.task = replace(
        runtime.task, requirements=(*runtime.task.requirements, query_req, sum_req)
    )
    runner.evidence.add(
        EvidenceRecord.create(
            "p",
            "task_1",
            "query",
            "plan-query",
            {},
            {
                "data": [
                    {"year": 2023, "planned": 12, "unit": "kg"},
                    {"year": 2024, "planned": "invalid", "unit": "kg"},
                ],
                "complete": True,
                "has_more": False,
                "total_count": 2,
            },
        ),
        requirement=query_req,
    )
    before = runner.evidence.records
    assert (
        _execute(
            runner,
            monkeypatch,
            _action(
                {"requirement_id": "annual", "query_evidence_id": "q"},
                {"requirement_id": "plan_sums", "query_evidence_id": "p"},
            ),
        )
        == "provider_failed"
    )
    assert runner.evidence.records == before
    assert runtime.action_validation_error["path"] == "$.requests[1]"


@pytest.mark.parametrize(
    "updates",
    [
        {"complete": False},
        {"has_more": True},
        {"total_count": 3},
        {"total_count": True},
        {"total_count": "4"},
        {"data": {}},
    ],
)
def test_partial_or_untyped_population_fails_before_any_calculation(
    monkeypatch, updates
):
    runner = _setup(payload_updates=updates)
    before = runner.evidence.records
    assert _execute(runner, monkeypatch, _action()) == "provider_failed"
    assert (
        runner.runtimes["task_1"].action_validation_error["code"]
        == "invalid_calculation_groups"
    )
    assert runner.evidence.records == before


@pytest.mark.parametrize(
    "last_row",
    [
        {"year": 2025, "unit": "kg"},
        {"year": 2025, "quantity": "NaN", "unit": "kg"},
        {"year": 2025, "quantity": True, "unit": "kg"},
        {"year": 2025, "quantity": 20, "unit": ""},
        {"year": 2024, "quantity": 20, "unit": "kg"},
        {"year": {}, "quantity": 20, "unit": "kg"},
    ],
)
def test_invalid_last_group_discards_all_previously_staged_groups(
    monkeypatch, last_row
):
    runner = _setup(
        rows=[
            {"year": 2023, "quantity": 10, "unit": "pieces"},
            last_row,
        ]
    )
    if last_row.get("year") == 2024:
        runner = _setup(
            rows=[{"year": 2024, "quantity": 10, "unit": "pieces"}, last_row]
        )
    before = runner.evidence.records
    assert _execute(runner, monkeypatch, _action()) == "provider_failed"
    assert runner.evidence.records == before


def test_invalid_later_request_discards_valid_earlier_request(monkeypatch):
    runner = _setup()
    before = runner.evidence.records
    assert (
        _execute(
            runner,
            monkeypatch,
            _action(
                {"requirement_id": "annual", "query_evidence_id": "q"},
                {"requirement_id": "absent", "query_evidence_id": "q"},
            ),
        )
        == "provider_failed"
    )
    assert runner.evidence.records == before
    assert runner.runtimes["task_1"].action_validation_error["path"] == "$.requests[1]"


@pytest.mark.parametrize(
    "source", ["missing", "foreign", "unlinked", "different_requirement", "calculation"]
)
def test_query_must_be_task_owned_and_linked_to_exact_bound_source(monkeypatch, source):
    runner = _setup()
    runtime = runner.runtimes["task_1"]
    if source != "missing":
        query_req = EvidenceRequirement("different", "query", "Different source", None)
        runtime.task = replace(
            runtime.task, requirements=(*runtime.task.requirements, query_req)
        )
        record = EvidenceRecord.create(
            source,
            "other_task" if source == "foreign" else "task_1",
            "calculation" if source == "calculation" else "query",
            source,
            {},
            runner.evidence.get("q").payload(),
        )
        runner.evidence.add(
            record,
            requirement=query_req
            if source in {"foreign", "different_requirement"}
            else None,
        )
    before = runner.evidence.records
    assert (
        _execute(
            runner,
            monkeypatch,
            _action({"requirement_id": "annual", "query_evidence_id": source}),
        )
        == "provider_failed"
    )
    assert runtime.action_validation_error["code"] == "invalid_calculation_groups"
    assert runner.evidence.records == before


def _cross_query_binding():
    def source(requirement, evidence):
        return CrossQuerySourceBinding(
            requirement,
            evidence,
            ("entity",),
            ("date",),
            ("entity",),
            ("start",),
            ("end",),
        )

    return CalculationBinding(
        ("left", "right"),
        None,
        cross_query=CrossQueryBinding(
            (source("left", "left_sum"), source("right", "right_sum")),
            ("schema",),
            ("s",),
        ),
    )


@pytest.mark.parametrize(
    "mode",
    [
        "deferred",
        "unbound",
        "derived",
        "conversion",
        "cross_query",
        "multi_source",
        "average",
        "count",
        "empty",
    ],
)
def test_unsupported_modes_are_rejected_without_binding_or_zero_invention(
    monkeypatch, mode
):
    runner = _setup(rows=[] if mode == "empty" else None)
    runtime = runner.runtimes["task_1"]
    annual = runtime.task.requirements[-1]
    if mode in {"deferred", "unbound"}:
        annual = replace(annual, binding=None, binding_required=mode == "deferred")
    elif mode == "derived":
        annual = replace(annual, binding=CalculationBinding(("query",), None))
    elif mode == "conversion":
        annual = replace(
            annual,
            binding=replace(
                annual.binding,
                conversion=ConversionBinding(
                    ("factor",),
                    ("identity",),
                    ("schema",),
                    ("s",),
                ),
            ),
        )
    elif mode == "cross_query":
        annual = replace(annual, binding=_cross_query_binding())
    elif mode == "multi_source":
        annual = replace(
            annual,
            binding=replace(annual.binding, source_requirement_ids=("query", "other")),
        )
    elif mode in {"average", "count"}:
        annual = replace(annual, operation=mode)
    runtime.task = replace(
        runtime.task, requirements=(*runtime.task.requirements[:-1], annual)
    )
    before_task, before_evidence = runtime.task, runner.evidence.records
    assert _execute(runner, monkeypatch, _action()) == "provider_failed"
    assert runtime.action_validation_error["code"] == "invalid_calculation_groups"
    assert runtime.task == before_task
    assert runner.evidence.records == before_evidence


def test_deadline_after_last_calculation_rejects_entire_group_action(monkeypatch):
    runner = _setup()
    original = runner._calculate_action
    accepted = []

    def calculate(runtime, action, staged):
        result = original(runtime, action, staged)
        accepted.append(result)
        if len(accepted) == 3:
            runner.clock = lambda: 101.0
        return result

    monkeypatch.setattr(runner, "_calculate_action", calculate)
    before = runner.evidence.records
    assert _execute(runner, monkeypatch, _action()) is None
    assert accepted == [True, True, True]
    assert runner.runtimes["task_1"].reason == "deadline_exceeded"
    assert runner.evidence.records == before


def test_missing_selected_group_cannot_complete_and_tampered_value_is_revalidated(
    monkeypatch,
):
    runner = _setup()
    assert _execute(runner, monkeypatch, _action()) is None
    runtime = runner.runtimes["task_1"]
    records = _calculations(runner)
    assert (
        _execute(
            runner,
            monkeypatch,
            {"action": "complete", "evidence_ids": [records[0].evidence_id]},
        )
        == "provider_failed"
    )
    assert runtime.action_validation_error["code"] == "unsatisfied_requirements"
    payload = records[-1].payload()
    payload["value"] = 999
    forged = EvidenceRecord.create(
        "forged",
        "task_1",
        "calculation",
        records[-1].call_identity,
        records[-1].provenance,
        payload,
    )
    runner.evidence.add(forged, requirement=runtime.task.requirements[-1])
    assert (
        _execute(
            runner,
            monkeypatch,
            {
                "action": "complete",
                "evidence_ids": [
                    records[0].evidence_id,
                    records[1].evidence_id,
                    "forged",
                ],
            },
        )
        == "provider_failed"
    )
    assert runtime.action_validation_error["code"] == "invalid_completion_evidence"
    assert runtime.selected_evidence_ids == ()


def test_schema_query_bind_groups_complete_and_synthesis_transport(monkeypatch):
    base = _task("task_1")
    schema_req = EvidenceRequirement(
        "schema", "schema", "Read exposed definitions", None
    )
    annual = EvidenceRequirement(
        "annual",
        "calculation",
        "Requested annual actuals",
        "sum",
        None,
        binding_required=True,
    )
    task = replace(
        base,
        requirements=(schema_req, *base.requirements, annual),
        completion_criteria=("schema", "query", "annual"),
        routing_features=("requires_calculation",),
    )
    outputs = {
        "get_manager_schema": {
            "manager": "PartManager",
            "fields": [
                {
                    "name": "quantity",
                    "type": "Int",
                    "description": "Observed actual units",
                },
            ],
        },
        "query": {
            "data": [
                {"year": 2023, "quantity": 10, "unit": "pieces"},
                {"year": 2024, "quantity": 12, "unit": "pieces"},
            ],
            "complete": True,
            "has_more": False,
            "total_count": 2,
        },
    }
    runner = _runner(lambda name, *_: outputs[name], tasks=[task])
    from general_manager.chat.providers.base import ToolCallEvent

    binding = CalculationBinding(("query",), ("quantity",), (("year",),), ("unit",))
    ids = ["task_1:schema:1", "task_1:calculation:3", "task_1:calculation:4"]
    calls = _record_rounds(
        monkeypatch,
        [
            ToolCallEvent(
                "schema-call", "get_manager_schema", {"manager": "PartManager"}
            ),
            ToolCallEvent(
                "query-call",
                "query",
                {"manager": "PartManager", "fields": ["year", "quantity", "unit"]},
            ),
            {
                "action": "bind_calculation",
                "requirement_id": "annual",
                "binding": binding.as_mapping(),
            },
            _action(
                {"requirement_id": "annual", "query_evidence_id": "task_1:query:2"}
            ),
            {"action": "complete", "evidence_ids": ids},
        ],
    )

    async def execute():
        for _ in range(5):
            assert await runner._execute_one_pass(runner.runtimes["task_1"], ()) is None

    asyncio.run(execute())
    assert len(calls) == 5
    assert runner.runtimes["task_1"].status == "resolved"
    assert [record.payload()["value"] for record in _calculations(runner)] == [10, 12]
    _SynthesisProvider.calls.clear()
    _SynthesisProvider.responses = [
        json.dumps({"answer": "2023: 10 pieces; 2024: 12 pieces.", "evidence_ids": ids})
    ]
    result = asyncio.run(
        synthesize_answer(
            "Give annual actual quantities",
            runner.synthesis_evidence(),
            {"resolved": 1, "total": 1},
            _settings(),
            RoundBudget(()),
        )
    )
    assert result.evidence_ids == tuple(ids)
    reference_message = next(
        message.content
        for message in _SynthesisProvider.calls[-1]
        if message.content.startswith("RESOLVED_REFERENCE_DATA=")
    )
    assert "task_1:query:2" in reference_message
    assert (
        "Render every fulfilled component explicitly requested by the user"
        in _SynthesisProvider.calls[-1][0].content
    )
    assert "calculate_groups" in calls[0][1][0].content
