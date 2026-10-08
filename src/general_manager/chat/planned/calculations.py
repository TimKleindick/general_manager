"""Deterministic, allow-listed calculations over structured evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import NoReturn

from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    canonical_call_identity,
)
from general_manager.chat.planned.models import (
    CALCULATION_OPERATIONS,
    CalculationBinding,
)
from general_manager.chat.planned.calculation_scope import calculation_scope


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
    """A query path or the verified scalar value of derived evidence."""

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
    """Evaluate explicit numeric operations; units require separate source proofs."""
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

    if operation in ("difference", "ratio", "percentage", "multiply"):
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
    if operation in ("multiply", "sum_products"):
        if len(numbers) % 2:
            _calculation_error("sum_products requires quantity/factor pairs.")
        # Form exact products before sizing the complete sum, including cancellation.
        with localcontext() as context:
            context.prec = max(
                28,
                max(
                    len(numbers[index].as_tuple().digits)
                    + len(numbers[index + 1].as_tuple().digits)
                    for index in range(0, len(numbers), 2)
                ),
            )
            products = [
                numbers[index] * numbers[index + 1]
                for index in range(0, len(numbers), 2)
            ]
            nonzero = [item for item in products if item]
            if not nonzero:
                return Decimal(0)
            context.prec = max(
                28,
                max(item.adjusted() for item in nonzero)
                - min(int(item.as_tuple().exponent) for item in nonzero)
                + 1
                + len(str(len(nonzero))),
            )
            return sum(products, Decimal(0))
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
    require_linked: bool = False,
    binding: CalculationBinding | None = None,
) -> EvidenceRecord:
    """Compute from task-owned queries or transitively verified calculations.

    The scheduler requires every source to be linked to a declared requirement.
    Standalone callers may compute before linking, but task ownership is mandatory.
    """
    if not isinstance(store, EvidenceStore):
        _type_error("store must be an EvidenceStore.")
    if not isinstance(operands, Sequence) or isinstance(
        operands, (str, bytes, bytearray)
    ):
        _calculation_error("operands must be a sequence of CalculationOperand records.")

    resolver = _OperandResolver(store, task_id, require_linked)
    normalized_operands = list(operands)
    if operation == "sum_products" and (binding is None or binding.conversion is None):
        _calculation_error("sum_products evidence requires a source-bound conversion.")
    verified_scope = (
        calculation_scope(operation, normalized_operands, store, task_id, binding)
        if binding is not None and binding.conversion is not None
        else None
    )
    values = [resolver.resolve(operand) for operand in normalized_operands]
    result = calculate(operation, values)
    payload_value: int | str
    if isinstance(result, int):
        payload_value = result
    elif result == result.to_integral_value():
        payload_value = int(result)
    else:
        payload_value = format(result, "f")
    payload: dict[str, object] = {
        "operation": operation,
        "value": payload_value,
        "operands": [
            {"evidence_id": operand.evidence_id, "path": list(operand.path)}
            for operand in normalized_operands
        ],
    }
    if binding is not None:
        payload["binding"] = binding.as_mapping()
        payload["scope"] = (
            verified_scope
            if verified_scope is not None
            else calculation_scope(
                operation, normalized_operands, store, task_id, binding
            )
        )
    if call_identity is None:
        call_identity = _calculation_call_identity(
            operation, normalized_operands, binding
        )
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


class _OperandResolver:
    """Re-evaluate each derived node once; no recursion or trusted model labels."""

    def __init__(
        self, store: EvidenceStore, task_id: str, require_linked: bool
    ) -> None:
        self.store = store
        self.task_id = task_id
        self.require_linked = require_linked
        self.verified: dict[str, Decimal | int] = {}

    def _source(self, evidence_id: str) -> EvidenceRecord:
        source = self.store.get(evidence_id)
        if source is None:
            _calculation_error(f"evidence {evidence_id!r} was not found.")
        if source.task_id != self.task_id:
            _calculation_error("calculation evidence must belong to the current task.")
        if self.require_linked and not self.store.is_linked(self.task_id, evidence_id):
            _calculation_error(
                "calculation evidence must be linked to a task requirement."
            )
        if source.kind not in ("query", "calculation"):
            _calculation_error(
                "operands require query or verified calculation evidence."
            )
        return source

    def _derivation(
        self, source: EvidenceRecord
    ) -> tuple[str, list[CalculationOperand], CalculationBinding | None]:
        payload = source.payload()
        if not isinstance(payload, Mapping) or set(payload) not in (
            {"operation", "value", "operands"},
            {"operation", "value", "operands", "binding", "scope"},
        ):
            _calculation_error("invalid calculation evidence payload.")
        operation, raw = payload["operation"], payload["operands"]
        if not isinstance(operation, str) or not isinstance(raw, list):
            _calculation_error("invalid calculation evidence derivation.")
        operands: list[CalculationOperand] = []
        for item in raw:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"evidence_id", "path"}
                or not isinstance(item["path"], list)
            ):
                _calculation_error("invalid calculation evidence operand.")
            operands.append(
                CalculationOperand(item["evidence_id"], tuple(item["path"]))
            )
        try:
            binding = (
                CalculationBinding.from_mapping(payload["binding"])
                if "binding" in payload
                else None
            )
        except ValueError as exc:
            _calculation_error("invalid stored calculation binding.", cause=exc)
        if operation == "sum_products" and (
            binding is None or binding.conversion is None
        ):
            _calculation_error(
                "sum_products evidence requires a source-bound conversion."
            )
        if source.provenance.get(
            "calculator"
        ) != "framework" or source.call_identity != _calculation_call_identity(
            operation, operands, binding
        ):
            _calculation_error(
                "calculation evidence has no matching framework derivation."
            )
        return operation, operands, binding

    def _value(self, operand: CalculationOperand) -> object:
        source = self._source(operand.evidence_id)
        if source.kind == "calculation":
            if operand.path != ("value",):
                _calculation_error(
                    "derived operands must reference only the numeric value."
                )
            return self.verified[operand.evidence_id]
        try:
            value = source.payload()
            for part in operand.path:
                value = _path_value(value, part)
        except (KeyError, IndexError, TypeError) as exc:
            _calculation_error(
                f"operand path is invalid for evidence {operand.evidence_id!r}.",
                cause=exc,
            )
        return value

    def resolve(self, operand: CalculationOperand) -> object:
        if not isinstance(operand, CalculationOperand):
            _calculation_error("calculation operands must be structured records.")
        active: set[str] = set()
        stack = [(operand.evidence_id, False)]
        while stack:
            evidence_id, expanded = stack.pop()
            source = self._source(evidence_id)
            if source.kind == "query" or evidence_id in self.verified:
                continue
            operation, operands, binding = self._derivation(source)
            if binding is not None and binding.conversion is not None:
                if source.payload()["scope"] != calculation_scope(
                    operation, operands, self.store, self.task_id, binding
                ):
                    _calculation_error(
                        "stored calculation scope does not match its sources."
                    )
            if expanded:
                result = calculate(operation, [self._value(item) for item in operands])
                if _decimal(source.payload()["value"]) != result:
                    _calculation_error(
                        "stored calculation value does not match its derivation."
                    )
                if (
                    binding is not None
                    and binding.conversion is None
                    and source.payload()["scope"]
                    != calculation_scope(
                        operation, operands, self.store, self.task_id, binding
                    )
                ):
                    _calculation_error(
                        "stored calculation scope does not match its sources."
                    )
                self.verified[evidence_id] = result
                active.remove(evidence_id)
                continue
            if evidence_id in active:
                _calculation_error("calculation evidence contains a dependency cycle.")
            active.add(evidence_id)
            stack.append((evidence_id, True))
            stack.extend((item.evidence_id, False) for item in reversed(operands))
        return self._value(operand)


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
    operation: str,
    operands: Sequence[CalculationOperand],
    binding: CalculationBinding | None = None,
) -> str:
    return canonical_call_identity(
        "calculate",
        {
            "operation": operation,
            **({"binding": binding.as_mapping()} if binding is not None else {}),
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
