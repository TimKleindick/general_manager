"""Calculation values and evidence must not depend on the caller's Decimal context."""

from decimal import (
    Context,
    Decimal,
    Inexact,
    MAX_EMAX,
    MIN_EMIN,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_HALF_UP,
    localcontext,
)
from itertools import permutations

import pytest

from general_manager.chat.planned.calculations import (
    CalculationError,
    CalculationOperand,
    calculate,
    calculate_evidence,
)
from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
from general_manager.chat.planned.models import CalculationBinding, EvidenceRequirement


@pytest.fixture(
    params=[(6, ROUND_DOWN, False), (80, ROUND_CEILING, True), (3, ROUND_HALF_UP, True)]
)
def host_context(request):
    precision, rounding, trapped = request.param
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        context.Emin = -9
        context.Emax = 9
        context.clamp = 1
        context.capitals = 0
        for signal in context.traps:
            context.traps[signal] = trapped
        context.clear_flags()
        context.flags[Inexact] = True
        before = repr(context)
        yield context
        assert repr(context) == before


@pytest.mark.parametrize(
    "values,residual",
    [
        (("1E28", "1", "-1E28"), "1"),
        (("9.999999E80", "0.0000000017", "-9.999999E80"), "1.7E-9"),
        (("1.23456789123456789E-50", "7E-120", "-1.23456789123456789E-50"), "7E-120"),
    ],
)
def test_exact_sums_retain_cancellation_residual_in_every_order(
    host_context: Context, values: tuple[str, ...], residual: str
) -> None:
    for order in permutations(values):
        assert calculate("sum", order) == Decimal(residual)


@pytest.mark.parametrize(
    "operation,values,expected",
    [
        ("sum", ["12345.67", ".01"], "12345.68"),
        ("sum", ["9.99"] * 101, "1008.99"),
        ("sum", ["0E-1000", "0E1000"], "0"),
        ("difference", ["12345.67", ".01"], "12345.66"),
        ("difference", ["1E40", "1"], "9999999999999999999999999999999999999999"),
        (
            "multiply",
            ["123456789.123456789", "10.000000001"],
            "1234567891.358024679123456789",
        ),
        ("sum_products", ["1E60", "1E60", "7E-30", "1E-30", "-1E60", "1E60"], "7E-60"),
        ("minimum", ["1E30", "1E-30"], "1E-30"),
        ("maximum", ["1E30", "1E-30"], "1E30"),
        ("sum", ["1E1000001", "1E1000001"], "2E1000001"),
        ("multiply", ["1E-1000001", "1E-1000001"], "1E-2000002"),
        ("sum", [f"1E{MIN_EMIN - 100}"], f"1E{MIN_EMIN - 100}"),
        ("multiply", [f"1E{MIN_EMIN}", "1E-100"], f"1E{MIN_EMIN - 100}"),
    ],
)
def test_exact_operations_are_isolated_from_host_settings(
    host_context: Context, operation: str, values: list[str], expected: str
) -> None:
    assert calculate(operation, values) == Decimal(expected)


@pytest.mark.parametrize(
    "operation,values,expected",
    [
        ("ratio", ["1", "3"], "0.3333333333333333333333333333"),
        ("percentage", ["1", "3"], "33.33333333333333333333333333"),
        ("average", ["1E28", "1", "-1E28"], "0.3333333333333333333333333333"),
        ("average", ["12345.67", ".01"], "6172.84"),
        (
            "ratio",
            ["12345678901234567890123456785", "10"],
            "1234567890123456789012345678",
        ),
        (
            "ratio",
            ["12345678901234567890123456795", "10"],
            "1234567890123456789012345680",
        ),
        (
            "ratio",
            ["-12345678901234567890123456785", "10"],
            "-1234567890123456789012345678",
        ),
        ("ratio", ["1E30", "1E-30"], "1E60"),
        ("percentage", ["1E-30", "1E30"], "1E-58"),
        (
            "percentage",
            [f"1E{MIN_EMIN - 100}", "1E-100"],
            f"1E{MIN_EMIN + 2}",
        ),
    ],
)
def test_divisions_use_28_significant_digits_and_half_even_rounding(
    host_context: Context, operation: str, values: list[str], expected: str
) -> None:
    assert calculate(operation, values) == Decimal(expected)


@pytest.mark.parametrize(
    "value", ["not-a-number", "NaN", "sNaN", "Infinity", "-Infinity"]
)
def test_invalid_numeric_inputs_leave_host_context_unchanged(
    host_context: Context, value: str
) -> None:
    with pytest.raises(CalculationError, match="finite numbers"):
        calculate("sum", [value])


@pytest.mark.parametrize(
    "operation,values",
    [
        ("multiply", [f"1E{MAX_EMAX}", "10"]),
        ("multiply", [f"1E{MIN_EMIN}", f"1E{MIN_EMIN}"]),
        ("ratio", [f"1E{MAX_EMAX}", "0.1"]),
        ("ratio", [f"1E{MIN_EMIN}", "1E100"]),
    ],
)
def test_decimal_range_failures_are_calculation_errors(
    host_context: Context, operation: str, values: list[str]
) -> None:
    with pytest.raises(CalculationError, match="range"):
        calculate(operation, values)


@pytest.mark.parametrize(
    "operation,values,expected",
    [
        ("sum", ["1E28", "1", "-1E28"], 1),
        ("sum", ["12345.67", ".01"], "12345.68"),
        ("average", ["1E28", "1", "-1E28"], "0.3333333333333333333333333333"),
    ],
)
def test_bound_evidence_serializes_and_recomputes_under_a_different_host_context(
    host_context: Context, operation: str, values: list[str], expected: int | str
) -> None:
    store = EvidenceStore()
    store.add(
        EvidenceRecord.create(
            "query",
            "task",
            "query",
            "query-call",
            {},
            {
                "data": [{"value": value, "unit": "kg"} for value in values],
                "complete": True,
                "has_more": False,
                "total_count": len(values),
            },
        ),
        requirement=EvidenceRequirement(
            "source", "query", "Complete observed values", None
        ),
    )
    binding = CalculationBinding(("source",), ("value",), (), ("unit",))
    result = calculate_evidence(
        "result",
        "task",
        operation,
        [
            CalculationOperand("query", ("data", index, "value"))
            for index in range(len(values))
        ],
        store,
        binding=binding,
        require_linked=True,
    )
    assert result.payload()["value"] == expected
    assert result.payload()["scope"]["unit"] == "kg"
    assert result.payload()["scope"]["population_complete"] is True
    store.add(
        result,
        requirement=EvidenceRequirement(
            "computed", "calculation", "Verified result", operation, binding
        ),
    )
    with localcontext() as different:
        different.prec = 2
        different.rounding = ROUND_HALF_UP
        derived = calculate_evidence(
            "derived",
            "task",
            "sum",
            [CalculationOperand("result", ("value",))],
            store,
            require_linked=True,
        )
    assert derived.payload()["value"] == expected


def test_stored_rounded_cancellation_result_fails_recomputation() -> None:
    store = EvidenceStore()
    store.add(
        EvidenceRecord.create(
            "q", "t", "query", "query-call", {}, {"data": ["1E28", "1", "-1E28"]}
        )
    )
    result = calculate_evidence(
        "sum", "t", "sum", [CalculationOperand("q", ("data",))], store
    )
    payload = result.payload()
    payload["value"] = 0
    store.add(
        EvidenceRecord.create(
            result.evidence_id,
            result.task_id,
            result.kind,
            result.call_identity,
            result.provenance,
            payload,
        )
    )
    with pytest.raises(CalculationError, match="does not match its derivation"):
        calculate_evidence(
            "next", "t", "sum", [CalculationOperand("sum", ("value",))], store
        )


def test_malformed_stored_value_does_not_change_host_context(
    host_context: Context,
) -> None:
    store = EvidenceStore()
    store.add(EvidenceRecord.create("q", "t", "query", "query-call", {}, 1))
    result = calculate_evidence("sum", "t", "sum", [CalculationOperand("q", ())], store)
    payload = result.payload()
    payload["value"] = "not-a-number"
    store.add(
        EvidenceRecord.create(
            result.evidence_id,
            result.task_id,
            result.kind,
            result.call_identity,
            result.provenance,
            payload,
        )
    )
    with pytest.raises(CalculationError, match="finite numbers"):
        calculate_evidence(
            "next", "t", "sum", [CalculationOperand("sum", ("value",))], store
        )
