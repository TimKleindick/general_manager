"""Check exact query populations and units for declared arithmetic bindings."""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, NoReturn
import json
import re
from datetime import date, datetime, timezone
from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import CalculationBinding, PlannedTask

if TYPE_CHECKING:
    from general_manager.chat.planned.calculations import CalculationOperand


def _invalid(message: str) -> NoReturn:
    from general_manager.chat.planned.calculations import CalculationError

    raise CalculationError(message)


def _field(row: object, path: tuple[str, ...]) -> Any:
    value = row
    for part in path:
        if not isinstance(value, Mapping) or part not in value:
            _invalid("bound field is absent from query rows.")
        value = value[part]
    return value


def _utc_year(value: object) -> int:
    if not isinstance(value, str):
        _invalid(
            "UTC year grouping requires an ISO date or timezone-qualified timestamp."
        )
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return date.fromisoformat(value).year
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        _invalid("invalid ISO date for UTC year grouping.")
    if timestamp.tzinfo is None or "T" not in value:
        _invalid("timestamp year grouping requires an explicit timezone.")
    try:
        return timestamp.astimezone(timezone.utc).year
    except (ValueError, OverflowError):
        _invalid("UTC year is outside the supported calendar range.")


def group_key(row: object, binding: CalculationBinding) -> list[object]:
    """Apply only declared scalar fields and the explicit UTC-year transform."""
    values = [_field(row, path) for path in binding.group_by]
    if any(isinstance(value, (Mapping, list)) for value in values):
        _invalid("group values must be scalar.")
    return [*values, *(_utc_year(_field(row, path)) for path in binding.utc_year_by)]


def calculation_scope(
    operation: str,
    operands: Sequence["CalculationOperand"],
    store: EvidenceStore,
    task_id: str,
    binding: CalculationBinding,
) -> dict[str, object]:
    if binding.conversion is not None:
        if operation != "sum_products":
            _invalid("conversion bindings belong only to sum_products.")
        from general_manager.chat.planned.conversion import conversion_scope

        return conversion_scope(operands, store, task_id, binding)
    if operation == "sum_products":
        _invalid("sum_products requires an explicit conversion binding.")
    records = [store.get(item.evidence_id) for item in operands]
    if not records or any(
        record is None
        or record.task_id != task_id
        or not any(
            store.is_linked_to(task_id, source, record.evidence_id)
            for source in binding.source_requirement_ids
        )
        for record in records
    ):
        _invalid("operands must belong to the declared source requirements.")
    if any(
        not any(
            record is not None
            and store.is_linked_to(task_id, source, record.evidence_id)
            for record in records
        )
        for source in binding.source_requirement_ids
    ):
        _invalid("direct operands must cover every declared source requirement.")
    if binding.value_path is not None:
        if len(binding.source_requirement_ids) != 1 or any(
            record is None
            or record.kind != "query"
            or record.evidence_id != operands[0].evidence_id
            for record in records
        ):
            _invalid("a grouped reduction requires one complete query population.")
        record = records[0]
        assert record is not None
        payload = record.payload()
        rows = payload.get("data") if isinstance(payload, Mapping) else None
        if (
            not isinstance(rows, list)
            or payload.get("complete") is not True
            or payload.get("has_more") is not False
            or type(payload.get("total_count")) is not int
            or payload["total_count"] != len(rows)
        ):
            _invalid("aggregation requires a verified complete query population.")
        if operation == "count":
            if (
                binding.value_path
                or binding.group_by
                or binding.utc_year_by
                or len(operands) != 1
                or operands[0].path != ("data",)
            ):
                _invalid(
                    "count requires the complete root collection without row grouping."
                )
            group = []
            indices = list(range(len(rows)))
        else:
            if (
                operation not in ("sum", "average", "minimum", "maximum")
                or not binding.value_path
            ):
                _invalid("query bindings support only complete group reductions.")
            first_path = operands[0].path
            if (
                len(first_path) != len(binding.value_path) + 2
                or first_path[0] != "data"
                or type(first_path[1]) is not int
                or not 0 <= first_path[1] < len(rows)
            ):
                _invalid(
                    "bound operands must identify row values in the root query population."
                )
            group = group_key(rows[first_path[1]], binding)
            indices = [
                index
                for index, row in enumerate(rows)
                if json.dumps(group_key(row, binding), sort_keys=True)
                == json.dumps(group, sort_keys=True)
            ]
            expected = {("data", index, *binding.value_path) for index in indices}
            actual = [operand.path for operand in operands]
            if len(actual) != len(expected) or set(actual) != expected:
                _invalid(
                    "operands must include every row of exactly one declared group, once."
                )
        units = (
            [_field(rows[index], binding.unit_path) for index in indices]
            if binding.unit_path is not None
            else []
        )
        if units and (
            any(not isinstance(unit, str) or not unit.strip() for unit in units)
            or any(unit != units[0] for unit in units)
        ):
            _invalid("group values have unknown or incompatible explicit units.")
        return {
            "query_evidence_id": record.evidence_id,
            "query_call_identity": record.call_identity,
            "value_path": list(binding.value_path),
            "group_by": [list(path) for path in binding.group_by],
            "utc_year_by": [list(path) for path in binding.utc_year_by],
            "groups": [group],
            "leaf_paths": [list(operand.path) for operand in operands],
            "unit": "records" if operation == "count" else units[0] if units else None,
            "population_complete": True,
        }
    scopes = []
    for record in records:
        assert record is not None
        payload = record.payload()
        if (
            record.kind != "calculation"
            or not isinstance(payload, Mapping)
            or not isinstance(payload.get("scope"), Mapping)
        ):
            _invalid(
                "derived bindings require population-verified predecessor calculations."
            )
        scopes.append(payload["scope"])
    for key in (
        "query_evidence_id",
        "query_call_identity",
        "value_path",
        "group_by",
        "utc_year_by",
        "unit",
        "population_complete",
        *(("conversion",) if any("conversion" in scope for scope in scopes) else ()),
    ):
        if any(scope.get(key) != scopes[0].get(key) for scope in scopes):
            _invalid("derived operands have incompatible populations, fields or units.")
    result = dict(scopes[0])
    # Scope records source membership, while operands preserve arithmetic multiplicity.
    # Shared ancestors must not double their metadata at every derivation depth.
    for field in ("groups", "leaf_paths"):
        result[field] = list(
            {
                json.dumps(item, sort_keys=True): item
                for scope in scopes
                for item in scope[field]
            }.values()
        )
    if operation in ("ratio", "percentage"):
        result["unit"] = "ratio" if operation == "ratio" else "%"
    if operation == "multiply":
        if any(scope.get("unit") is not None for scope in scopes):
            _invalid(
                "numeric multiplication cannot infer unit dimensions; use an explicit conversion."
            )
        result["unit"] = None
    return result


def validate_calculation_binding_evidence(
    task: "PlannedTask",
    requirement_id: str,
    binding: CalculationBinding,
    store: EvidenceStore,
) -> None:
    """Reject inexecutable deferred bindings before their immutable assignment."""
    from general_manager.chat.planned.calculations import (
        CalculationError,
        CalculationOperand,
        calculate,
        calculate_evidence,
    )

    requirement = next(
        req for req in task.requirements if req.requirement_id == requirement_id
    )
    sources = [
        tuple(
            record
            for record in store.for_task(task.task_id)
            if store.is_linked_to(task.task_id, source, record.evidence_id)
        )
        for source in binding.source_requirement_ids
    ]
    if not sources or any(not records for records in sources):
        _invalid("Read concrete evidence for every declared source before binding.")
    if binding.conversion is not None:
        if requirement.operation != "sum_products" or len(sources) != 1:
            _invalid("conversion bindings require one query source and sum_products.")
        errors = []
        for record in sources[0]:
            if record.kind != "query":
                continue
            try:
                payload = record.payload()
                rows = payload.get("data") if isinstance(payload, Mapping) else None
                if not isinstance(rows, list) or not rows:
                    _invalid("conversion requires observed complete rows.")
                conversion_groups: dict[str, list[int]] = {}
                for index, row in enumerate(rows):
                    conversion_groups.setdefault(
                        json.dumps(group_key(row, binding), sort_keys=True), []
                    ).append(index)
                for indices in conversion_groups.values():
                    assert binding.value_path is not None
                    calculate_evidence(
                        "conversion-binding-validation",
                        task.task_id,
                        "sum_products",
                        [
                            CalculationOperand(
                                record.evidence_id, ("data", index, *path)
                            )
                            for index in indices
                            for path in (
                                binding.value_path,
                                binding.conversion.factor_path,
                            )
                        ],
                        store,
                        require_linked=True,
                        binding=binding,
                    )
            except CalculationError as exc:
                errors.append(str(exc))
            else:
                return
        _invalid(
            errors[-1] if errors else "No current compatible conversion query evidence."
        )
    if binding.value_path is None:
        if any(
            not any(record.kind == "calculation" for record in records)
            for records in sources
        ):
            _invalid("Derived bindings require calculation evidence for every source.")
        # Verify all linked predecessors without publishing a new calculation.
        # The sum is only a numeric/provenance probe, not the requested operation;
        # its operand selection/order remains the executor's responsibility.
        operands = [
            CalculationOperand(record.evidence_id, ("value",))
            for record in {
                record.evidence_id: record
                for records in sources
                for record in records
                if record.kind == "calculation"
            }.values()
        ]
        calculate_evidence(
            "binding-validation",
            task.task_id,
            "sum",
            operands,
            store,
            require_linked=True,
            binding=binding,
        )
        return
    if len(sources) != 1:
        _invalid("A root population binding requires exactly one source requirement.")
    errors = []
    for record in sources[0]:
        if record.kind != "query":
            continue
        try:
            payload = record.payload()
            rows = payload.get("data") if isinstance(payload, Mapping) else None
            if not isinstance(rows, list):
                _invalid("Binding requires a concrete root query population.")
            if requirement.operation == "count":
                calculation_scope(
                    "count",
                    [CalculationOperand(record.evidence_id, ("data",))],
                    store,
                    task.task_id,
                    binding,
                )
                return
            if not rows:
                _invalid("A scalar reduction binding requires observed row values.")
            for row in rows:
                value = row
                for part in binding.value_path:
                    if isinstance(value, list):
                        _invalid(
                            "Collection traversal is not a scalar binding; query the collection's source manager as the root population before binding."
                        )
                    value = _field(value, (part,))
                if isinstance(value, (list, Mapping)):
                    _invalid("A value binding must select a scalar, not a collection.")
                calculate("sum", [value])
            groups: dict[str, list[int]] = {}
            for index, row in enumerate(rows):
                groups.setdefault(
                    json.dumps(group_key(row, binding), sort_keys=True), []
                ).append(index)
            for indices in groups.values():
                calculation_scope(
                    str(requirement.operation),
                    [
                        CalculationOperand(
                            record.evidence_id, ("data", index, *binding.value_path)
                        )
                        for index in indices
                    ],
                    store,
                    task.task_id,
                    binding,
                )
        except CalculationError as exc:
            errors.append(str(exc))
        else:
            return
    _invalid(
        errors[-1]
        if errors
        else "No compatible query evidence exists for this binding."
    )
