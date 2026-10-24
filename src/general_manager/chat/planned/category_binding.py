"""Bind semantic category filters to values from the exposed read interface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
import re
from typing import Any, NoReturn
from graphql import Undefined

from general_manager.chat.graphql_contract import (
    argument_name,
    capture_manager_contract,
    runtime_schema,
)
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    canonical_call_identity,
)


INSTRUCTION = (
    "Natural-language categories require actual exposed values. Inspect the manager "
    "schema, then query its unfiltered category field and useful identity/name fields "
    "with category_lookup={schema_evidence_id:...}. Gather a complete population; "
    "a partial lookup cannot establish the category vocabulary or absence. Names "
    "and labels are not family/enum values. Select exact observed category values "
    "and pass category_bindings on the business query: each entry supplies "
    "schema_evidence_id, query_evidence_id of the complete lookup, the observed "
    "direct scalar field, and filter_path to an exact/in category scalar or scalar list "
    "inside filters or arguments. The runtime validates same-manager/root, current "
    "schema/exposure, observed executable filter_contract, completeness and exact typed membership. "
    "schema_evidence_not_linked means the supplied observation does not satisfy a declared "
    "schema requirement; use a compatible linked inspection, not the same unlinked detail again. "
    "A linked overview can establish a category lookup. Business category bindings also require "
    "observed output and filter input definitions; an explicitly requested full inspection can "
    "satisfy an overview requirement without changing its scope. invalid_filter_path means the "
    "path must start at filters or the actual arguments/filter name, not a bare field. "
    "The root must declare its common interface_list population_contract for this manager. "
    "Inspect the native filter input definitions at the same snapshot; use their "
    "declared field/operator meanings, never guessed suffixes. Re-query after "
    "schema changes. Never infer global absence from an unverified category label. "
    "If the exposed vocabulary does not resolve the requested category, report the "
    "unresolved meaning rather than inventing a mapping. Exact user-supplied scalar "
    "identifiers may use ordinary literal filters. "
    "category_lookups is operational feedback bound to actual query/schema evidence. "
    "gather_complete_lookup means the existing population is partial; obtain all rows. "
    "bind_business_query means that lookup is complete: select the relevant observed "
    "category values from its query_evidence_id, inspect any missing output/filter "
    "definitions at its snapshot, and issue the business query with category_bindings. "
    "schema_evidence_ids lists current linked inspections of that same manager/snapshot; "
    "choose one with the necessary observed definitions, or request compatible details. "
    "Repeating the same complete lookup does not execute the requested category query. "
    "After a verified bound query, business_query_evidence_ids points to its actual results. "
    "gather_complete_business_query means those results still require missing rows/pages. "
    "check_requirements_and_complete means inspect all remaining task requirements, "
    "gather missing evidence or calculations, and complete only when all are supported. "
    "This feedback grants no new evidence authority, category meaning or filter permission. "
    "Group independent overview/schema/path/query reads in one provider round after "
    "their exact observed names, snapshot and identity prerequisites are available; "
    "tool calls remain ordered and may not depend on results still missing from that round. "
)

_SCHEMA_ID = {"type": "string", "minLength": 1}
_BACKEND_QUERY_KEYS = frozenset(
    {"manager", "filters", "fields", "root", "arguments", "limit", "offset"}
)
QUERY_PROPERTIES: dict[str, Any] = {
    "category_lookup": {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_evidence_id"],
        "properties": {"schema_evidence_id": _SCHEMA_ID},
        "description": "Read actual category values using this task's current exposed schema. Use an unfiltered root lookup; complete all rows before binding.",
    },
    "category_bindings": {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_evidence_id",
                "query_evidence_id",
                "field",
                "filter_path",
            ],
            "properties": {
                "schema_evidence_id": _SCHEMA_ID,
                "query_evidence_id": _SCHEMA_ID,
                "field": {"type": "string", "minLength": 1},
                "filter_path": {
                    "type": "array",
                    "minItems": 2,
                    "items": {"type": "string", "minLength": 1},
                },
            },
        },
        "description": "Validate exact/in category membership against actual values in a complete unfiltered same-root query and current output/input schema definitions. filter_path addresses the advertised executable native input field, whose filter_contract must declare the observed field and exact/in operator. No guessed aliases or name-to-family inference.",
    },
}


class CategoryBindingError(ValueError):
    """A declared category lookup or binding lacks the required public evidence."""

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason
        super().__init__("category binding requires complete current public values")


def _reject(reason: str | None = None) -> NoReturn:
    raise CategoryBindingError(reason)


def _source(
    store: EvidenceStore, task_id: str, evidence_id: object, kind: str
) -> EvidenceRecord:
    record = store.get(evidence_id) if isinstance(evidence_id, str) else None
    if record is None or record.task_id != task_id or record.kind != kind:
        _reject()
    assert record is not None
    if not store.is_linked(task_id, record.evidence_id):
        _reject("schema_evidence_not_linked" if kind == "schema" else None)
    return record


def _complete_rows(payload: object) -> list[Any] | None:
    """Use the same complete-population test for binding and operational feedback."""
    if not isinstance(payload, Mapping):
        return None
    rows = payload.get("data")
    if (
        not isinstance(rows, list)
        or payload.get("complete") is not True
        or payload.get("has_more") is not False
        or type(payload.get("total_count")) is not int
        or payload["total_count"] != len(rows)
        or any(not isinstance(row, Mapping) for row in rows)
    ):
        return None
    return rows


def _identity(record: EvidenceRecord, tool: str, manager: str) -> dict[str, Any]:
    try:
        call = json.loads(record.call_identity)
        args = call["args"]
        if (
            call["name"] != tool
            or args.get("manager") != manager
            or record.provenance.get("tool") != tool
            or record.provenance.get("manager") != manager
            or canonical_call_identity(tool, args) != record.call_identity
        ):
            _reject()
    except (KeyError, TypeError, ValueError):
        _reject()
    return dict(args)


def _schema(
    store: EvidenceStore, task_id: str, evidence_id: object, manager: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = _source(store, task_id, evidence_id, "schema")
    args = _identity(record, "get_manager_schema", manager)
    payload = record.payload()
    full, digest = capture_manager_contract(manager)
    if (
        full is None
        or not store.schema_current(record)
        or not isinstance(payload, dict)
        or payload.get("manager") != manager
        or payload.get("snapshot") != digest
        or payload.get("schema_view") != args.get("view", "overview")
    ):
        _reject()
    assert full is not None
    return full, payload


def _root(args: Mapping[str, Any], full: Mapping[str, Any]) -> str:
    roots = full["roots"]
    root = args.get("root", roots[0] if len(roots) == 1 else None)
    if not isinstance(root, str) or root not in roots:
        _reject()
    assert isinstance(root, str)
    if full["root_fields"][root].get("population_contract") != {
        "source": "interface_list",
        "manager": full["manager"],
        "filter_source": "interface_filter",
    }:
        _reject()
    return root


def _unfiltered(args: Mapping[str, Any], full: Mapping[str, Any]) -> str:
    if (
        set(args) - _BACKEND_QUERY_KEYS
        or type(args.get("offset", 0)) is not int
        or args.get("offset", 0) != 0
    ):
        _reject()
    root = _root(args, full)
    query_type = runtime_schema().query_type
    if query_type is None:
        _reject()
    field = query_type.fields[root]
    filter_name = argument_name(field, "filter")
    arguments = args.get("arguments", {})
    if (
        args.get("filters")
        or not isinstance(arguments, Mapping)
        or filter_name in arguments
        or set(arguments) - set(field.args)
    ):
        _reject()
    for name, argument in field.args.items():
        role = argument.out_name or name
        value = arguments.get(name, argument.default_value)
        if role == "page":
            if (
                value is not Undefined
                and value is not None
                and (type(value) is not int or value != 1)
            ):
                _reject()
        elif role == "page_size":
            if (
                value is not Undefined
                and value is not None
                and (type(value) is not int or value < 1)
            ):
                _reject()
        elif name in arguments or (value is not Undefined and value is not None):
            _reject()
    return root


def _field(full: Mapping[str, Any], observed: Mapping[str, Any], name: str) -> None:
    current = full["types"][full["type"]]["fields"].get(name)
    fields = (
        observed.get("output_fields", {})
        if observed.get("schema_view") == "overview"
        else observed.get("types", {}).get(full["type"], {}).get("fields", {})
    )
    if (
        not isinstance(current, Mapping)
        or fields.get(name) != current
        or "[" in current["type"]
        or current.get("value_contract")
        != {
            "source": "interface_attribute",
            "field": name,
            "transform": "identity",
        }
    ):
        _reject()
    definition = full["types"].get(re.sub(r"[!]", "", current["type"]), {})
    if definition.get("kind") not in {"scalar", "enum"}:
        _reject()


def _filter_value(
    args: Mapping[str, Any],
    full: Mapping[str, Any],
    observed: Mapping[str, Any],
    field: str,
    path: object,
) -> object:
    if not isinstance(path, list) or any(
        not isinstance(part, str) or not part for part in path
    ):
        _reject("invalid_filter_path")
    query_type = runtime_schema().query_type
    if query_type is None:
        _reject()
    root_field = query_type.fields[_root(args, full)]
    filter_name = argument_name(root_field, "filter")
    prefix = ["filters"] if path[:1] == ["filters"] else ["arguments", filter_name]
    if filter_name is None or path[: len(prefix)] != prefix:
        _reject("invalid_filter_path")
    selectors = path[len(prefix) :]
    if (
        not selectors
        or len(selectors) > 2
        or (len(selectors) == 2 and selectors[0] != field)
    ):
        _reject("invalid_filter_path")
    type_name = str(root_field.args[filter_name].type).removesuffix("!")
    for index, selector in enumerate(selectors):
        definition = full["types"].get(type_name)
        if not isinstance(definition, Mapping) or definition.get("kind") != "input":
            _reject()
        if observed.get("types", {}).get(type_name) != definition:
            _reject("filter_input_definition_not_observed")
        signature = definition["fields"].get(selector)
        if not isinstance(signature, Mapping):
            _reject()
        if index < len(selectors) - 1:
            if signature.get("filter_contract") is not None:
                _reject()
            type_name = signature["type"].removesuffix("!")
    marker = signature.get("filter_contract")
    if (
        not isinstance(marker, Mapping)
        or marker.get("source") != "interface_filter"
        or marker.get("field") != field
        or marker.get("operator") not in {"exact", "in"}
    ):
        _reject()
    value: object = args
    for part in path:
        if not isinstance(value, Mapping) or part not in value:
            _reject()
        value = value[part]
    if (marker["operator"] == "in") != isinstance(value, list):
        _reject()
    return value


def validate_category_query(
    args: Mapping[str, Any], store: EvidenceStore, task_id: str
) -> dict[str, Any]:
    """Remove scheduler-only metadata after proving all declared bindings atomically."""
    backend = {
        key: value
        for key, value in args.items()
        if key not in QUERY_PROPERTIES and key != "requirement_id"
    }
    if not any(key in args for key in QUERY_PROPERTIES):
        return backend
    manager = backend.get("manager")
    if (
        not isinstance(manager, str)
        or set(QUERY_PROPERTIES).issubset(args)
        or set(backend) - _BACKEND_QUERY_KEYS
    ):
        _reject()
    if "category_lookup" in args:
        lookup = args["category_lookup"]
        if not isinstance(lookup, Mapping) or set(lookup) != {"schema_evidence_id"}:
            _reject()
        full, _ = _schema(store, task_id, lookup["schema_evidence_id"], manager)
        _unfiltered(backend, full)
        return backend
    bindings = args["category_bindings"]
    if not isinstance(bindings, list) or not bindings:
        _reject()
    for binding in bindings:
        if (
            not isinstance(binding, Mapping)
            or set(binding)
            != {"schema_evidence_id", "query_evidence_id", "field", "filter_path"}
            or not isinstance(binding["field"], str)
        ):
            _reject()
        full, observed = _schema(store, task_id, binding["schema_evidence_id"], manager)
        field = binding["field"]
        _field(full, observed, field)
        source = _source(store, task_id, binding["query_evidence_id"], "query")
        lookup_args = _identity(source, "query", manager)
        if _unfiltered(lookup_args, full) != _root(
            backend, full
        ) or field not in lookup_args.get("fields", []):
            _reject()
        witness = json.loads(source.provenance.get("query_schema_witness", "null"))
        digest = observed["snapshot"]
        if (
            not isinstance(witness, Mapping)
            or type(witness.get("version")) is not int
            or witness.get("version") != 1
            or witness.get("stable") is not True
            or not isinstance(witness.get("before"), Mapping)
            or not isinstance(witness.get("after"), Mapping)
            or witness["before"] != witness["after"]
            or witness.get("before", {}).get(manager) != digest
            or witness.get("after", {}).get(manager) != digest
        ):
            _reject()
        payload = source.payload()
        rows = _complete_rows(payload)
        if rows is None or not rows or any(field not in row for row in rows):
            _reject()
        actual = [
            (type(row[field]), row[field]) for row in rows if row[field] is not None
        ]
        value = _filter_value(backend, full, observed, field, binding["filter_path"])
        values = value if isinstance(value, list) else [value]
        if not values or any(
            type(item) not in (str, int, float, bool)
            or (isinstance(item, float) and not math.isfinite(item))
            or (type(item), item) not in actual
            for item in values
        ):
            _reject()
    return backend


def validate_category_result(
    args: Mapping[str, Any],
    store: EvidenceStore,
    task_id: str,
    query_schema_witness: str | None,
) -> None:
    """Recheck admission after dispatch, including the original cached witness."""
    if not any(key in args for key in QUERY_PROPERTIES):
        return
    validate_category_query(args, store, task_id)
    witness = json.loads(query_schema_witness or "null")
    if (
        not isinstance(witness, Mapping)
        or type(witness.get("version")) is not int
        or witness.get("version") != 1
        or witness.get("stable") is not True
        or not isinstance(witness.get("before"), Mapping)
        or not isinstance(witness.get("after"), Mapping)
        or witness["before"] != witness["after"]
    ):
        _reject()
    manager = args["manager"]
    selectors = (
        [args["category_lookup"]]
        if "category_lookup" in args
        else args["category_bindings"]
    )
    for selector in selectors:
        _, observed = _schema(store, task_id, selector["schema_evidence_id"], manager)
        digest = observed["snapshot"]
        if (
            witness.get("before", {}).get(manager) != digest
            or witness.get("after", {}).get(manager) != digest
        ):
            _reject()


def _business_population_identity(
    backend: Mapping[str, Any], full: Mapping[str, Any], source: EvidenceRecord
) -> str:
    """Identify one observed population/projection, excluding only its window."""
    root = _root(backend, full)
    query_type = runtime_schema().query_type
    if query_type is None:
        _reject()
    field = query_type.fields[root]
    window_arguments = {
        argument_name(field, "page"),
        argument_name(field, "page_size"),
    }
    population = {
        key: value
        for key, value in backend.items()
        if key not in {"limit", "offset", "arguments"}
    }
    population["root"] = root
    arguments = backend.get("arguments", {})
    if not isinstance(arguments, Mapping):
        _reject()
    arguments = {
        key: value for key, value in arguments.items() if key not in window_arguments
    }
    if arguments:
        population["arguments"] = arguments
    witness = json.loads(source.provenance["query_schema_witness"])
    return canonical_call_identity(
        "query",
        {
            "population": population,
            "schema_before": witness["before"],
            "schema_after": witness["after"],
        },
    )


def category_lookup_feedback(
    args: Mapping[str, Any],
    record: EvidenceRecord,
    store: EvidenceStore,
    task_id: str,
    business_queries: Sequence[tuple[Mapping[str, Any], EvidenceRecord]] = (),
) -> dict[str, object] | None:
    """Point to the next category step without selecting a category or granting authority.

    Only an exact successful history/result match reaches this helper. Reuse the
    admission owner as well, so stale schema/exposure or foreign/unlinked query
    data cannot be presented as a ready lookup.
    """
    if "category_lookup" not in args:
        return None
    try:
        manager = args["manager"]
        if not isinstance(manager, str):
            return None
        source = _source(store, task_id, record.evidence_id, "query")
        backend = validate_category_query(args, store, task_id)
        if _identity(source, "query", manager) != backend:
            return None
        validate_category_result(
            args, store, task_id, source.provenance.get("query_schema_witness")
        )
        schema_id = args["category_lookup"]["schema_evidence_id"]
        full, observed = _schema(store, task_id, schema_id, manager)
        root = _unfiltered(backend, full)
    except (KeyError, TypeError, ValueError):
        return None
    rows = _complete_rows(source.payload())
    business_populations: dict[str, list[EvidenceRecord]] = {}
    for query_args, candidate in business_queries:
        bindings = query_args.get("category_bindings")
        if not isinstance(bindings, list) or not any(
            isinstance(binding, Mapping)
            and binding.get("query_evidence_id") == source.evidence_id
            for binding in bindings
        ):
            continue
        try:
            business_source = _source(store, task_id, candidate.evidence_id, "query")
            backend = validate_category_query(query_args, store, task_id)
            if _identity(business_source, "query", manager) != backend:
                continue
            validate_category_result(
                query_args,
                store,
                task_id,
                business_source.provenance.get("query_schema_witness"),
            )
            population = _business_population_identity(backend, full, business_source)
        except (KeyError, TypeError, ValueError):
            continue
        previous = business_populations.setdefault(population, [])
        if business_source in previous:
            continue
        # A later complete observation supersedes earlier windows of this exact
        # population only. A newly observed partial result still needs gathering.
        if _complete_rows(business_source.payload()) is not None:
            previous[:] = [business_source]
        else:
            previous.append(business_source)
    business_sources = [
        item
        for population_sources in business_populations.values()
        for item in population_sources
    ]
    schema_ids = []
    for candidate in store.for_task(task_id):
        if candidate.kind != "schema":
            continue
        try:
            _, payload = _schema(store, task_id, candidate.evidence_id, manager)
        except (KeyError, TypeError, ValueError):
            continue
        if payload["snapshot"] == observed["snapshot"]:
            schema_ids.append(candidate.evidence_id)
    if business_sources:
        next_step = (
            "check_requirements_and_complete"
            if all(
                _complete_rows(item.payload()) is not None for item in business_sources
            )
            else "gather_complete_business_query"
        )
    elif rows is None:
        next_step = "gather_complete_lookup"
    elif rows:
        next_step = "bind_business_query"
    else:
        next_step = "resolve_unavailable_category_values"
    return {
        "query_evidence_id": source.evidence_id,
        "schema_evidence_id": schema_id,
        "manager": manager,
        "root": root,
        "snapshot": observed["snapshot"],
        "complete": rows is not None,
        "next_step": next_step,
        "schema_evidence_ids": schema_ids,
        **(
            {
                "business_query_evidence_ids": [
                    item.evidence_id for item in business_sources
                ]
            }
            if business_sources
            else {}
        ),
    }
