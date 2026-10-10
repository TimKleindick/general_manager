"""Binding admission controls; synthetic repros of E006/E028/E029, not model scores."""

from dataclasses import replace
import pytest
from general_manager.chat.planned.calculation_scope import (
    validate_calculation_binding_evidence,
)
from general_manager.chat.planned.calculations import (
    CalculationError,
    CalculationOperand,
    calculate_evidence,
)
from general_manager.chat.planned.models import CalculationBinding, EvidenceRequirement
from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
from tests.unit.test_chat_planned_deferred_binding import deferred


def setup(rows, *, complete=True, has_more=False, count=None, binding=None):
    _runner, runtime, _, raw = deferred()
    source = runtime.task.requirements[0]
    store = EvidenceStore()
    record = EvidenceRecord.create(
        "q",
        runtime.task.task_id,
        "query",
        "query",
        {},
        {
            "data": rows,
            "complete": complete,
            "has_more": has_more,
            "total_count": len(rows) if count is None else count,
        },
    )
    store.add(record, requirement=source)
    b = CalculationBinding.from_mapping(binding or raw)
    return runtime.task, b, store


def admit(task, b, store):
    before = store.records
    validate_calculation_binding_evidence(task, "annual", b, store)
    assert store.records == before


@pytest.mark.parametrize(
    "relation,field",
    [("projectCommercialList", "revenueEur"), ("shipmentsList", "quantity")],
)
@pytest.mark.parametrize("length", [0, 1, 2])
def test_recorded_collection_binding_shapes_never_collapse_to_scalar(
    relation, field, length
):
    task, b, store = setup([{"year": 2025, relation: {"items": [{field: 7}] * length}}])
    b = replace(b, value_path=(relation, "items", field))
    with pytest.raises(CalculationError, match="Collection traversal"):
        admit(task, b, store)


@pytest.mark.parametrize(
    "damage",
    [
        "incomplete",
        "more",
        "wrong_count",
        "bool_count",
        "missing",
        "heterogeneous",
        "boolean",
        "null",
        "text",
        "collection",
        "unit_missing",
        "unit_mixed",
        "group_missing",
        "group_collection",
        "foreign_task",
        "unlinked",
        "empty",
    ],
)
def test_bad_query_bindings_are_rejected_without_assignment(damage):
    rows = [
        {"year": 2025, "quantity": 2, "unit": "pieces"},
        {"year": 2025, "quantity": 3, "unit": "pieces"},
    ]
    options = {}
    if damage == "incomplete":
        options["complete"] = False
    elif damage == "more":
        options["has_more"] = True
    elif damage == "wrong_count":
        options["count"] = 3
    elif damage == "bool_count":
        options["count"] = True
    elif damage == "missing":
        rows[0].pop("quantity")
        rows[1].pop("quantity")
    elif damage == "heterogeneous":
        rows[1].pop("quantity")
    elif damage in ["boolean", "null", "text", "collection"]:
        rows[1]["quantity"] = {
            "boolean": True,
            "null": None,
            "text": "abc",
            "collection": [3],
        }[damage]
    elif damage == "unit_missing":
        rows[1].pop("unit")
    elif damage == "unit_mixed":
        rows[1]["unit"] = "kg"
    elif damage == "group_missing":
        rows[1].pop("year")
    elif damage == "group_collection":
        rows[1]["year"] = [2025]
    elif damage == "empty":
        rows = []
    task, b, store = setup(rows, **options)
    b = replace(b, unit_path=("unit",))
    if damage == "foreign_task":
        task = replace(task, task_id="foreign")
    elif damage == "unlinked":
        store = EvidenceStore()
    with pytest.raises(CalculationError):
        admit(task, b, store)
    assert task.requirements[-1].binding is None


def test_nested_object_fields_remain_executable_and_root_count_accepts_empty():
    task, b, store = setup(
        [
            {"year": 2025, "metrics": {"quantity": 4}},
            {"year": 2025, "metrics": {"quantity": 5}},
        ]
    )
    b = replace(b, value_path=("metrics", "quantity"))
    admit(task, b, store)
    result = calculate_evidence(
        "sum",
        task.task_id,
        "sum",
        [CalculationOperand("q", ("data", i, "metrics", "quantity")) for i in range(2)],
        store,
        require_linked=True,
        binding=b,
    )
    assert result.payload()["value"] == 9
    task, b, store = setup([])
    task = replace(
        task,
        requirements=(
            *task.requirements[:-1],
            replace(task.requirements[-1], operation="count"),
        ),
    )
    admit(task, replace(b, value_path=(), group_by=()), store)


@pytest.mark.parametrize("damage", ["forged", "incompatible"])
def test_derived_binding_requires_recomputed_compatible_predecessors(damage):
    task, b, store = setup(
        [{"year": 2025, "quantity": 2}, {"year": 2026, "quantity": 3}]
    )
    req = EvidenceRequirement("previous", "calculation", "prior sums", "sum", b)
    for i in range(2):
        record = calculate_evidence(
            f"c{i}",
            task.task_id,
            "sum",
            [CalculationOperand("q", ("data", i, "quantity"))],
            store,
            require_linked=True,
            binding=b,
        )
        payload = record.payload()
        if i == 1:
            if damage == "forged":
                payload["value"] = 999
            else:
                payload["scope"]["unit"] = "kg"
        store.add(
            EvidenceRecord.create(
                record.evidence_id,
                record.task_id,
                record.kind,
                record.call_identity,
                record.provenance,
                payload,
            ),
            requirement=req,
        )
    task = replace(
        task, requirements=(*task.requirements[:-1], req, task.requirements[-1])
    )
    with pytest.raises(CalculationError):
        admit(task, CalculationBinding(("previous",), None, (), None), store)


def test_derived_binding_accepts_recomputed_predecessors_without_publishing_probe():
    task, b, store = setup(
        [{"year": 2025, "quantity": 2}, {"year": 2026, "quantity": 3}]
    )
    req = EvidenceRequirement("previous", "calculation", "prior sums", "sum", b)
    for i in range(2):
        record = calculate_evidence(
            f"c{i}",
            task.task_id,
            "sum",
            [CalculationOperand("q", ("data", i, "quantity"))],
            store,
            require_linked=True,
            binding=b,
        )
        store.add(record, requirement=req)
    task = replace(
        task, requirements=(*task.requirements[:-1], req, task.requirements[-1])
    )
    admit(task, CalculationBinding(("previous",), None, (), None), store)
