"""Prove rowwise conversion against exact current public schema/query witnesses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from decimal import Decimal
import json
import re
from typing import Any, NoReturn, TYPE_CHECKING

from general_manager.chat.graphql_contract import capture_manager_contract
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    canonical_call_identity,
)
from general_manager.chat.planned.models import CalculationBinding
from general_manager.interface.unit_contract import FieldUnitContract

if TYPE_CHECKING:
    from general_manager.chat.planned.calculations import CalculationOperand


def _fail(message: str) -> NoReturn:
    from general_manager.chat.planned.calculations import CalculationError

    raise CalculationError(message)


def _identity(record: EvidenceRecord, name: str) -> dict[str, Any]:
    try:
        value = json.loads(record.call_identity)
    except (ValueError, TypeError):
        _fail("conversion sources require a canonical tool call.")
    if (
        not isinstance(value, dict)
        or set(value) != {"name", "args"}
        or value["name"] != name
        or not isinstance(value["args"], dict)
        or canonical_call_identity(name, value["args"]) != record.call_identity
        or record.provenance.get("tool") != name
        or record.provenance.get("manager") != value["args"].get("manager")
    ):
        _fail("conversion source call/provenance does not match.")
    return value["args"]


def _source_hash(record: EvidenceRecord) -> str:
    value = {
        "evidence_id": record.evidence_id,
        "task_id": record.task_id,
        "kind": record.kind,
        "call_identity": record.call_identity,
        "provenance": dict(record.provenance),
        "payload_json": record.payload_json,
    }
    return sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _selected(fields: object, path: tuple[str, ...]) -> bool:
    if not path or not isinstance(fields, list):
        return False
    for selection in fields:
        if isinstance(selection, str) and selection == path[0] and len(path) == 1:
            return True
        if isinstance(selection, Mapping):
            if "field" in selection:
                name, children = selection["field"], selection.get("fields")
            elif len(selection) == 1:
                name, children = next(iter(selection.items()))
            else:
                continue
            if name == path[0] and len(path) > 1 and _selected(children, path[1:]):
                return True
    return False


def _schemas(
    store: EvidenceStore, task_id: str, binding: CalculationBinding
) -> dict[str, dict[str, Any]]:
    assert binding.conversion is not None
    result = {}
    for requirement, evidence_id in zip(
        binding.conversion.schema_requirement_ids,
        binding.conversion.schema_evidence_ids,
        strict=True,
    ):
        record = store.get(evidence_id)
        if (
            record is None
            or record.task_id != task_id
            or record.kind != "schema"
            or not store.is_linked_to(task_id, requirement, evidence_id)
            or not store.schema_current(record)
        ):
            _fail("conversion requires task-linked current schema evidence.")
        args = _identity(record, "get_manager_schema")
        payload = record.payload()
        manager = args.get("manager")
        if (
            not isinstance(manager, str)
            or manager in result
            or not isinstance(payload, dict)
            or payload.get("manager") != manager
            or payload.get("schema_view") != args.get("view", "overview")
            or payload.get("contract_version") != 2
        ):
            _fail("conversion schema selector/manager scope is invalid.")
        try:
            full, snapshot = capture_manager_contract(manager)
        except (ValueError, TypeError) as exc:
            _fail(f"conversion schema declaration is invalid: {type(exc).__name__}.")
        if full is None or payload.get("snapshot") != snapshot:
            _fail("conversion schema/exposure snapshot has changed.")
        result[manager] = {
            "record": record,
            "payload": payload,
            "full": full,
            "snapshot": snapshot,
        }
    # A later capture/default hook cannot silently invalidate an earlier witness.
    for manager, context in result.items():
        if capture_manager_contract(manager)[1] != context["snapshot"]:
            _fail("conversion schema snapshots are unstable.")
    return result


def _field(
    schemas: dict[str, dict[str, Any]], manager: str, name: str
) -> dict[str, Any]:
    context = schemas.get(manager)
    if context is None:
        _fail("each conversion manager needs its own allowed schema witness.")
    full, payload = context["full"], context["payload"]
    definition = full["types"][full["type"]]["fields"].get(name)
    observed = (
        payload.get("output_fields", {})
        if payload["schema_view"] == "overview"
        else payload.get("types", {}).get(full["type"], {}).get("fields", {})
    )
    if (
        not isinstance(definition, dict)
        or not isinstance(observed, Mapping)
        or observed.get(name) != definition
    ):
        _fail("conversion field definition was not observed exactly in its schema.")
    return definition


def _value(row: object, path: tuple[str, ...]) -> Any:
    value = row
    for part in path:
        if not isinstance(value, Mapping) or part not in value:
            _fail("conversion path is missing or traverses a collection.")
        value = value[part]
    return value


def _record_id(value: object, signature: str) -> str:
    types = {"Int": (int,), "String": (str,), "ID": (str, int)}
    if (
        type(value) not in types.get(signature.removesuffix("!"), ())
        or not str(value).strip()
    ):
        _fail("conversion needs observed nonempty GraphQL record IDs.")
    return str(value)


def _identifier(schemas: dict[str, dict[str, Any]], manager: str) -> dict[str, Any]:
    definition = _field(schemas, manager, "id")
    marker = definition.get("identity_contract")
    if (
        not isinstance(marker, Mapping)
        or marker.get("source") != "interface_input"
        or marker.get("input_name") != "id"
        or marker.get("input_type") not in ("int", "str")
        or re.sub(r"[!]", "", definition["type"])
        not in ({"Int", "ID"} if marker["input_type"] == "int" else {"String", "ID"})
    ):
        _fail(
            "conversion identifiers require the explicit shared interface constructor contract."
        )
    return definition


def conversion_scope(
    operands: Sequence[CalculationOperand],
    store: EvidenceStore,
    task_id: str,
    binding: CalculationBinding,
) -> dict[str, object]:
    """No scalar broadcasting: each exact quantity/factor pair comes from one row."""
    from general_manager.chat.planned.calculation_scope import group_key
    from general_manager.chat.planned.calculations import _decimal

    conversion = binding.conversion
    if (
        conversion is None
        or len(binding.source_requirement_ids) != 1
        or binding.value_path is None
        or len(binding.value_path) != 1
        or binding.unit_path is None
        or len(binding.unit_path) != 1
        or not operands
    ):
        _fail(
            "conversion requires one root quantity/unit population and schema binding."
        )
    record = store.get(operands[0].evidence_id)
    if (
        record is None
        or record.task_id != task_id
        or record.kind != "query"
        or not store.is_linked_to(
            task_id, binding.source_requirement_ids[0], record.evidence_id
        )
        or any(item.evidence_id != record.evidence_id for item in operands)
    ):
        _fail(
            "all conversion operands require the same declared complete query source."
        )
    args = _identity(record, "query")
    manager = args.get("manager")
    if not isinstance(manager, str):
        _fail("conversion query manager is missing.")
    payload = record.payload()
    rows = payload.get("data") if isinstance(payload, Mapping) else None
    if (
        not isinstance(rows, list)
        or not rows
        or payload.get("complete") is not True
        or payload.get("has_more") is not False
        or type(payload.get("total_count")) is not int
        or payload["total_count"] != len(rows)
    ):
        _fail("conversion requires every row of a verified complete population.")
    paths = (
        binding.value_path,
        binding.unit_path,
        conversion.factor_path,
        conversion.factor_identity_path,
        ("id",),
        *binding.group_by,
        *binding.utc_year_by,
    )
    if any(not _selected(args.get("fields"), path) for path in paths):
        _fail(
            "every conversion value/unit/group/identity path must be observed in the original query."
        )
    schemas = _schemas(store, task_id, binding)
    quantity = _field(schemas, manager, binding.value_path[0]).get("unit_contract")
    if (
        not isinstance(quantity, Mapping)
        or quantity.get("kind") != "quantity"
        or quantity.get("unit_field") != binding.unit_path[0]
    ):
        _fail("quantity units need a declared numeric-field/sibling-unit contract.")
    _field(schemas, manager, binding.unit_path[0])
    quantity_identity = _identifier(schemas, manager)
    used = {manager}
    factor_manager = manager
    for part in conversion.factor_path[:-1]:
        signature = _field(schemas, factor_manager, part)["type"]
        if "[" in signature:
            _fail("conversion relation paths cannot traverse collections.")
        definition = schemas[factor_manager]["full"]["types"].get(
            signature.removesuffix("!"), {}
        )
        if definition.get("kind") != "reference":
            _fail("conversion factor origins require declared manager relations.")
        factor_manager = definition["manager"]
        used.add(factor_manager)
    if used != set(schemas):
        _fail("conversion schema witnesses must cover exactly the actual manager path.")
    if conversion.factor_identity_path != (*conversion.factor_path[:-1], "id"):
        _fail("factor identity must belong to the same observed factor object.")
    factor_identity = _identifier(schemas, factor_manager)
    factor = _field(schemas, factor_manager, conversion.factor_path[-1]).get(
        "unit_contract"
    )
    if not isinstance(factor, Mapping) or factor.get("kind") != "factor":
        _fail("factor values need a trusted declared source/target dimension contract.")
    quantity = FieldUnitContract.from_mapping(quantity).as_mapping()
    factor = FieldUnitContract.from_mapping(factor).as_mapping()
    ids = [_record_id(_value(row, ("id",)), quantity_identity["type"]) for row in rows]
    if len(set(ids)) != len(ids):
        _fail("conversion population IDs must be unique.")
    origins = []
    origin_values: dict[str, Decimal] = {}
    for row in rows:
        unit = _value(row, binding.unit_path)
        term = quantity["units"].get(unit) if isinstance(unit, str) else None
        if term != factor["source"]:
            _fail(
                "raw quantity units are absent or incompatible with the trusted factor source dimension."
            )
        _decimal(_value(row, binding.value_path))
        factor_value = _decimal(_value(row, conversion.factor_path))
        origin = _record_id(
            _value(row, conversion.factor_identity_path), factor_identity["type"]
        )
        if origin in origin_values and origin_values[origin] != factor_value:
            _fail("the same factor origin cannot carry inconsistent observed values.")
        origin_values[origin] = factor_value
        origins.append(origin)
    first = operands[0].path
    if (
        len(first) != len(binding.value_path) + 2
        or first[0] != "data"
        or type(first[1]) is not int
        or not 0 <= first[1] < len(rows)
    ):
        _fail("conversion operands must identify original quantity rows.")
    group = group_key(rows[first[1]], binding)
    indices = [
        index for index, row in enumerate(rows) if group_key(row, binding) == group
    ]
    expected = [
        ("data", index, *path)
        for index in indices
        for path in (binding.value_path, conversion.factor_path)
    ]
    if [item.path for item in operands] != expected:
        _fail(
            "conversion requires every row of exactly one group, as quantity/factor pairs once in original order."
        )
    return {
        "query_evidence_id": record.evidence_id,
        "query_call_identity": record.call_identity,
        "value_path": list(binding.value_path),
        "group_by": [list(path) for path in binding.group_by],
        "utc_year_by": [list(path) for path in binding.utc_year_by],
        "groups": [group],
        "leaf_paths": [list(item.path) for item in operands],
        "unit": factor["target"]["unit"],
        "population_complete": True,
        "conversion": {
            "binding": conversion.as_mapping(),
            "quantity_contract": quantity,
            "factor_contract": factor,
            "population_source_sha256": _source_hash(record),
            "population_ids": ids,
            "factor_origin_ids": origins,
            "schema_sources": [
                {
                    "evidence_id": context["record"].evidence_id,
                    "source_sha256": _source_hash(context["record"]),
                    "manager": name,
                    "snapshot": context["snapshot"],
                }
                for name, context in schemas.items()
            ],
        },
    }
