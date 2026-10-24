"""Bound expansion and preserve the original unit interpretation of query values."""

from decimal import Decimal
import json
from typing import Any

import pytest

from general_manager.chat.planned import calculations
from general_manager.chat.planned.calculations import (
    CalculationError,
    CalculationOperand,
    calculate_evidence,
)
from general_manager.chat.planned.cross_query import capture_query_schema_witness
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    canonical_call_identity,
)
from general_manager.chat.planned.models import EvidenceRequirement, SchemaBinding
from general_manager.chat.graphql_contract import clear_contract_cache
from general_manager.chat.schema_inspection import inspect_manager_schema
from general_manager.interface.unit_contract import FieldUnitContract, UNIT_EXTENSION
from tests.unit.test_graphql_unit_contract_v50 import unit_schema as _unit_schema
from tests.unit.test_unit_conversion_v50 import _setup

unit_schema = _unit_schema


@pytest.mark.parametrize(
    "operation, values",
    [
        ("sum", ["1e1000000000", "1"]),
        ("sum", ["1e-1000000000", "1"]),
        ("sum", ["1e1000000000", "1", "-1e1000000000"]),
        ("average", ["1e1000000000", "1"]),
        ("difference", ["1e1000000000", "1"]),
        ("sum_products", ["1e1000000000", "1", "1", "1"]),
        ("multiply", ["9" * 3000, "9" * 3000]),
    ],
)
def test_exact_arithmetic_rejects_excessive_working_precision_before_allocation(
    monkeypatch: pytest.MonkeyPatch, operation: str, values: list[str]
) -> None:
    original = calculations.Context

    def bounded_context(**kwargs: Any) -> Any:
        assert kwargs["prec"] <= 4300, "unsafe Decimal expansion reached allocation"
        return original(**kwargs)

    monkeypatch.setattr(calculations, "Context", bounded_context)
    with pytest.raises(CalculationError, match="precision"):
        calculations.calculate(operation, values)


def _scalar_store(value: str) -> EvidenceStore:
    store = EvidenceStore()
    store.add(EvidenceRecord.create("q", "t", "query", "query", {}, {"value": value}))
    return store


@pytest.mark.parametrize("value", ["1e1000000000", "-1e1000000000", "1e-1000000000"])
def test_evidence_rejects_large_representation_before_integer_or_fixed_conversion(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    original = calculations.calculate

    class ObservedDecimal(Decimal):
        def __int__(self) -> int:
            message = "unsafe integer expansion reached conversion"
            raise AssertionError(message)

        def __format__(self, spec: str) -> str:
            message = "unsafe fixed-point expansion reached conversion"
            raise AssertionError(message)

    def observed_calculate(operation: str, operands: Any) -> Decimal:
        return ObservedDecimal(original(operation, operands))

    monkeypatch.setattr(calculations, "calculate", observed_calculate)
    with pytest.raises(CalculationError, match="representation"):
        calculate_evidence(
            "c", "t", "sum", [CalculationOperand("q", ("value",))], _scalar_store(value)
        )


@pytest.mark.parametrize("value", ["0e1000000000", "0e-1000000000"])
def test_zero_does_not_expand_its_exponent(value: str) -> None:
    result = calculate_evidence(
        "c", "t", "sum", [CalculationOperand("q", ("value",))], _scalar_store(value)
    )
    assert result.payload()["value"] == 0


@pytest.mark.parametrize("value", ["1e4299", "1e-4299"])
def test_evidence_accepts_bounded_integer_and_fractional_representation(
    value: str,
) -> None:
    result = calculate_evidence(
        "c", "t", "sum", [CalculationOperand("q", ("value",))], _scalar_store(value)
    ).payload()["value"]
    assert Decimal(str(result)) == Decimal(value)


@pytest.mark.parametrize(
    "damage", [None, "changed_contract", "absent", "unstable", "different_after"]
)
def test_conversion_requires_original_stable_query_schema_witness(
    unit_schema: Any, damage: str | None
) -> None:
    original, binding, operands = _setup(unit_schema)
    query = original.get("q")
    assert query is not None
    captured = capture_query_schema_witness("Item")
    witness = {"version": 1, "before": captured, "after": captured, "stable": True}
    if damage == "unstable":
        witness["stable"] = False
    elif damage == "different_after":
        witness["after"] = {**captured, "Other": "changed"}
    provenance = dict(query.provenance)
    provenance.pop("query_schema_witness", None)
    if damage != "absent":
        provenance["query_schema_witness"] = json.dumps(witness)
    if damage == "changed_contract":
        unit_schema[0].get_type("Other").fields["rate"].extensions[UNIT_EXTENSION] = (
            FieldUnitContract.factor(source_unit="count", target_unit="kg").as_mapping()
        )
        clear_contract_cache()
        assert capture_query_schema_witness("Item") != captured

    store = EvidenceStore()
    store.add(
        EvidenceRecord.create(
            "q", "t", "query", query.call_identity, provenance, query.payload()
        ),
        requirement=EvidenceRequirement("population", "query", "Original values", None),
    )
    for manager, requirement, evidence_id in [
        ("Item", "quantity_schema", "sq"),
        ("Other", "factor_schema", "sf"),
    ]:
        payload = inspect_manager_schema(manager)
        store.observe_schema("t", payload)
        store.add(
            EvidenceRecord.create(
                evidence_id,
                "t",
                "schema",
                canonical_call_identity("get_manager_schema", {"manager": manager}),
                {"manager": manager, "tool": "get_manager_schema"},
                payload,
            ),
            requirement=EvidenceRequirement(
                requirement,
                "schema",
                "Current schema",
                None,
                schema=SchemaBinding(manager, "overview", (), "current"),
            ),
        )
    if damage is None:
        result = calculate_evidence(
            "c",
            "t",
            "sum_products",
            operands,
            store,
            binding=binding,
            require_linked=True,
        )
        assert result.payload()["value"] == 35
        assert result.payload()["scope"]["unit"] == "gram"
    else:
        with pytest.raises(CalculationError, match="query-time schema witness"):
            calculate_evidence(
                "c",
                "t",
                "sum_products",
                operands,
                store,
                binding=binding,
                require_linked=True,
            )
