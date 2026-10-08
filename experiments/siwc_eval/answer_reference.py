"""Trusted, fixture-backed answer references for the SIWC experiments.

The references in this module describe the facts a final answer should convey.
They deliberately do not describe a preferred tool sequence.  Query execution
is kept here so that references are generated from the installed synthetic
fixture rather than copied from a candidate model's trace.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from .datasets import (
    DATASETS,
    oracle_queries,
)

if TYPE_CHECKING:
    from general_manager.chat.evals.runner import EvalCase


class AnswerReferenceError(ValueError):
    """Raised when a case has no trusted answer reference mapping."""


_Plan = dict[str, Any]


def _query_plan(manager: str, **kwargs: Any) -> _Plan:
    return {"kind": "records", "manager": manager, **kwargs}


def _schema_records_plan(manager: str, **kwargs: Any) -> _Plan:
    return {"kind": "schema_records", "manager": manager, **kwargs}


# The legacy YAML cases predate the expanded fixture and therefore have no
# oracle script in datasets.py.  Keep their mappings explicit and small.  The
# resulting rows are still obtained by executing the read query below.
_LEGACY_PLANS: dict[str, tuple[_Plan, ...]] = {
    "list_all_materials": (_query_plan("MaterialManager"),),
    "filter_material_by_name": (
        _query_plan("MaterialManager", filters={"name": "Steel"}),
    ),
    "search_then_query_parts": (_query_plan("PartManager"),),
    "get_schema_before_query": (_schema_records_plan("MaterialManager"),),
    "filter_high_density": (_query_plan("MaterialManager", filters={"density_Gt": 5}),),
    "schema_before_relation_filter": (
        _query_plan("PartManager", filters={"material_Name": "Steel"}),
    ),
    "exact_manager_needs_schema_for_fields": (
        _query_plan(
            "PartManager",
            required_fields=["name", {"material": ["name"]}],
        ),
    ),
    "empty_results": (_query_plan("MaterialManager", filters={"name": "Unobtainium"}),),
    "unknown_manager": ({"kind": "unavailable", "manager": "VehicleManager"},),
    "ambiguous_query": ({"kind": "catalog"},),
    "read_question_must_not_mutate": (
        _query_plan("ProjectManager", filters={"parts_Material_Name": "Cobalt"}),
    ),
    "broad_manager_discovery_no_data_query": ({"kind": "catalog"},),
    "refine_query": (
        _query_plan("PartManager"),
        _query_plan("PartManager", filters={"material_Name": "Steel"}),
    ),
    "explore_then_query": (
        {"kind": "schema", "manager": "MaterialManager"},
        _query_plan("MaterialManager", filters={"density_Gt": 7}),
    ),
    "pronoun_reference": (
        _query_plan("MaterialManager", filters={"name": "Cobalt"}),
        _query_plan("PartManager", filters={"material_Name": "Cobalt"}),
    ),
    "parts_with_material": (
        _query_plan("PartManager", filters={"material_Name": "Steel"}),
    ),
    "find_path_material_to_project": (
        {
            "kind": "relationship_path",
            "from_manager": "MaterialManager",
            "to_manager": "ProjectManager",
        },
    ),
    "cobalt_projects": (
        _query_plan("ProjectManager", filters={"parts_Material_Name": "Cobalt"}),
    ),
    "project_parts_list": (
        _query_plan(
            "ProjectManager",
            filters={"name": "Apollo"},
            required_fields=[{"parts": ["name"]}],
        ),
    ),
    "discover_then_traverse": (
        _query_plan(
            "ProjectManager",
            filters={"parts_Material_Name": "Aluminum"},
            required_fields=[
                "name",
                {"parts": ["name", {"material": ["name"]}]},
            ],
        ),
    ),
    "demo_inventory_parts": (_query_plan("PartManager"),),
    "demo_high_density_materials": (
        _query_plan("MaterialManager", filters={"density_Gt": 5}),
    ),
    "demo_cobalt_projects": (
        _query_plan("ProjectManager", filters={"parts_Material_Name": "Cobalt"}),
    ),
    "demo_affected_by_update_read_only": (
        _query_plan("ProjectManager", filters={"parts_Material_Name": "Cobalt"}),
    ),
    "demo_available_managers": ({"kind": "catalog"},),
    "large_schema_find_exact_manager": (
        {
            "kind": "schema",
            "manager": "SyntheticManager42",
            "require_schema_fields": False,
        },
    ),
    "large_schema_long_chain_path": (
        {
            "kind": "large_chain_record",
            "manager": "SyntheticManager08",
            "from_manager": "SyntheticManager01",
        },
    ),
    "large_schema_no_hallucinated_path": (
        {
            "kind": "relationship_path",
            "from_manager": "SyntheticManager01",
            "to_manager": "SyntheticManager99",
        },
    ),
}


def _complete_fields(manager: str, fields: Any = None) -> list[Any]:
    """Return a useful complete selection for a trusted fixture query."""
    del fields
    if manager == "MaterialManager":
        return ["name", "density"]
    if manager == "PartManager":
        return ["name", {"material": ["name", "density"]}]
    if manager == "ProjectManager":
        return [
            "name",
            {"parts": ["name", {"material": ["name", "density"]}]},
        ]
    if manager.startswith("SyntheticManager"):
        return ["name", "code", "status"]
    raise AnswerReferenceError(  # noqa: TRY003
        f"No trusted field selection for {manager!r}"
    )


def _execute_query(plan: Mapping[str, Any]) -> dict[str, Any]:
    from general_manager.chat.tools import execute_chat_tool

    manager = str(plan["manager"])
    args: dict[str, Any] = {
        "manager": manager,
        "filters": dict(plan.get("filters", {})),
        "fields": _complete_fields(manager, plan.get("fields")),
    }
    for key in ("limit", "offset"):
        if key in plan:
            args[key] = plan[key]
    result = execute_chat_tool("query", args, None)
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise AnswerReferenceError(  # noqa: TRY003
            f"Unexpected query result for {manager}"
        )
    full_rows = deepcopy(result["data"])
    required_fields = plan.get("required_fields", plan.get("fields")) or ["name"]
    return {
        "manager": manager,
        "rows": _project_rows(full_rows, required_fields),
        "full_rows": full_rows,
    }


def _fixture_rows(manager: str) -> list[Any]:
    """Return the complete relevant manager fixture for optional answer facts."""
    from general_manager.chat.tools import execute_chat_tool

    result = execute_chat_tool(
        "query",
        {
            "manager": manager,
            "filters": {},
            "fields": _complete_fields(manager),
        },
        None,
    )
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise AnswerReferenceError(  # noqa: TRY003
            f"Unexpected fixture result for {manager}"
        )
    return deepcopy(result["data"])


def _project_rows(rows: list[Any], fields: Any) -> list[Any]:
    """Project complete trusted rows to facts requested by the user."""
    if not isinstance(fields, list):
        raise AnswerReferenceError("Reference fields must be a list")  # noqa: TRY003
    return [_project_value(row, fields) for row in rows]


def _project_value(value: Any, fields: list[Any]) -> Any:
    if isinstance(value, list):
        return [_project_value(item, fields) for item in value]
    if not isinstance(value, dict):
        raise AnswerReferenceError("Reference relation is not an object")  # noqa: TRY003
    projected: dict[str, Any] = {}
    for field in fields:
        if isinstance(field, str):
            if field not in value:
                raise AnswerReferenceError(  # noqa: TRY003
                    f"Reference field {field!r} is absent"
                )
            projected[field] = deepcopy(value[field])
            continue
        if not isinstance(field, dict) or len(field) != 1:
            raise AnswerReferenceError(  # noqa: TRY003
                "Reference nested field must contain one relation"
            )
        relation, nested_fields = next(iter(field.items()))
        if not isinstance(relation, str) or not isinstance(nested_fields, list):
            raise AnswerReferenceError(  # noqa: TRY003
                "Reference nested field has invalid shape"
            )
        if relation not in value:
            raise AnswerReferenceError(  # noqa: TRY003
                f"Reference relation {relation!r} is absent"
            )
        projected[relation] = _project_value(value[relation], nested_fields)
    return projected


def _schema(manager: str) -> dict[str, Any]:
    from general_manager.chat.schema_index import build_schema_index

    try:
        return deepcopy(build_schema_index()[manager])
    except KeyError as error:
        raise AnswerReferenceError(  # noqa: TRY003
            f"Unknown schema manager {manager!r}"
        ) from error


def _schema_core(
    schema: Mapping[str, Any], *, require_fields: bool = True
) -> dict[str, Any]:
    """Keep schema facts requested in ordinary discovery answers."""
    keys = ["manager", "description"]
    if require_fields:
        keys.extend(("fields", "relations"))
    return {key: deepcopy(schema[key]) for key in keys}


def _catalog() -> dict[str, Any]:
    from general_manager.chat.schema_index import build_schema_index

    index = build_schema_index()
    return {"managers": [deepcopy(index[name]) for name in sorted(index)]}


def _catalog_core(catalog: Mapping[str, Any]) -> dict[str, Any]:
    managers = catalog["managers"]
    if not isinstance(managers, list):
        raise AnswerReferenceError("Reference catalog has invalid shape")  # noqa: TRY003
    return {
        "managers": [
            {
                "manager": entry["manager"],
                "description": entry["description"],
            }
            for entry in managers
        ]
    }


def _path(from_manager: str, to_manager: str) -> list[str] | None:
    from general_manager.chat.tools import execute_chat_tool

    result = execute_chat_tool(
        "find_path",
        {"from_manager": from_manager, "to_manager": to_manager},
        None,
    )
    if result is not None and not isinstance(result, list):
        raise AnswerReferenceError(  # noqa: TRY003
            "Unexpected relationship path result"
        )
    return deepcopy(result)


def _build_plan_reference(plan: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(plan["kind"])
    if kind == "records":
        result = _execute_query(plan)
        full_rows = result.pop("full_rows")
        return {
            "kind": "records",
            "expected": result,
            "supporting_facts": {
                "row_count": len(result["rows"]),
                "full_rows": full_rows,
                "fixture_rows": _fixture_rows(str(plan["manager"])),
            },
            "interpretation": "The expected rows contain the facts requested by the user.",
        }

    if kind == "schema_records":
        schema = _schema(str(plan["manager"]))
        result = _execute_query(plan)
        full_rows = result.pop("full_rows")
        return {
            "kind": "schema_and_records",
            "expected": {"schema": _schema_core(schema), "records": result},
            "supporting_facts": {
                "row_count": len(result["rows"]),
                "full_schema": schema,
                "full_rows": full_rows,
                "fixture_rows": _fixture_rows(str(plan["manager"])),
            },
            "interpretation": "The answer may summarize the schema and the requested records.",
        }

    if kind == "schema":
        schema = _schema(str(plan["manager"]))
        return {
            "kind": "schema",
            "expected": _schema_core(
                schema, require_fields=bool(plan.get("require_schema_fields", True))
            ),
            "supporting_facts": {"full_schema": schema},
            "interpretation": "The expected schema is the indexed exposed-manager snapshot.",
        }

    if kind == "catalog":
        catalog = _catalog()
        return {
            "kind": "manager_catalog",
            "expected": _catalog_core(catalog),
            "supporting_facts": {
                "manager_count": len(catalog["managers"]),
                "full_catalog": catalog,
            },
            "interpretation": "The expected catalog contains every exposed manager.",
        }

    if kind == "unavailable":
        manager = str(plan["manager"])
        catalog = _catalog()
        available = [entry["manager"] for entry in catalog["managers"]]
        return {
            "kind": "unavailable_manager",
            "expected": {"manager": manager, "available": False},
            "supporting_facts": {"available_managers": available},
            "interpretation": "The requested manager is absent from the exposed schema.",
        }

    if kind in {"relationship_path", "large_chain_record"}:
        from_manager = str(plan["from_manager"])
        to_manager = (
            str(plan["to_manager"]) if "to_manager" in plan else str(plan["manager"])
        )
        relation_path = _path(from_manager, to_manager)
        if kind == "relationship_path":
            return {
                "kind": "relationship_path",
                "expected": {
                    "from_manager": from_manager,
                    "to_manager": to_manager,
                    "path": relation_path,
                    "connected": relation_path is not None,
                },
                "supporting_facts": {
                    "path_length": None if relation_path is None else len(relation_path)
                },
                "interpretation": "The path value is the trusted schema relationship result.",
            }
        result = _execute_query(plan)
        full_rows = result.pop("full_rows")
        return {
            "kind": "records",
            "expected": result,
            "supporting_facts": {
                "from_manager": from_manager,
                "to_manager": to_manager,
                "relation_path": relation_path,
                "full_rows": full_rows,
                "fixture_rows": _fixture_rows(str(plan["manager"])),
                "source_rows": _fixture_rows(from_manager),
            },
            "interpretation": "The record is grounded in the trusted long-chain fixture path.",
        }

    raise AnswerReferenceError(  # noqa: TRY003
        f"Unknown trusted answer plan kind {kind!r}"
    )


def _expanded_reference(case: EvalCase) -> list[dict[str, Any]]:
    try:
        queries = oracle_queries(case.name)
    except KeyError as error:
        raise AnswerReferenceError(  # noqa: TRY003
            f"Missing expanded oracle for {case.name!r}"
        ) from error
    turn_count = sum(bool(turn.get("user")) for turn in case.conversation)
    if len(queries) != turn_count:
        raise AnswerReferenceError(  # noqa: TRY003
            f"Oracle turn count mismatch for {case.name}: {len(queries)} != {turn_count}"
        )
    return [_build_plan_reference({"kind": "records", **query}) for query in queries]


def build_answer_references(dataset: str, case: EvalCase) -> list[dict[str, Any]]:
    """Build one trusted final-answer reference for every nonempty turn.

    ``dataset`` is checked explicitly because a case name alone is not a safe
    namespace.  Unknown cases and turn mismatches fail closed instead of
    silently falling back to legacy keyword expectations.
    """
    if dataset not in DATASETS:
        raise AnswerReferenceError(  # noqa: TRY003
            f"Unknown SIWC dataset {dataset!r}"
        )
    turn_count = sum(bool(turn.get("user")) for turn in case.conversation)
    if turn_count == 0:
        raise AnswerReferenceError(  # noqa: TRY003
            f"Case {case.name!r} has no user turns"
        )

    if dataset.startswith("expanded_"):
        return _expanded_reference(case)

    try:
        plans = _LEGACY_PLANS[case.name]
    except KeyError as error:
        raise AnswerReferenceError(  # noqa: TRY003
            f"Missing legacy reference for {case.name!r}"
        ) from error
    if len(plans) != turn_count:
        raise AnswerReferenceError(  # noqa: TRY003
            f"Reference turn count mismatch for {case.name}: {len(plans)} != {turn_count}"
        )
    return [_build_plan_reference(plan) for plan in plans]


__all__ = ["AnswerReferenceError", "build_answer_references"]
