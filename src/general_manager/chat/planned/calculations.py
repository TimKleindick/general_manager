"""Deterministic, allow-listed calculations over structured evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import NoReturn

from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    canonical_call_identity,
)
from general_manager.chat.planned.models import CALCULATION_OPERATIONS


class CalculationError(ValueError):
    """Raised when a requested calculation cannot be safely evaluated."""


def _calculation_error(message: str, *, cause: BaseException | None = None) -> NoReturn:
    if cause is None:
        raise CalculationError(message)
    raise CalculationError(message) from cause


def _type_error(message: str) -> NoReturn:
    raise TypeError(message)


@dataclass(frozen=True)
class CalculationOperand:
    """A structured path into one query evidence payload."""

    evidence_id: str
    path: tuple[str | int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not self.evidence_id.strip():
            _calculation_error("evidence_id must be a non-empty string.")
        if not isinstance(self.path, tuple):
            _calculation_error("operand path must be a tuple.")
        if any(
            not isinstance(part, (str, int)) or isinstance(part, bool)
            for part in self.path
        ):
            _calculation_error("operand path parts must be strings or integers.")
        if any(isinstance(part, int) and part < 0 for part in self.path):
            _calculation_error("operand path indices must be non-negative.")


def calculate(operation: str, operands: Sequence[object]) -> Decimal | int:
    """Evaluate one of the eight named operations using Decimal arithmetic."""
    if operation not in CALCULATION_OPERATIONS:
        _calculation_error(f"unsupported calculation operation {operation!r}.")
    if not isinstance(operands, Sequence) or isinstance(
        operands, (str, bytes, bytearray)
    ):
        _calculation_error("operands must be a sequence.")

    values = list(operands)
    if operation == "count":
        if len(values) != 1:
            _calculation_error("count requires exactly one operand.")
        return _count(values[0])

    if operation in ("difference", "ratio", "percentage"):
        if len(values) != 2:
            _calculation_error(f"{operation} requires exactly two operands.")
    elif not values:
        _calculation_error(f"{operation} requires at least one operand.")

    values = _expand_numeric_values(values)
    if not values and operation in ("sum", "average", "minimum", "maximum"):
        _calculation_error(
            f"{operation} requires at least one value after sequence expansion."
        )
    numbers = [_decimal(value) for value in values]
    if operation == "sum":
        return sum(numbers, Decimal(0))
    if operation == "average":
        return sum(numbers, Decimal(0)) / Decimal(len(numbers))
    if operation == "minimum":
        return min(numbers)
    if operation == "maximum":
        return max(numbers)
    left, right = numbers
    if operation == "difference":
        return left - right
    if right == 0:
        _calculation_error("division by zero is not allowed.")
    if operation == "ratio":
        return left / right
    return left / right * Decimal(100)


def calculate_evidence(
    evidence_id: str,
    task_id: str,
    operation: str,
    operands: Sequence[CalculationOperand],
    store: EvidenceStore,
    *,
    provenance: Mapping[str, str] | None = None,
    call_identity: str | None = None,
) -> EvidenceRecord:
    """Compute a value from query evidence and return derived evidence."""
    if not isinstance(store, EvidenceStore):
        _type_error("store must be an EvidenceStore.")
    if not isinstance(operands, Sequence) or isinstance(
        operands, (str, bytes, bytearray)
    ):
        _calculation_error("operands must be a sequence of CalculationOperand records.")

    values: list[object] = []
    normalized_operands: list[CalculationOperand] = []
    for operand in operands:
        if not isinstance(operand, CalculationOperand):
            _calculation_error("calculation operands must be structured records.")
        source = store.get(operand.evidence_id)
        if source is None:
            _calculation_error(f"evidence {operand.evidence_id!r} was not found.")
        if source.kind != "query":
            _calculation_error("calculation operands must reference query evidence.")
        try:
            value = source.payload()
            for part in operand.path:
                value = _path_value(value, part)
        except (KeyError, IndexError, TypeError) as exc:
            _calculation_error(
                f"operand path is invalid for evidence {operand.evidence_id!r}.",
                cause=exc,
            )
        values.append(value)
        normalized_operands.append(operand)

    result = calculate(operation, values)
    payload_value: int | str
    if isinstance(result, int):
        payload_value = result
    elif result == result.to_integral_value():
        payload_value = int(result)
    else:
        payload_value = format(result, "f")
    payload = {
        "operation": operation,
        "value": payload_value,
        "operands": [
            {"evidence_id": operand.evidence_id, "path": list(operand.path)}
            for operand in normalized_operands
        ],
    }
    if call_identity is None:
        call_identity = _calculation_call_identity(operation, normalized_operands)
    return EvidenceRecord.create(
        evidence_id,
        task_id,
        "calculation",
        call_identity,
        {"calculator": "framework", "operation": operation}
        if provenance is None
        else provenance,
        payload,
    )


def _path_value(value: object, part: str | int) -> object:
    if isinstance(value, Mapping) and isinstance(part, str):
        return value[part]
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and isinstance(part, int)
    ):
        return value[part]
    _type_error("path part is incompatible with the current payload value.")


def _count(value: object) -> int:
    if isinstance(value, Mapping):
        return len(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value)
    _calculation_error("count requires a structured collection operand.")


def _expand_numeric_values(values: list[object]) -> list[object]:
    if (
        len(values) == 1
        and isinstance(values[0], Sequence)
        and not isinstance(values[0], (str, bytes, bytearray))
    ):
        return list(values[0])
    return values


def _decimal(value: object) -> Decimal:
    if (
        isinstance(value, bool)
        or value is None
        or isinstance(value, (Mapping, list, tuple, dict))
    ):
        _calculation_error("numeric operands must be scalar numbers.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        _calculation_error("numeric operands must be finite numbers.", cause=exc)
    if not result.is_finite():
        _calculation_error("numeric operands must be finite numbers.")
    return result


def _calculation_call_identity(
    operation: str, operands: Sequence[CalculationOperand]
) -> str:
    return canonical_call_identity(
        "calculate",
        {
            "operation": operation,
            "operands": [
                {"evidence_id": operand.evidence_id, "path": list(operand.path)}
                for operand in operands
            ],
        },
    )


__all__ = [
    "CalculationError",
    "CalculationOperand",
    "calculate",
    "calculate_evidence",
]
