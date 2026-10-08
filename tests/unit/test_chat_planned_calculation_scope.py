"""A project subtotal cannot satisfy a bound customer/year aggregate."""

from dataclasses import replace
import pytest
from general_manager.chat.planned.models import CalculationBinding, EvidenceRequirement
from general_manager.chat.planned.calculations import (
    CalculationOperand as Operand,
    CalculationError,
    calculate_evidence,
)
from general_manager.chat.planned.evidence import (
    EvidenceStore,
    EvidenceRecord,
    canonical_call_identity,
)


def fixture(complete=True, unit="pieces"):
    rows = [
        {
            "customer": "C01",
            "year": year,
            "project": project,
            "quantity": quantity,
            "unit": unit,
        }
        for year, quantity in [(2023, 50), (2024, 60)]
        for project in ["P01", "P02"]
    ]
    store = EvidenceStore()
    query = EvidenceRecord.create(
        "q",
        "t",
        "query",
        canonical_call_identity(
            "query", {"manager": "Shipment", "filters": {"customer": "C01"}}
        ),
        {},
        {"data": rows, "total_count": 4, "has_more": False, "complete": complete},
    )
    store.add(
        query,
        requirement=EvidenceRequirement("actuals", "query", "customer actuals", None),
    )
    binding = CalculationBinding(("actuals",), ("quantity",), (("year",),), ("unit",))
    return store, binding


def total(store, binding, name, indices):
    result = calculate_evidence(
        name,
        "t",
        "sum",
        [Operand("q", ("data", i, "quantity")) for i in indices],
        store,
        require_linked=True,
        binding=binding,
    )
    store.add(
        result,
        requirement=EvidenceRequirement(
            "annual", "calculation", "annual totals", "sum", binding
        ),
    )
    return result


def test_complete_group_chain_gives_customer_delta_twenty():
    store, binding = fixture()
    total(store, binding, "old", [0, 1])
    total(store, binding, "new", [2, 3])
    derived = CalculationBinding(("annual",), None, (), None)
    delta = calculate_evidence(
        "delta",
        "t",
        "difference",
        [Operand("new", ("value",)), Operand("old", ("value",))],
        store,
        require_linked=True,
        binding=derived,
    )
    assert delta.payload()["value"] == 20
    assert delta.payload()["scope"]["unit"] == "pieces"
    assert delta.payload()["scope"]["query_evidence_id"] == "q"


@pytest.mark.parametrize("indices", [[0], [0, 0], [0, 2], [0, 1, 2, 3]])
def test_group_requires_exact_complete_set_of_contributing_rows(indices):
    store, binding = fixture()
    with pytest.raises(CalculationError):
        total(store, binding, "bad", indices)


def test_individual_project_difference_cannot_satisfy_annual_total_binding():
    store, binding = fixture()
    total(store, binding, "old", [0, 1])
    with pytest.raises(CalculationError):
        calculate_evidence(
            "bad",
            "t",
            "difference",
            [
                Operand("q", ("data", 2, "quantity")),
                Operand("q", ("data", 0, "quantity")),
            ],
            store,
            require_linked=True,
            binding=CalculationBinding(("annual",), None, (), None),
        )


def test_incomplete_population_cannot_be_aggregated():
    store, binding = fixture(complete=False)
    with pytest.raises(CalculationError):
        total(store, binding, "bad", [0, 1])


def test_scope_metadata_is_recomputed_not_trusted():
    store, binding = fixture()
    valid = total(store, binding, "old", [0, 1])
    payload = valid.payload()
    payload["scope"]["unit"] = "EUR"
    forged = EvidenceRecord.create(
        "forged", "t", "calculation", valid.call_identity, valid.provenance, payload
    )
    store.add(
        forged,
        requirement=EvidenceRequirement(
            "annual", "calculation", "annual", "sum", binding
        ),
    )
    with pytest.raises(CalculationError):
        calculate_evidence(
            "bad",
            "t",
            "sum",
            [Operand("forged", ("value",))],
            store,
            require_linked=True,
        )


def test_unknown_unit_stays_unknown_without_guessing_field_names():
    store, binding = fixture()
    result = total(store, replace(binding, unit_path=None), "total", [0, 1])
    assert result.payload()["scope"]["unit"] is None


@pytest.mark.parametrize(
    ("date_value", "expected_year"),
    [
        ("2023-01-02", 2023),
        ("2023-12-31T23:30:00-02:00", 2024),
        ("2024-01-01T01:00:00+02:00", 2023),
    ],
)
def test_explicit_utc_year_grouping_respects_timestamp_offsets(
    date_value, expected_year
):
    from general_manager.chat.planned.calculation_scope import group_key

    binding = CalculationBinding(("q",), ("quantity",), utc_year_by=(("shippedAt",),))
    assert group_key({"shippedAt": date_value}, binding) == [expected_year]
    assert CalculationBinding.from_mapping(binding.as_mapping()) == binding


@pytest.mark.parametrize(
    "date_value",
    [
        "2023",
        "next year",
        "2023-02-30",
        "2023-01-01T12:00:00",
        "0001-01-01T00:00:00+01:00",
        2023,
        None,
    ],
)
def test_year_grouping_rejects_ambiguous_or_invalid_dates(date_value):
    from general_manager.chat.planned.calculation_scope import group_key

    binding = CalculationBinding(("q",), ("quantity",), utc_year_by=(("shippedAt",),))
    with pytest.raises(CalculationError):
        group_key({"shippedAt": date_value}, binding)


def _dated_rows(rows):
    store = EvidenceStore()
    query = EvidenceRecord.create(
        "q",
        "t",
        "query",
        canonical_call_identity("query", {"manager": "Shipment"}),
        {},
        {"data": rows, "total_count": len(rows), "has_more": False, "complete": True},
    )
    store.add(query, requirement=EvidenceRequirement("actuals", "query", "read", None))
    return store


def test_distinct_days_in_same_year_form_one_complete_group():
    rows = [
        {"shippedAt": "2023-01-01", "quantity": 50, "unit": "pieces"},
        {"shippedAt": "2023-11-30", "quantity": 50, "unit": "pieces"},
        {"shippedAt": "2024-01-01", "quantity": 120, "unit": "pieces"},
    ]
    binding = CalculationBinding(
        ("actuals",), ("quantity",), (), ("unit",), (("shippedAt",),)
    )
    store = _dated_rows(rows)
    assert total(store, binding, "annual2023", [0, 1]).payload()["value"] == 100
    with pytest.raises(CalculationError):
        total(store, binding, "partial", [0])


def test_explicit_mixed_units_in_one_group_are_rejected():
    rows = [
        {"year": 2023, "quantity": 50, "unit": "pieces"},
        {"year": 2023, "quantity": 50, "unit": "kg"},
    ]
    binding = CalculationBinding(("actuals",), ("quantity",), (("year",),), ("unit",))
    with pytest.raises(CalculationError):
        total(_dated_rows(rows), binding, "mixed", [0, 1])


def test_shared_ancestry_does_not_multiply_scope_metadata():
    store, binding = fixture()
    previous = total(store, binding, "base", [0, 1])
    source_requirement = "annual"
    for index in range(8):
        derived = CalculationBinding((source_requirement,), None)
        record = calculate_evidence(
            f"delta{index}",
            "t",
            "difference",
            [
                Operand(previous.evidence_id, ("value",)),
                Operand(previous.evidence_id, ("value",)),
            ],
            store,
            require_linked=True,
            binding=derived,
        )
        assert record.payload()["value"] == 0
        assert record.payload()["scope"]["leaf_paths"] == [
            ["data", 0, "quantity"],
            ["data", 1, "quantity"],
        ]
        assert record.payload()["scope"]["groups"] == [[2023]]
        source_requirement = f"delta{index}"
        store.add(
            record,
            requirement=EvidenceRequirement(
                source_requirement, "calculation", "difference", "difference", derived
            ),
        )
        previous = record
