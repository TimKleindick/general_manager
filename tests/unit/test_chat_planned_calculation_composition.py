"""Verified arithmetic composition over immutable, task-bound evidence."""

import pytest
from general_manager.chat.planned.calculations import (
    CalculationError,
    CalculationOperand as Operand,
    calculate_evidence,
)
from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
from general_manager.chat.planned.models import EvidenceRequirement


def source_store():
    store = EvidenceStore()
    query = EvidenceRecord.create(
        "q", "t", "query", "query-call", {}, {"data": [50, 50, 60, 60]}
    )
    store.add(
        query,
        requirement=EvidenceRequirement("query", "query", "authorized source", None),
    )
    return store


def add_sum(store, name, indices):
    record = calculate_evidence(
        name,
        "t",
        "sum",
        [Operand("q", ("data", i)) for i in indices],
        store,
        require_linked=True,
    )
    store.add(
        record, requirement=EvidenceRequirement(name, "calculation", "sum", "sum")
    )
    return record


def test_sum_difference_percentage_recomputes_authorized_chain():
    store = source_store()
    add_sum(store, "old", [0, 1])
    add_sum(store, "new", [2, 3])
    delta = calculate_evidence(
        "delta",
        "t",
        "difference",
        [Operand("new", ("value",)), Operand("old", ("value",))],
        store,
        require_linked=True,
    )
    store.add(
        delta,
        requirement=EvidenceRequirement(
            "change", "calculation", "change", "difference"
        ),
    )
    growth = calculate_evidence(
        "growth",
        "t",
        "percentage",
        [Operand("delta", ("value",)), Operand("old", ("value",))],
        store,
        require_linked=True,
    )
    assert delta.payload()["value"] == 20
    assert growth.payload()["value"] == 20


@pytest.mark.parametrize("path", [(), ("operands", 0), ("operation",), ("value", 0)])
def test_derived_operand_must_reference_scalar_value(path):
    store = source_store()
    add_sum(store, "sum", [0, 1])
    with pytest.raises(CalculationError):
        calculate_evidence(
            "next", "t", "sum", [Operand("sum", path)], store, require_linked=True
        )


@pytest.mark.parametrize(
    "mutation",
    ["value", "operation", "identity", "task", "provenance", "missing", "cycle"],
)
def test_rejects_forged_or_unresolvable_derivation(mutation):
    store = source_store()
    valid = calculate_evidence(
        "derived", "t", "sum", [Operand("q", ("data", 0))], store
    )
    payload = valid.payload()
    identity, task, provenance = valid.call_identity, valid.task_id, valid.provenance
    if mutation == "value":
        payload["value"] = 900
    if mutation == "operation":
        payload["operation"] = "count"
    if mutation == "identity":
        identity = "forged"
    if mutation == "task":
        task = "foreign"
    if mutation == "provenance":
        provenance = {"calculator": "model"}
    if mutation in ("missing", "cycle"):
        payload["operands"][0] = {
            "evidence_id": "missing" if mutation == "missing" else "derived",
            "path": ["value"],
        }
    forged = EvidenceRecord.create(
        "derived", task, "calculation", identity, provenance, payload
    )
    store.add(forged)
    with pytest.raises(CalculationError):
        calculate_evidence("next", "t", "sum", [Operand("derived", ("value",))], store)


def test_foreign_query_cannot_be_used_even_without_link_requirement():
    store = source_store()
    with pytest.raises(CalculationError):
        calculate_evidence("next", "foreign", "sum", [Operand("q", ("data", 0))], store)


def test_link_requirement_applies_to_every_transitive_source():
    store = source_store()
    leaf = EvidenceRecord.create(
        "unlinked", "t", "query", "query-call", {}, {"value": 4}
    )
    store.add(leaf)
    derived = calculate_evidence(
        "derived", "t", "sum", [Operand("unlinked", ("value",))], store
    )
    store.add(
        derived, requirement=EvidenceRequirement("sum", "calculation", "sum", "sum")
    )
    with pytest.raises(CalculationError):
        calculate_evidence(
            "next",
            "t",
            "sum",
            [Operand("derived", ("value",))],
            store,
            require_linked=True,
        )


def test_returned_payload_mutation_does_not_change_recomputed_source():
    store = source_store()
    derived = add_sum(store, "sum", [0, 1])
    derived.payload()["value"] = 0
    result = calculate_evidence(
        "next", "t", "sum", [Operand("sum", ("value",))], store, require_linked=True
    )
    assert result.payload()["value"] == 100


def test_cycle_with_valid_canonical_identity_reaches_cycle_detection():
    from general_manager.chat.planned.calculations import _calculation_call_identity

    store = source_store()
    operands = [Operand("cycle", ("value",))]
    record = EvidenceRecord.create(
        "cycle",
        "t",
        "calculation",
        _calculation_call_identity("sum", operands),
        {"calculator": "framework", "operation": "sum"},
        {
            "operation": "sum",
            "value": 1,
            "operands": [{"evidence_id": "cycle", "path": ["value"]}],
        },
    )
    store.add(record)
    with pytest.raises(CalculationError, match="cycl"):
        calculate_evidence("result", "t", "sum", operands, store)
