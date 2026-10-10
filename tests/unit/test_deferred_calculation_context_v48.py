"""Deferred work is explicit guidance, never chosen binding or new evidence."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
from general_manager.chat.planned.models import (
    CalculationBinding,
    EvidenceRequirement,
    PlannedTask,
)
from general_manager.chat.planned.scheduler import _executor_messages
from general_manager.chat.planned.validation import bind_calculation_requirement


def task():
    return PlannedTask(
        "work",
        "Aggregate the observed requested quantity",
        (),
        (
            EvidenceRequirement("schema", "schema", "Inspect contract", None),
            EvidenceRequirement(
                "identity", "query", "Resolve requested identity", None
            ),
            EvidenceRequirement("path", "path", "Inspect relation", None),
            EvidenceRequirement("rows", "query", "Read complete root rows", None),
            EvidenceRequirement(
                "total",
                "calculation",
                "Sum selected quantity",
                "sum",
                binding_required=True,
            ),
            EvidenceRequirement("later", "query", "A later unrelated read", None),
        ),
        ("schema", "identity", "path", "rows", "total", "later"),
        ("requires_calculation", "multiple_queries"),
    )


def store(t, *, linked=True, owner=None, complete=True):
    result = EvidenceStore()
    record = EvidenceRecord.create(
        "query-proof",
        owner or t.task_id,
        "query",
        "native-query-call",
        {},
        {
            "data": [
                {"quantity": 7, "unit": "pieces"},
                {"quantity": 9, "unit": "pieces"},
            ],
            "complete": complete,
            "has_more": not complete,
            "total_count": 2,
        },
    )
    result.add(record, requirement=t.requirements[3] if linked else None)
    return result


def request(t, e):
    messages = _executor_messages("Keep the requested quantity and unit", t, e)
    return messages, json.loads(
        next(
            m.content for m in messages if m.content.startswith("REFERENCE_DATA=")
        ).split("=", 1)[1]
    )


def context(t, e):
    return request(t, e)[1]["deferred_calculations"]


def test_deferred_action_is_explicit_but_no_binding_or_operation_is_chosen():
    t = task()
    e = store(t)
    value = context(t, e)
    assert value == [
        {
            "requirement_id": "total",
            "operation": "sum",
            "required_action": "bind_calculation",
            "binding_validated": False,
            "possible_source_requirements": [
                {"requirement_id": "identity", "kind": "query", "evidence_ids": []},
                {
                    "requirement_id": "rows",
                    "kind": "query",
                    "evidence_ids": ["query-proof"],
                },
            ],
        }
    ]
    assert "value_path" not in json.dumps(value)
    assert "unit_path" not in json.dumps(value)


def test_future_schema_and_path_requirements_are_not_source_candidates():
    t = task()
    sources = context(t, store(t))[0]["possible_source_requirements"]
    assert {s["requirement_id"] for s in sources} == {"identity", "rows"}


@pytest.mark.parametrize("linked,owner", [(False, None), (False, "other-task")])
def test_unlinked_and_foreign_evidence_are_not_suggested(linked, owner):
    t = task()
    sources = context(t, store(t, linked=linked, owner=owner))[0][
        "possible_source_requirements"
    ]
    assert all(s["evidence_ids"] == [] for s in sources)


def test_source_absence_is_explicit_and_never_claimed_as_ready():
    value = context(task(), EvidenceStore())[0]
    assert value["binding_validated"] is False
    assert all(not s["evidence_ids"] for s in value["possible_source_requirements"])


def test_partial_query_is_reference_only_and_cannot_bind():
    from general_manager.chat.planned.calculation_scope import (
        validate_calculation_binding_evidence,
    )
    from general_manager.chat.planned.calculations import CalculationError

    t = task()
    e = store(t, complete=False)
    assert context(t, e)[0]["binding_validated"] is False
    binding = CalculationBinding(("rows",), ("quantity",), unit_path=("unit",))
    with pytest.raises(CalculationError):
        validate_calculation_binding_evidence(t, "total", binding, e)


def test_valid_assignment_removes_guidance_and_keeps_actual_task_binding():
    t = task()
    e = store(t)
    bound = bind_calculation_requirement(
        t, "total", CalculationBinding(("rows",), ("quantity",), unit_path=("unit",))
    )
    messages, ref = request(bound, e)
    assert "deferred_calculations" not in ref
    assert next(
        r for r in ref["task"]["requirements"] if r["requirement_id"] == "total"
    )["binding"]["value_path"] == ["quantity"]
    assert "DEFERRED_CALCULATION_GUIDANCE" not in messages[0].content


def test_original_task_and_full_evidence_values_remain_immutable():
    t = task()
    e = store(t)
    before = deepcopy(t)
    records = e.records
    _, ref = request(t, e)
    assert "deferred_calculations" in ref
    assert t == before and e.records == records
    assert ref["task_evidence"][0]["payload"] == records[0].payload()


def test_legacy_nonrequired_binding_gets_no_new_guidance():
    t = task()
    legacy = replace(
        t,
        requirements=tuple(replace(r, binding_required=False) for r in t.requirements),
    )
    messages, ref = request(legacy, store(legacy))
    assert "deferred_calculations" not in ref
    assert "DEFERRED_CALCULATION_GUIDANCE" not in messages[0].content


def test_instruction_explains_pending_work_without_forbidding_legitimate_block():
    t = task()
    messages, _ = request(t, store(t))
    instruction = "\n".join(m.content for m in messages if m.role == "system")
    assert "DEFERRED_CALCULATION_GUIDANCE" in instruction
    assert "actual obstacle" in instruction
    assert "binding_validated=false" in instruction


def test_scheduler_bind_then_calculate_keeps_each_phase_contract(monkeypatch):
    import asyncio
    from tests.unit.test_chat_planned_deferred_binding import deferred
    from tests.unit.test_chat_planned_tool_feedback import _record_rounds

    runner, runtime, actions, binding = deferred()
    calls = _record_rounds(
        monkeypatch,
        [
            {
                "action": "bind_calculation",
                "requirement_id": "annual",
                "binding": binding,
            },
            {"action": "calculate_batch", "calculations": actions},
        ],
    )
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    refs = [
        json.loads(
            next(
                m.content for m in call[1] if m.content.startswith("REFERENCE_DATA=")
            ).split("=", 1)[1]
        )
        for call in calls
    ]
    assert refs[0]["deferred_calculations"][0]["requirement_id"] == "annual"
    assert "deferred_calculations" not in refs[1]
    assert [
        r.payload()["value"] for r in runner.evidence.records if r.kind == "calculation"
    ] == [100, 120, 140]


def test_legitimate_model_block_is_still_accepted(monkeypatch):
    import asyncio
    from tests.unit.test_chat_planned_deferred_binding import deferred
    from tests.unit.test_chat_planned_tool_feedback import _record_rounds

    runner, runtime, _, _ = deferred()
    _record_rounds(monkeypatch, [{"action": "block", "reason": "invalid_plan"}])
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert runtime.status == "blocked"
    assert runtime.task.requirements[-1].binding is None
