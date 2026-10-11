"""Verify a difference of two direct sums over independent complete queries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
import json
import re
from typing import Any, TYPE_CHECKING, cast

from graphql import Undefined

from general_manager.chat.graphql_contract import (
    capture_manager_contract,
    runtime_schema,
    argument_name,
)
from general_manager.chat.planned.conversion import (
    _fail,
    _identity,
    _schemas,
    _field as _schema_field,
    _identifier as _schema_identifier,
    _selected,
    _value,
    _source_hash,
)
from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import (
    CalculationBinding,
)
from general_manager.interface.unit_contract import FieldUnitContract

if TYPE_CHECKING:
    from general_manager.chat.planned.calculations import CalculationOperand


def _field(schemas: dict[str, Any], manager: str, name: str) -> dict[str, Any]:
    definition = _schema_field(schemas, manager, name)
    if definition.get("value_contract") != {
        "source": "interface_attribute",
        "field": name,
        "transform": "identity",
    }:
        _fail("cross-query output paths require their common executable source getter.")
    return definition


def _identifier(schemas: dict[str, Any], manager: str) -> dict[str, Any]:
    _field(schemas, manager, "id")
    return _schema_identifier(schemas, manager)


def capture_query_schema_witness(manager: object) -> dict[str, str]:
    """Capture source and exposed singular relation contracts without backend reads."""
    if not isinstance(manager, str):
        return {}
    pending, result = [manager], {}
    try:
        while pending:
            current = pending.pop()
            if current in result:
                continue
            full, snapshot = capture_manager_contract(current)
            if full is None or not snapshot:
                return {}
            result[current] = snapshot
            for field in full["types"][full["type"]]["fields"].values():
                if "[" in field["type"]:
                    continue
                definition = full["types"].get(field["type"].removesuffix("!"), {})
                if definition.get("kind") == "reference":
                    pending.append(definition["manager"])
        if any(
            capture_manager_contract(name)[1] != snapshot
            for name, snapshot in result.items()
        ):
            return {}
    except (ValueError, TypeError):
        return {}
    return result


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        _fail(
            "cross-query periods require observed ISO dates or timezone-qualified timestamps."
        )
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return datetime.combine(
                date.fromisoformat(value), datetime.min.time(), timezone.utc
            )
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None or "T" not in value:
            _fail("cross-query timestamps require an explicit timezone.")
        return timestamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        _fail("invalid cross-query ISO period.")


def _typed_id(value: object, definition: Mapping[str, Any]) -> dict[str, object]:
    """Restore constructor types only through the attested scalar ID transport."""
    kind = definition["identity_contract"]["input_type"]
    if definition["type"].removesuffix("!") == "ID":
        if kind == "int" and type(value) is str:
            try:
                native = int(value)
                # The ID serializer emits canonical decimal strings for native
                # integers. Never normalize padded IDs or business strings.
                if str(native) == value:
                    value = native
            except ValueError:
                pass  # The unchanged string fails the explicit type check below.
        elif kind == "str" and type(value) is int:
            # GraphQL's ID input codec accepts integers as their exact text.
            value = str(value)
    if type(value) is not {"int": int, "str": str}[kind] or (
        isinstance(value, str) and not value.strip()
    ):
        _fail("cross-query identities must retain the explicit constructor type.")
    return {"type": kind, "value": value}


def _entity_manager(
    schemas: dict[str, Any], manager: str, path: tuple[str, ...]
) -> tuple[str, set[str]]:
    used = {manager}
    if not path or path[-1] != "id":
        _fail("cross-query entity selectors must end at an explicit constructor ID.")
    for part in path[:-1]:
        definition = _field(schemas, manager, part)
        if "[" in definition["type"]:
            _fail("cross-query entity paths cannot traverse collections.")
        target = schemas[manager]["full"]["types"].get(
            definition["type"].removesuffix("!"), {}
        )
        if target.get("kind") != "reference":
            _fail(
                "cross-query entity paths require exposed singular manager relations."
            )
        manager = target["manager"]
        used.add(manager)
    _identifier(schemas, manager)
    return manager, used


def _observed_input(context: Mapping[str, Any], type_name: str) -> Mapping[str, Any]:
    full = context["full"]["types"].get(type_name)
    observations = context.get("observations", [context["payload"]])
    if (
        not isinstance(full, Mapping)
        or full.get("kind") != "input"
        or not any(
            payload.get("types", {}).get(type_name) == full for payload in observations
        )
    ):
        _fail(
            "cross-query filter definitions require exact observed input schema details."
        )
    return cast(Mapping[str, Any], full["fields"])


def _predicates(
    context: Mapping[str, Any],
    type_name: str,
    values: object,
    schemas: dict[str, Any],
    manager: str,
    prefix: tuple[str, ...] = (),
    field_prefix: tuple[str, ...] = (),
) -> dict[tuple[str, ...], tuple[tuple[str, ...], str, object]]:
    if not isinstance(values, Mapping):
        _fail("cross-query filters require a generated input object.")
    definitions = _observed_input(context, type_name)
    result = {}
    # Effective defaults are part of the population, even when omitted in a call.
    effective = {
        name: definition["default"]
        for name, definition in definitions.items()
        if "default" in definition
    }
    effective.update(values)
    for name, value in effective.items():
        definition = definitions.get(name)
        marker = (
            definition.get("filter_contract")
            if isinstance(definition, Mapping)
            else None
        )
        if (
            not isinstance(definition, Mapping)
            or not isinstance(marker, Mapping)
            or marker.get("source") != "interface_filter"
        ):
            _fail(
                "cross-query predicates require generated executable filter authority."
            )
        path = (*prefix, name)
        output_path = (*field_prefix, marker["field"])
        if marker["operator"] == "relation":
            output = _field(schemas, manager, marker["field"])
            target = schemas[manager]["full"]["types"].get(
                output["type"].removesuffix("!"), {}
            )
            if (
                "[" in output["type"]
                or target.get("kind") != "reference"
                or target.get("manager") != marker.get("manager")
                or marker.get("manager") not in schemas
            ):
                _fail(
                    "cross-query filter relation authority must match the exact exposed output manager namespace."
                )
            result.update(
                _predicates(
                    context,
                    definition["type"].removesuffix("!"),
                    value,
                    schemas,
                    marker["manager"],
                    path,
                    output_path,
                )
            )
        else:
            result[path] = (output_path, marker["operator"], value)
    return result


def _root_filters(
    context: Mapping[str, Any], args: Mapping[str, Any], schemas: dict[str, Any]
) -> dict[tuple[str, ...], tuple[tuple[str, ...], str, object]]:
    if (
        set(args)
        - {"manager", "filters", "fields", "root", "arguments", "limit", "offset"}
        or args.get("offset", 0) != 0
    ):
        _fail("cross-query source calls cannot carry unproved population selectors.")
    roots = context["full"]["roots"]
    root_name = args.get("root") or (roots[0] if len(roots) == 1 else None)
    schema = runtime_schema()
    if root_name not in roots or schema.query_type is None:
        _fail("cross-query root is not an exact advertised query root.")
    population = context["full"]["root_fields"][root_name].get("population_contract")
    if population != {
        "source": "interface_list",
        "manager": args.get("manager"),
        "filter_source": "interface_filter",
    }:
        _fail(
            "cross-query root population requires the common executable list resolver authority."
        )
    field = schema.query_type.fields[root_name]
    filter_name = argument_name(field, "filter")
    arguments = args.get("arguments", {})
    if not isinstance(arguments, Mapping) or filter_name is None:
        _fail("cross-query roots require generated filter arguments.")
    for name, argument in field.args.items():
        role = argument.out_name or name
        value = arguments.get(name, argument.default_value)
        if role == "page":
            if (
                value is not Undefined
                and value is not None
                and (type(value) is not int or value != 1)
            ):
                _fail("cross-query populations require the first complete page.")
        elif role == "page_size":
            if (
                value is not Undefined
                and value is not None
                and (type(value) is not int or value < 1)
            ):
                _fail("invalid cross-query page size.")
        elif role == "filter":
            if name in arguments or (
                value is not Undefined and value not in (None, {})
            ):
                _fail(
                    "cross-query filter arguments/defaults cannot narrow the population implicitly."
                )
        elif name in arguments or (value is not Undefined and value is not None):
            _fail("cross-query source roots contain an unproved argument or default.")
    if set(arguments) - set(field.args):
        _fail("cross-query source root arguments are unrecognized.")
    return _predicates(
        context,
        str(field.args[filter_name].type).removesuffix("!"),
        args.get("filters"),
        schemas,
        args["manager"],
    )


def _unit(
    schemas: dict[str, Any], manager: str, binding: CalculationBinding, rows: list[Any]
) -> dict[str, Any]:
    assert binding.value_path is not None
    declaration = _field(schemas, manager, binding.value_path[0]).get("unit_contract")
    try:
        declaration = FieldUnitContract.from_mapping(declaration).as_mapping()
    except ValueError:
        _fail("cross-query sums require an explicit public quantity unit contract.")
    if declaration["kind"] == "fixed_quantity":
        if binding.unit_path is not None:
            _fail("fixed quantity sums cannot substitute an unrelated row unit.")
        return {
            "label": declaration["label"],
            "term": declaration["term"],
            "contract": declaration,
        }
    if declaration["kind"] != "quantity" or binding.unit_path != (
        declaration["unit_field"],
    ):
        _fail(
            "cross-query dynamic units must match the actual declared sibling unit field."
        )
    _field(schemas, manager, declaration["unit_field"])
    labels = [_value(row, binding.unit_path) for row in rows]
    if any(
        not isinstance(label, str) or label not in declaration["units"]
        for label in labels
    ) or any(label != labels[0] for label in labels):
        _fail(
            "cross-query units must be declared and identical throughout each source."
        )
    return {
        "label": labels[0],
        "term": declaration["units"][labels[0]],
        "contract": declaration,
    }


def cross_query_scope(
    operation: str,
    operands: Sequence[CalculationOperand],
    store: EvidenceStore,
    task_id: str,
    binding: CalculationBinding,
) -> dict[str, object]:
    """Preserve each independent population; never relax legacy query equality."""
    from general_manager.chat.planned.calculation_scope import calculation_scope
    from general_manager.chat.planned.calculations import (
        _OperandResolver,
        _decimal,
    )

    cross = binding.cross_query
    if (
        cross is None
        or operation != "difference"
        or len(operands) != 2
        or [(item.evidence_id, item.path) for item in operands]
        != [(source.calculation_evidence_id, ("value",)) for source in cross.sources]
    ):
        _fail(
            "cross-query difference requires the exact two bound predecessor values in order."
        )
    schemas = _schemas(store, task_id, binding)
    sources, entities, windows, units, used = [], [], [], [], set()
    resolver = _OperandResolver(store, task_id, True)
    for selector, operand in zip(cross.sources, operands, strict=True):
        record = store.get(selector.calculation_evidence_id)
        if (
            record is None
            or record.task_id != task_id
            or record.kind != "calculation"
            or not store.is_linked_to(
                task_id, selector.source_requirement_id, record.evidence_id
            )
        ):
            _fail(
                "cross-query predecessors must match their exact current-task requirement links."
            )
        predecessor_operation, leaves, predecessor = resolver._derivation(record)
        if (
            predecessor_operation != "sum"
            or predecessor is None
            or predecessor.cross_query is not None
            or predecessor.conversion is not None
            or predecessor.group_by
            or predecessor.utc_year_by
            or predecessor.value_path is None
            or len(predecessor.value_path) != 1
            or len(predecessor.source_requirement_ids) != 1
        ):
            _fail(
                "cross-query predecessors must be direct complete ungrouped scalar quantity sums."
            )
        resolver.resolve(operand)
        original_scope = calculation_scope("sum", leaves, store, task_id, predecessor)
        query_id = original_scope.get("query_evidence_id")
        if not isinstance(query_id, str):
            _fail("cross-query sum population has no exact query identity.")
        query = store.get(query_id)
        if (
            query is None
            or query.task_id != task_id
            or query.kind != "query"
            or not store.is_linked_to(
                task_id, predecessor.source_requirement_ids[0], query.evidence_id
            )
        ):
            _fail("cross-query sums need their exact linked root query populations.")
        args = _identity(query, "query")
        manager = args.get("manager")
        if not isinstance(manager, str) or manager not in schemas:
            _fail("cross-query source manager needs its own exact schema witness.")
        payload = query.payload()
        rows = payload.get("data") if isinstance(payload, Mapping) else None
        if (
            not isinstance(rows, list)
            or not rows
            or set(payload) != {"data", "total_count", "has_more", "complete"}
            or payload.get("complete") is not True
            or payload.get("has_more") is not False
            or type(payload.get("total_count")) is not int
            or payload["total_count"] != len(rows)
        ):
            _fail("cross-query sums require nonempty complete root query populations.")
        required_paths = [
            ("id",),
            predecessor.value_path,
            selector.entity_path,
            selector.period_path,
            *((predecessor.unit_path,) if predecessor.unit_path else ()),
        ]
        if any(not _selected(args.get("fields"), path) for path in required_paths):
            _fail(
                "cross-query value/entity/period/unit/identity fields must be selected in the original query."
            )
        root_identity = _identifier(schemas, manager)
        root_ids = [_typed_id(_value(row, ("id",)), root_identity) for row in rows]
        if len({json.dumps(identity, sort_keys=True) for identity in root_ids}) != len(
            rows
        ):
            _fail("cross-query root record identities must be unique.")
        for row in rows:
            _decimal(_value(row, predecessor.value_path))
        entity_manager, path_managers = _entity_manager(
            schemas, manager, selector.entity_path
        )
        used.update(path_managers)
        if len(selector.period_path) != 1 or _field(
            schemas, manager, selector.period_path[0]
        )["type"].removesuffix("!") not in ("Date", "DateTime"):
            _fail(
                "cross-query periods require an observed date field on the root population."
            )
        predicates = _root_filters(schemas[manager], args, schemas)
        paths = (
            selector.entity_filter_path,
            selector.period_start_filter_path,
            selector.period_end_filter_path,
        )
        if set(predicates) != set(paths) or len(set(paths)) != 3:
            _fail(
                "cross-query predicates must contain exactly entity equality and gte/lt period bounds."
            )
        entity_predicate, lower, upper = (predicates[path] for path in paths)
        if (
            entity_predicate[:2] != (selector.entity_path, "exact")
            or lower[:2] != (selector.period_path, "gte")
            or upper[:2] != (selector.period_path, "lt")
        ):
            _fail(
                "cross-query selectors do not match executable entity/period filter meanings."
            )
        expected_entity = _typed_id(
            entity_predicate[2], _identifier(schemas, entity_manager)
        )
        start, end = _timestamp(lower[2]), _timestamp(upper[2])
        if start >= end:
            _fail("cross-query period must be a nonempty UTC half-open interval.")
        if any(
            _typed_id(
                _value(row, selector.entity_path), _identifier(schemas, entity_manager)
            )
            != expected_entity
            or not start <= _timestamp(_value(row, selector.period_path)) < end
            for row in rows
        ):
            _fail(
                "cross-query contributing rows must satisfy their exact entity and period predicates."
            )
        try:
            witness = json.loads(query.provenance.get("query_schema_witness", ""))
        except (TypeError, ValueError):
            _fail("cross-query query-time schema witness is absent.")
        current = capture_query_schema_witness(manager)
        if (
            not isinstance(witness, dict)
            or set(witness) != {"version", "before", "after", "stable"}
            or type(witness["version"]) is not int
            or witness["version"] != 1
            or witness["stable"] is not True
            or not current
            or witness["before"] != witness["after"]
            or witness["before"] != current
            or any(
                current.get(name) != schemas[name]["snapshot"] for name in path_managers
            )
        ):
            _fail(
                "cross-query original query-time schema witness is stale or unstable."
            )
        unit = _unit(schemas, manager, predecessor, rows)
        entities.append({"manager": entity_manager, "identity": expected_entity})
        windows.append(
            {"start": start.isoformat(), "end": end.isoformat(), "bounds": "gte/lt"}
        )
        units.append(unit)
        sources.append(
            {
                "calculation_evidence_id": record.evidence_id,
                "source_requirement_id": selector.source_requirement_id,
                "calculation_source_sha256": _source_hash(record),
                "query_evidence_id": query.evidence_id,
                "query_call_identity": query.call_identity,
                "query_source_sha256": _source_hash(query),
                "manager": manager,
                "value_path": list(predecessor.value_path),
                "unit_path": list(predecessor.unit_path)
                if predecessor.unit_path is not None
                else None,
                "original_scope": original_scope,
                "leaf_paths": original_scope["leaf_paths"],
                "selectors": selector.as_mapping(),
                "query_selectors": args,
                "query_schema_witness": witness,
                "population_ids": root_ids,
                "resolved_unit": unit,
            }
        )
    if (
        used != set(schemas)
        or entities[0] != entities[1]
        or windows[0] != windows[1]
        or units[0]["term"] != units[1]["term"]
        or units[0]["label"] != units[1]["label"]
    ):
        _fail(
            "cross-query sources need the same entity namespace, UTC period and exact declared unit, with exactly the required schemas."
        )
    if sources[0]["query_evidence_id"] == sources[1]["query_evidence_id"]:
        _fail("cross-query mode requires two distinct independent root populations.")
    return {
        "population_complete": True,
        "unit": units[0]["label"],
        "cross_query": {
            "version": 1,
            "binding": cross.as_mapping(),
            "entity": entities[0],
            "period": windows[0],
            "resolved_unit": {"label": units[0]["label"], "term": units[0]["term"]},
            "sources": sources,
            "schema_sources": [
                {
                    "manager": name,
                    "evidence_id": record.evidence_id,
                    "snapshot": context["snapshot"],
                    "source_sha256": _source_hash(record),
                }
                for name, context in schemas.items()
                for record in context["records"]
            ],
        },
    }
