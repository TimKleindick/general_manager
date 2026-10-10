"""Arithmetic can only convert dimensions from exact current linked sources."""

from dataclasses import replace
from decimal import Decimal, localcontext
from typing import Any

import pytest

from general_manager.chat.planned.calculations import (
    CalculationError,
    CalculationOperand,
    calculate,
    calculate_evidence,
)
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    canonical_call_identity,
)
from general_manager.chat.planned.models import (
    CalculationBinding,
    EvidenceRequirement,
    SchemaBinding,
)
from general_manager.chat.schema_inspection import inspect_manager_schema
from tests.unit.test_graphql_unit_contract_v50 import (
    unit_schema as _unit_schema_fixture,
    _attach,
)

unit_schema = _unit_schema_fixture


def test_multiply_is_binary_exact_decimal_arithmetic() -> None:
    with localcontext() as context:
        context.prec = 3
        assert calculate("multiply", ["123456789.125", "0.00000125"]) == Decimal(
            "154.32098640625"
        )


@pytest.mark.parametrize(
    "values",
    [[], [1], [1, 2, 3], [True, 2], [None, 2], ["NaN", 2], ["Infinity", 2], [[1], 2]],
)
def test_multiply_rejects_wrong_arity_or_nonfinite_scalars(values: Any) -> None:
    with pytest.raises(CalculationError):
        calculate("multiply", values)


def _setup(
    fixture: Any, case: str = "valid"
) -> tuple[EvidenceStore, CalculationBinding, list[CalculationOperand]]:
    from general_manager.chat.planned.models import ConversionBinding

    _attach(fixture)
    store = EvidenceStore()
    for manager, requirement_id, evidence_id in [
        ("Item", "quantity_schema", "sq"),
        ("Other", "factor_schema", "sf"),
    ]:
        payload = inspect_manager_schema(manager)
        if case == "forged_dimension" and manager == "Other":
            payload["output_fields"]["rate"]["unit_contract"]["target"]["dimension"] = {
                "[length]": 1
            }
        record = EvidenceRecord.create(
            evidence_id,
            "t",
            "schema",
            canonical_call_identity("get_manager_schema", {"manager": manager}),
            {"tool": "get_manager_schema", "manager": manager},
            payload,
        )
        store.observe_schema("t", payload)
        requirement = EvidenceRequirement(
            requirement_id,
            "schema",
            "Explicit current dimensions",
            None,
            schema=SchemaBinding(manager, "overview", (), "current"),
        )
        store.add(record, requirement=requirement)
    rows = [
        {
            "id": "q1",
            "amount": "12",
            "unitLabel": "widgets",
            "other": {"id": "o1", "rate": "1.25"},
        },
        {
            "id": "q2",
            "amount": "8",
            "unitLabel": "widgets",
            "other": {"id": "o2", "rate": "2.5"},
        },
    ]
    changes = {"null": None, "bool": True, "nonfinite": "NaN"}
    if case in changes:
        rows[0]["other"]["rate"] = changes[case]
    if case == "raw_unit":
        rows[0]["unitLabel"] = "gram"
    if case == "missing_id":
        del rows[0]["other"]["id"]
    if case == "duplicate_population":
        rows[1]["id"] = "q1"
    fields = ["id", "amount", "unitLabel", {"other": ["id", "rate"]}]
    if case == "unobserved_factor":
        fields[-1] = {"other": ["id"]}
    args = {"manager": "Item", "fields": fields}
    payload = {
        "data": rows,
        "complete": case != "partial",
        "has_more": case == "partial",
        "total_count": 3 if case == "wrong_count" else 2,
    }
    query = EvidenceRecord.create(
        "q",
        "foreign" if case == "foreign" else "t",
        "query",
        canonical_call_identity("query", args),
        {"manager": "Other" if case == "wrong_provenance" else "Item", "tool": "query"},
        payload,
    )
    store.add(
        query,
        requirement=None
        if case == "unlinked"
        else EvidenceRequirement(
            "population", "query", "Complete source population", None
        ),
    )
    binding = CalculationBinding(
        ("population",),
        ("amount",),
        unit_path=("unitLabel",),
        conversion=ConversionBinding(
            factor_path=("other", "rate"),
            factor_identity_path=("other", "id"),
            schema_requirement_ids=("quantity_schema", "factor_schema"),
            schema_evidence_ids=("sq", "sf"),
        ),
    )
    operands = [
        CalculationOperand("q", ("data", index, *path))
        for index in range(2)
        for path in [("amount",), ("other", "rate")]
    ]
    if case == "trimmed":
        operands = operands[:2]
    if case == "wrong_identity_path":
        binding = replace(
            binding,
            conversion=replace(binding.conversion, factor_identity_path=("id",)),
        )
    if case == "stale":
        from general_manager.interface.unit_contract import (
            FieldUnitContract,
            UNIT_EXTENSION,
        )

        fixture[0].get_type("Other").fields["rate"].extensions[UNIT_EXTENSION] = (
            FieldUnitContract.factor(source_unit="count", target_unit="m").as_mapping()
        )
    if case == "hidden":
        fixture[1]["Other"].chat_exposed = False
    return store, binding, operands


def test_complete_rowwise_conversion_preserves_declared_dimensions_and_sources(
    unit_schema: Any,
) -> None:
    store, binding, operands = _setup(unit_schema)
    result = calculate_evidence(
        "converted",
        "t",
        "sum_products",
        operands,
        store,
        binding=binding,
        require_linked=True,
    )
    payload = result.payload()
    assert payload["value"] == 35
    assert payload["scope"]["unit"] == "gram"
    assert payload["scope"]["population_complete"] is True
    assert payload["scope"]["conversion"]["factor_contract"]["source"]["dimension"] == {
        "[piece_count]": 1
    }
    assert payload["scope"]["conversion"]["factor_contract"]["target"]["dimension"] == {
        "[mass]": 1
    }
    assert len(payload["scope"]["conversion"]["schema_sources"]) == 2
    assert payload["operands"] == [
        {"evidence_id": item.evidence_id, "path": list(item.path)} for item in operands
    ]


@pytest.mark.parametrize(
    "case",
    [
        "partial",
        "wrong_count",
        "trimmed",
        "unlinked",
        "foreign",
        "wrong_provenance",
        "raw_unit",
        "null",
        "bool",
        "nonfinite",
        "missing_id",
        "duplicate_population",
        "unobserved_factor",
        "wrong_identity_path",
        "forged_dimension",
        "stale",
        "hidden",
    ],
)
def test_conversion_rejects_unproven_population_units_identities_or_schema(
    unit_schema: Any, case: str
) -> None:
    store, binding, operands = _setup(unit_schema, case)
    with pytest.raises(CalculationError):
        calculate_evidence(
            "bad",
            "t",
            "sum_products",
            operands,
            store,
            binding=binding,
            require_linked=True,
        )


def test_legacy_binding_mapping_and_sum_remain_byte_exact() -> None:
    binding = CalculationBinding(("population",), ("amount",), unit_path=("unitLabel",))
    assert binding.as_mapping() == {
        "source_requirement_ids": ["population"],
        "value_path": ["amount"],
        "group_by": [],
        "unit_path": ["unitLabel"],
    }
    assert calculate("sum", ["12", "8"]) == Decimal(20)


def test_invalid_dimension_authority_is_rejected_before_numeric_arithmetic(
    unit_schema: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from general_manager.chat.planned import calculations

    store, binding, operands = _setup(unit_schema, "raw_unit")
    called = []

    def numeric(*args: Any) -> Any:
        called.append(args)
        message = "arithmetic preceded unit proof"
        raise AssertionError(message)

    monkeypatch.setattr(calculations, "calculate", numeric)
    with pytest.raises(CalculationError):
        calculate_evidence(
            "bad",
            "t",
            "sum_products",
            operands,
            store,
            binding=binding,
            require_linked=True,
        )
    assert called == []


@pytest.mark.parametrize("mutation", [None, "scope", "snapshot", "value"])
def test_derived_conversion_rechecks_original_proofs_and_value(
    unit_schema: Any, mutation: str | None
) -> None:
    store, binding, operands = _setup(unit_schema)
    converted = calculate_evidence(
        "converted",
        "t",
        "sum_products",
        operands,
        store,
        binding=binding,
        require_linked=True,
    )
    payload = converted.payload()
    if mutation == "scope":
        payload["scope"]["unit"] = "meter"
    elif mutation == "value":
        payload["value"] = 99
    elif mutation == "snapshot":
        from general_manager.interface.unit_contract import (
            FieldUnitContract,
            UNIT_EXTENSION,
        )

        unit_schema[0].get_type("Other").fields["rate"].extensions[UNIT_EXTENSION] = (
            FieldUnitContract.factor(source_unit="count", target_unit="m").as_mapping()
        )
    converted = EvidenceRecord.create(
        converted.evidence_id,
        converted.task_id,
        converted.kind,
        converted.call_identity,
        converted.provenance,
        payload,
    )
    store.add(
        converted,
        requirement=EvidenceRequirement(
            "converted",
            "calculation",
            "Verified conversion",
            "sum_products",
            binding=binding,
        ),
    )
    args = ("derived", "t", "sum", [CalculationOperand("converted", ("value",))], store)
    if mutation is None:
        assert calculate_evidence(*args, require_linked=True).payload()["value"] == 35
    else:
        with pytest.raises(CalculationError):
            calculate_evidence(*args, require_linked=True)
