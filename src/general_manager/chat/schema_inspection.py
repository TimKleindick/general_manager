"""Snapshot-bound projections of the same exposed GraphQL read contract."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, NoReturn

from general_manager.chat.graphql_contract import (
    capture_manager_contract,
    INSPECTION_VERSION,
)

SCHEMA_VIEWS = frozenset({"overview", "detail", "full"})
SNAPSHOT_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
TYPE_NAME_PATTERN = re.compile(r"[_A-Za-z][_0-9A-Za-z]*\Z")


class SchemaInspectionError(ValueError):
    """Atomic selector/snapshot rejection; never carries partial definitions."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _reject(code: str) -> NoReturn:
    raise SchemaInspectionError(code)


def _field_references(fields: Mapping[str, Any]) -> set[str]:
    """Named refs in the already filtered native signatures, without expansion."""
    return {
        re.sub(r"[\[\]!]", "", signature["type"])
        for field in fields.values()
        for signature in (field, *field.get("arguments", {}).values())
    }


def _manifest(full: Mapping[str, Any], names: set[str]) -> dict[str, Any]:
    """Resolve each exposed one-hop reference in this exact captured contract."""
    definitions = full["types"]
    if not names.issubset(definitions):
        _reject("invalid_schema_reference")
    return {
        name: {
            key: value
            for key, value in definitions[name].items()
            if key in {"kind", "manager"}
        }
        for name in sorted(names)
    }


def _relation_types(full: Mapping[str, Any]) -> dict[str, list[str]]:
    """Project exact native tail signatures without expanding wrapper definitions."""
    definitions = full["types"]
    selected: dict[str, list[str]] = {}
    for relation in full["relations"]:
        if len(relation["path"]) < 2:
            continue
        current = full["type"]
        signatures = []
        for index, hop in enumerate(relation["path"]):
            signature = definitions[current]["fields"][hop]["type"]
            if index:
                signatures.append(signature)
            current = re.sub(r"[\[\]!]", "", signature)
        selected[relation["name"]] = signatures
    return {name: selected[name] for name in sorted(selected)}


def inspect_manager_schema(
    manager: str,
    *,
    view: str = "overview",
    types: object = None,
    snapshot: object = None,
) -> dict[str, Any] | None:
    """Protocol 3 overview discovers direct refs and native relation-path types.

    Returned references require that manager's own overview/snapshot. Detail
    contains exact definitions plus their next refs. Full is explicit.
    """
    if not isinstance(manager, str) or not manager.strip():
        _reject("invalid_schema_selector")
    if not isinstance(view, str) or view not in SCHEMA_VIEWS:
        _reject("invalid_schema_selector")
    if view == "detail":
        if (
            not isinstance(types, list)
            or not types
            or any(
                not isinstance(name, str) or not TYPE_NAME_PATTERN.fullmatch(name)
                for name in types
            )
            or len(set(types)) != len(types)
            or not isinstance(snapshot, str)
            or not SNAPSHOT_PATTERN.fullmatch(snapshot)
        ):
            _reject("invalid_schema_selector")
    elif types is not None or snapshot is not None:
        _reject("invalid_schema_selector")
    full, digest = capture_manager_contract(manager)
    if full is None:
        return None
    if view == "detail" and snapshot != digest:
        _reject("schema_snapshot_mismatch")
    metadata = {
        "inspection_version": INSPECTION_VERSION,
        "schema_view": view,
        "snapshot": digest,
        "schema_complete": view == "full",
    }
    if view == "full":
        return {**full, **metadata}
    if view == "detail":
        assert isinstance(types, list)
        if any(name not in full["types"] for name in types):
            _reject("invalid_schema_selector")
        selected = {name: full["types"][name] for name in sorted(types)}
        names = set(types)
        for definition in selected.values():
            names.update(_field_references(definition.get("fields", {})))
        return {
            "contract_version": full["contract_version"],
            "manager": manager,
            **metadata,
            "types": selected,
            "type_manifest": _manifest(full, names),
        }
    output_fields = full["types"][full["type"]]["fields"]
    relation_types = _relation_types(full)
    names = {full["type"]}
    names.update(_field_references(full["root_fields"]))
    names.update(_field_references(output_fields))
    targets = {relation["target"] for relation in full["relations"]}
    names.update(
        name
        for name, definition in full["types"].items()
        if definition.get("kind") == "reference"
        and definition.get("manager") in targets
    )
    return {
        **{key: value for key, value in full.items() if key != "types"},
        **metadata,
        "output_fields": output_fields,
        "type_manifest": _manifest(full, names),
        **({"relation_types": relation_types} if relation_types else {}),
    }


def dispatch_schema_inspection(args: Mapping[str, Any]) -> dict[str, Any] | None:
    """Strict public tool adapter, including direct Python dispatcher callers."""
    try:
        if set(args) - {"manager", "view", "types", "snapshot"}:
            _reject("invalid_schema_selector")
        # Explicit JSON null is not an omitted selector.
        if any(
            key in args and args[key] is None for key in ("view", "types", "snapshot")
        ):
            _reject("invalid_schema_selector")
        return inspect_manager_schema(
            args.get("manager", ""),
            view=args.get("view", "overview"),
            types=args.get("types"),
            snapshot=args.get("snapshot"),
        )
    except SchemaInspectionError as error:
        return {"status": "error", "code": error.code}
