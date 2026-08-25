"""Contract tests for deterministic planned-chat calculations."""

from __future__ import annotations

from decimal import Decimal

import pytest

from general_manager.chat.planned.calculations import (
    CalculationError,
    CalculationOperand,
    calculate,
    calculate_evidence,
)
from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore


@pytest.mark.parametrize(
    ("operation", "operands", "expected"),
    [
        ("count", [[1, 2, 3]], 3),
        ("sum", ["1.1", "2.2"], Decimal("3.3")),
        ("average", [1, 2, 3], Decimal("2")),
        ("minimum", [3, 1, 2], Decimal("1")),
        ("maximum", [3, 1, 2], Decimal("3")),
        ("difference", [7, 2], Decimal("5")),
        ("ratio", [6, 3], Decimal("2")),
        ("percentage", [1, 4], Decimal("25")),
    ],
)
def test_supported_calculations(
    operation: str, operands: list[object], expected: Decimal | int
) -> None:
    assert calculate(operation, operands) == expected


@pytest.mark.parametrize(
    ("operation", "operands"),
    [
        ("count", [1, 2]),
        ("sum", []),
        ("average", []),
        ("minimum", []),
        ("maximum", []),
        ("difference", [1]),
        ("difference", [1, 2, 3]),
        ("ratio", [1]),
        ("percentage", [1, 2, 3]),
        ("unknown", [1]),
    ],
)
def test_calculations_reject_wrong_operand_counts_or_operations(
    operation: str, operands: list[object]
) -> None:
    with pytest.raises(CalculationError):
        calculate(operation, operands)


@pytest.mark.parametrize("value", [None, True, object(), "not-a-number"])
def test_numeric_calculations_reject_nonnumeric_operands(value: object) -> None:
    with pytest.raises(CalculationError):
        calculate("sum", [value])


def test_calculations_reject_division_by_zero() -> None:
    with pytest.raises(CalculationError):
        calculate("ratio", [1, 0])
    with pytest.raises(CalculationError):
        calculate("percentage", [1, 0])


def test_calculate_evidence_reads_only_query_evidence_by_structured_path() -> None:
    store = EvidenceStore()
    source = EvidenceRecord.create(
        "ev-query",
        "task-1",
        "query",
        "query-call",
        {"tool": "query"},
        {"data": [{"value": "1.1"}, {"value": "2.2"}]},
    )
    store.add(source)

    result = calculate_evidence(
        "ev-calc",
        "task-1",
        "sum",
        [
            CalculationOperand("ev-query", ("data", 0, "value")),
            CalculationOperand("ev-query", ("data", 1, "value")),
        ],
        store,
    )

    assert result.kind == "calculation"
    assert result.payload() == {
        "operation": "sum",
        "value": "3.3",
        "operands": [
            {"evidence_id": "ev-query", "path": ["data", 0, "value"]},
            {"evidence_id": "ev-query", "path": ["data", 1, "value"]},
        ],
    }


@pytest.mark.parametrize(
    "operand",
    [
        CalculationOperand("missing", ("data", 0)),
        CalculationOperand("ev-query", ("missing",)),
        CalculationOperand("ev-query", ("data", 5)),
    ],
)
def test_calculate_evidence_rejects_missing_or_invalid_paths(
    operand: CalculationOperand,
) -> None:
    store = EvidenceStore()
    store.add(
        EvidenceRecord.create(
            "ev-query", "task-1", "query", "query-call", {}, {"data": [1]}
        )
    )

    with pytest.raises(CalculationError):
        calculate_evidence("ev-calc", "task-1", "sum", [operand], store)


def test_calculate_evidence_rejects_non_query_and_nonnumeric_sources() -> None:
    store = EvidenceStore()
    store.add(EvidenceRecord.create("ev-schema", "task-1", "schema", "call", {}, 1))
    with pytest.raises(CalculationError):
        calculate_evidence(
            "ev-calc",
            "task-1",
            "sum",
            [CalculationOperand("ev-schema", ())],
            store,
        )

    store.add(
        EvidenceRecord.create(
            "ev-query", "task-1", "query", "query-call", {}, {"value": "x"}
        )
    )
    with pytest.raises(CalculationError):
        calculate_evidence(
            "ev-calc",
            "task-1",
            "sum",
            [CalculationOperand("ev-query", ("value",))],
            store,
        )
