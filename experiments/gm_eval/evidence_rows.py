"""Select identity rows only through saved query selections and typed schemas."""

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any


def _selections(fields: list[Any], name: str) -> list[list[Any] | None]:
    matches: list[list[Any] | None] = []
    for field in fields:
        if field == name:
            matches.append(None)
        elif isinstance(field, Mapping):
            if field.get("field") == name:
                children = field.get("fields")
                matches.append(children if isinstance(children, list) else None)
            elif "field" not in field and name in field:
                children = field[name]
                matches.append(children if isinstance(children, list) else None)
    return matches


def _selected_row(row: Mapping[str, Any], fields: list[Any]) -> dict[str, Any]:
    return {
        name: value
        for name, value in row.items()
        if len(_selections(fields, name)) == 1
    }


def _snapshot(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _consistent_schema(schema: Mapping[str, Any]) -> bool:
    """Reject contradictory declarations even when their claimed digest agrees."""
    view = schema.get("schema_view")
    if "schema_view" in schema and (
        view not in {"overview", "detail", "full"}
        or not _snapshot(schema.get("snapshot"))
    ):
        return False
    types, manifest = schema.get("types", {}), schema.get("type_manifest", {})
    if not isinstance(types, Mapping) or not isinstance(manifest, Mapping):
        return False
    for name, definition in types.items():
        declared = manifest.get(name)
        if not isinstance(definition, Mapping):
            return False
        if isinstance(declared, Mapping) and any(
            key in declared and key in definition and declared[key] != definition[key]
            for key in ("kind", "manager")
        ):
            return False
    root, fields = schema.get("type"), schema.get("output_fields")
    owner = types.get(root) if isinstance(root, str) else None
    # Untagged legacy captures need actual loaded types, not lite declarations.
    if "schema_view" not in schema and (
        ("schema_complete" in schema and schema["schema_complete"] is not True)
        or not isinstance(owner, Mapping)
        or owner.get("kind") != "object"
        or not isinstance(owner.get("fields"), Mapping)
    ):
        return False
    if fields is not None and not isinstance(fields, Mapping):
        return False
    return not (
        isinstance(owner, Mapping)
        and fields is not None
        and (owner.get("kind") != "object" or owner.get("fields") != fields)
    )


def _merge_schema(
    known: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any] | None:
    if known == incoming:
        return deepcopy(known)
    # Legacy complete captures remain eligible only when identical. Selective
    # fragments need a real, matching manager snapshot, never latest-view wins.
    if (
        known.get("manager") != incoming.get("manager")
        or not _snapshot(known.get("snapshot"))
        or known.get("snapshot") != incoming.get("snapshot")
    ):
        return None
    merged = deepcopy(known)
    for name, value in incoming.items():
        if name in {"schema_view", "schema_complete"}:
            continue
        if name in {"types", "type_manifest"}:
            definitions = merged.setdefault(name, {})
            if not isinstance(value, Mapping) or not isinstance(definitions, dict):
                return None
            for typename, definition in value.items():
                if typename in definitions and definitions[typename] != definition:
                    return None
                definitions[typename] = deepcopy(definition)
        elif name in merged and merged[name] != value:
            return None
        else:
            merged[name] = deepcopy(value)
    return merged if _consistent_schema(merged) else None


def _schemas_before(
    query: Mapping[str, Any], calls: list[dict[str, Any]]
) -> dict[str, tuple[dict[str, Any], list[str]]]:
    schemas: dict[str, tuple[dict[str, Any], list[str]]] = {}
    ambiguous: set[str] = set()
    for call in calls:
        if call is query:
            break
        args, output = call.get("arguments"), call.get("output")
        if (
            call.get("name") != "get_manager_schema"
            or call.get("error")
            or not isinstance(args, Mapping)
            or not isinstance(output, dict)
            or output.get("contract_version") != 2
            or not isinstance(output.get("manager"), str)
            or args.get("manager") != output["manager"]
            or args.get("view") not in (None, output.get("schema_view", "full"))
            or args.get("snapshot") not in (None, output.get("snapshot"))
            or not isinstance(call.get("id"), str)
            or sum(other.get("id") == call.get("id") for other in calls) != 1
            or (
                isinstance(call.get("source_turn"), int)
                and isinstance(query.get("source_turn"), int)
                and call["source_turn"] > query["source_turn"]
            )
        ):
            continue
        manager = output["manager"]
        if not _consistent_schema(output):
            ambiguous.add(manager)
            continue
        if manager in schemas:
            merged = _merge_schema(schemas[manager][0], output)
            if merged is None:
                ambiguous.add(manager)
            else:
                schemas[manager] = (merged, [*schemas[manager][1], call["id"]])
        else:
            schemas[manager] = (deepcopy(output), [call["id"]])
    return {name: value for name, value in schemas.items() if name not in ambiguous}


def _typed_path(
    schema: Mapping[str, Any], path: list[str], target: str
) -> list[bool] | None:
    types, manifest = schema.get("types", {}), schema.get("type_manifest", {})
    root = current = schema.get("type")
    if (
        not isinstance(types, Mapping)
        or not isinstance(manifest, Mapping)
        or not isinstance(current, str)
    ):
        return None
    modern = schema.get("schema_view") in {"overview", "detail", "full"} and _snapshot(
        schema.get("snapshot")
    )
    relation_types = schema.get("relation_types", {})
    tail = relation_types.get(path[0]) if isinstance(relation_types, Mapping) else None
    if tail is not None and (
        not modern
        or not isinstance(tail, list)
        or len(tail) != len(path) - 1
        or not all(isinstance(signature, str) for signature in tail)
    ):
        return None
    collections: list[bool] = []
    for index, hop in enumerate(path):
        owner = types.get(current)
        if modern and owner is None and current == root:
            declared = manifest.get(root)
            fields = schema.get("output_fields")
            if (
                isinstance(declared, Mapping)
                and declared.get("kind") == "object"
                and isinstance(fields, Mapping)
            ):
                owner = {"kind": "object", "fields": fields}
        projected = tail[index - 1] if tail is not None and index else None
        if modern and owner is None and projected is not None:
            declared = manifest.get(current)
            if isinstance(declared, Mapping) and declared.get("kind") == "object":
                owner = {"kind": "object", "fields": {hop: {"type": projected}}}
        if not isinstance(owner, Mapping) or owner.get("kind") != "object":
            return None
        fields = owner.get("fields")
        field = fields.get(hop) if isinstance(fields, Mapping) else None
        typename = field.get("type") if isinstance(field, Mapping) else None
        if not isinstance(typename, str) or (
            projected is not None and typename != projected
        ):
            return None
        parsed = re.fullmatch(
            r"(?P<plain>[_A-Za-z][_0-9A-Za-z]*)!?|"
            r"\[(?P<item>[_A-Za-z][_0-9A-Za-z]*)!?\]!?",
            typename,
        )
        if parsed is None:
            return None
        collections.append(parsed.group("item") is not None)
        current = parsed.group("item") or parsed.group("plain")
    # Only declared manager references may terminate a selected path. An
    # unloaded object/union cannot be inferred from its name or returned JSON.
    # Manifest references are snapshot-bound modern declarations only.
    terminal = types.get(current, manifest.get(current) if modern else None)
    if (
        isinstance(terminal, Mapping)
        and terminal.get("kind") == "reference"
        and terminal.get("manager") == target
    ):
        return collections
    return None


def _composite_leaf_selected(
    schema: Mapping[str, Any],
    typename: str,
    fields: list[Any],
    schemas: Mapping[str, tuple[dict[str, Any], list[str]]],
) -> bool:
    """Reject leaf selectors only where bound definitions prove an object."""
    types, manifest = schema.get("types", {}), schema.get("type_manifest", {})
    modern = schema.get("schema_view") in {"overview", "detail", "full"}
    owner = types.get(typename, {})
    definitions = owner.get("fields", {}) if isinstance(owner, Mapping) else {}
    if modern and typename == schema.get("type") and not definitions:
        definitions = schema.get("output_fields", {})
    if not isinstance(definitions, Mapping):
        return False
    for name, field in definitions.items():
        if not isinstance(field, Mapping) or not isinstance(field.get("type"), str):
            continue
        target = field["type"].removesuffix("!")
        while target.startswith("[") and target.endswith("]"):
            target = target[1:-1].removesuffix("!")
        if re.fullmatch(r"[_A-Za-z][_0-9A-Za-z]*", target) is None:
            continue
        definition = types.get(target, manifest.get(target, {}) if modern else {})
        if not isinstance(definition, Mapping) or definition.get("kind") not in {
            "object",
            "reference",
        }:
            continue
        selections = _selections(fields, name)
        if any(children is None or not children for children in selections):
            return True
        nested_schema, nested_type = schema, target
        if definition.get("kind") == "reference":
            manager = definition.get("manager")
            bound = schemas.get(manager) if isinstance(manager, str) else None
            if bound is None:
                continue
            nested_schema, _ = bound
            nested_type = nested_schema.get("type")
        if isinstance(nested_type, str) and any(
            _composite_leaf_selected(nested_schema, nested_type, children, schemas)
            for children in selections
            if children is not None
        ):
            return True
    return False


def query_identity_rows(
    query: Mapping[str, Any], calls: list[dict[str, Any]], manager: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep actual row coordinates, schema calls and the query binding as proof."""
    args, output = query.get("arguments"), query.get("output", query.get("result"))
    root = query.get("root_manager")
    if (
        query.get("name") != "query"
        or sum(call.get("id") == query.get("id") for call in calls) != 1
        or query.get("error")
        or not isinstance(root, str)
        or not isinstance(args, Mapping)
        or args.get("manager") != root
        or not isinstance(args.get("fields"), list)
        or not isinstance(output, Mapping)
        or not isinstance(output.get("data"), list)
        or not all(isinstance(row, dict) for row in output["data"])
        or not isinstance(query.get("managers"), list)
        or not all(isinstance(name, str) for name in query["managers"])
        or root not in query["managers"]
        or manager not in query["managers"]
    ):
        return [], []
    schemas = _schemas_before(query, calls)
    bound = schemas.get(root)
    if bound is not None and isinstance(bound[0].get("type"), str):
        if _composite_leaf_selected(
            bound[0], bound[0]["type"], args["fields"], schemas
        ):
            return [], []
    rows: list[dict[str, Any]] = []
    proofs: list[dict[str, Any]] = []

    def walk(
        owner: str,
        fields: list[Any],
        located: list[tuple[dict[str, Any], list[str | int]]],
        schema_calls: list[str],
        snapshots: dict[str, str],
    ) -> None:
        if owner not in query["managers"]:
            return
        if owner == manager:
            for row, coordinate in located:
                rows.append(_selected_row(row, fields))
                proofs.append(
                    {
                        "row_path": coordinate,
                        "query_call_id": query["id"],
                        "target_manager": owner,
                        "schema_call_ids": list(dict.fromkeys(schema_calls)),
                        **({"schema_snapshots": dict(snapshots)} if snapshots else {}),
                        "selected_fields": sorted(_selected_row(row, fields)),
                    }
                )
        if owner not in schemas:
            return
        schema, schema_ids = schemas[owner]
        relations = schema.get("relations")
        if not isinstance(relations, list):
            return
        for relation in relations:
            if not isinstance(relation, Mapping):
                continue
            path, target = relation.get("path"), relation.get("target")
            if (
                not isinstance(path, list)
                or not path
                or not all(isinstance(hop, str) and hop for hop in path)
                or relation.get("name") != path[0]
                or not isinstance(target, str)
                or sum(
                    isinstance(other, Mapping) and other.get("name") == path[0]
                    for other in relations
                )
                != 1
            ):
                continue
            collections = _typed_path(schema, path, target)
            if collections is None:
                continue
            selected, nested = fields, located
            for hop, collection in zip(path, collections, strict=True):
                choices = _selections(selected, hop)
                if len(choices) != 1 or not isinstance(choices[0], list):
                    nested = []
                    break
                selected = choices[0]
                next_rows: list[tuple[dict[str, Any], list[str | int]]] = []
                for row, coordinate in nested:
                    value = row.get(hop)
                    if not collection and isinstance(value, dict):
                        next_rows.append((value, [*coordinate, hop]))
                    elif collection and isinstance(value, list):
                        next_rows.extend(
                            (item, [*coordinate, hop, index])
                            for index, item in enumerate(value)
                            if isinstance(item, dict)
                        )
                nested = next_rows
            if nested:
                # Each recursion consumes selected fields; cyclic manager graphs
                # cannot cause an unbounded traversal of a finite query.
                binding = dict(snapshots)
                if _snapshot(schema.get("snapshot")):
                    binding[owner] = schema["snapshot"]
                walk(target, selected, nested, [*schema_calls, *schema_ids], binding)

    walk(
        root,
        args["fields"],
        [(row, ["data", i]) for i, row in enumerate(output["data"])],
        [],
        {},
    )
    return rows, proofs
