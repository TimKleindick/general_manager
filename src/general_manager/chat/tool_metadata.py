"""Shared chat tool metadata used by providers and prompt generation."""

from __future__ import annotations

from typing import Any


FIELD_SELECTION_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "string"},
        {
            "type": "object",
            "minProperties": 1,
            "maxProperties": 1,
            "additionalProperties": {
                "type": "array",
                "items": {"$ref": "#/$defs/fieldSelection"},
            },
        },
    ]
}


FIELD_SELECTION_SCHEMA["oneOf"].append(
    {
        "type": "object",
        "properties": {
            "field": {"type": "string"},
            "arguments": {"type": "object", "additionalProperties": True},
            "fields": {"type": "array", "items": {"$ref": "#/$defs/fieldSelection"}},
        },
        "required": ["field"],
        "additionalProperties": False,
    }
)


READ_TOOL_GUIDANCE = (
    "Read contract 2, schema inspection 3: get_manager_schema defaults to overview: exact root signatures, "
    "output field signatures, relation manager references and a direct type_manifest. "
    "Load needed input/enum/object definitions with view=detail, types=[exact observed names], "
    "snapshot=the same manager overview digest. Details contain exact one-hop definitions "
    "and a type_manifest for their next references; load those names as needed at the same snapshot. Related manager references require that "
    "manager's own overview and snapshot. On schema_snapshot_mismatch get a fresh overview "
    "and rebuild selectors. view=full is explicit, never an automatic fallback. "
    "schema_complete marks full schema only and does not prove query/task completion. "
    "Use those GraphQL names unchanged; Python aliases "
    "are not supported. At the root, query.fields selects row fields directly, "
    'for example ["code"]. The adapter adds the root items/pageInfo wrapper; '
    "do not include that wrapper in query.fields. Nested collections retain "
    'their items/pageInfo wrappers, for example {"materialsList": '
    '[{"items": ["code"]}, {"pageInfo": ["totalCount"]}]}. Nested '
    "arguments use {field, arguments, fields}. Respect advertised filter depth. "
    "The relation quantifier none is unsupported inside exclude inputs; "
    "use it only in filter inputs that advertise it. "
    "Inspect referenced managers separately when their output type is a reference. "
    "query.complete describes this response's full root and nested coverage, not "
    "whether the user's task is complete. has_more describes the root page only; "
    "null total_count/has_more means unknown, never complete coverage. Offset uses "
    "bounded native pages; do not combine it with page/pageSize arguments. "
    "For exhaustive questions gather every required page; a bounded question may "
    "use a partial response. Keep pageInfo totals when reading nested collections."
)


TOOL_DESCRIPTIONS: dict[str, str] = {
    "search_managers": (
        "Search exposed managers by text. Use when the exact or unknown manager "
        "must be discovered."
    ),
    "get_manager_schema": (
        "Inspect one exposed manager before query. Defaults to compact overview. Load exact observed "
        "type names with view=detail plus its snapshot; full is explicit. Selectors fail atomically."
    ),
    "find_path": (
        "Find a relationship traversal path between exposed managers for "
        "cross-manager questions."
    ),
    "query": (
        "Execute a structured read query via GraphQL after inspecting schema and "
        "choosing fields. Contract 2 uses exact executable GraphQL names, typed nested "
        "filter objects. Root query.fields selects row fields directly (for example "
        '["code"]); the adapter adds root items/pageInfo. Keep items/pageInfo '
        "wrappers for nested collections. Python aliases are rejected. "
        "The relation quantifier none is unsupported inside exclude inputs."
    ),
    "mutate": (
        "Execute one allow-listed mutation via GraphQL only after user confirmation; "
        "do not combine mutate with another tool call."
    ),
}


TOOL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_managers": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language search text for manager discovery.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "get_manager_schema": {
        "type": "object",
        "properties": {
            "manager": {
                "type": "string",
                "description": "Exact manager name, for example PartManager.",
            },
            "view": {"enum": ["overview", "detail", "full"], "default": "overview"},
            "types": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "pattern": "^[_A-Za-z][_0-9A-Za-z]*$"},
                "description": "Exact type_manifest names from this manager's overview or detail at the same snapshot.",
            },
            "snapshot": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "description": "The matching manager overview snapshot digest.",
            },
        },
        "allOf": [
            {
                "if": {
                    "required": ["view"],
                    "properties": {"view": {"const": "detail"}},
                },
                "then": {"required": ["types", "snapshot"]},
                "else": {
                    "not": {
                        "anyOf": [{"required": ["types"]}, {"required": ["snapshot"]}]
                    }
                },
            }
        ],
        "required": ["manager"],
        "additionalProperties": False,
    },
    "find_path": {
        "type": "object",
        "properties": {
            "from_manager": {
                "type": "string",
                "description": "Starting manager name.",
            },
            "to_manager": {
                "type": "string",
                "description": "Destination manager name.",
            },
        },
        "required": ["from_manager", "to_manager"],
        "additionalProperties": False,
    },
    "query": {
        "type": "object",
        "properties": {
            "manager": {
                "type": "string",
                "description": "Exact manager name to query.",
            },
            "root": {
                "type": "string",
                "description": "Exact advertised GraphQL root; required only if several roots exist.",
            },
            "arguments": {
                "type": "object",
                "additionalProperties": True,
                "description": "Exact root argument names and typed JSON values from get_manager_schema. Do not duplicate the same filter input in filters and arguments.",
            },
            "filters": {
                "type": "object",
                "description": "The direct fields of the root's advertised filter input, e.g. {name: value}; do not wrap them in {filter: {...}}. Alternatively use arguments with the exact advertised root filter argument name (arguments.filter only when that name is filter). Use either form, never both. Nested objects are allowed only at fields supported by that input type.",
                "additionalProperties": True,
            },
            "fields": {
                "type": "array",
                "description": "Select root row fields directly: the adapter adds root items/pageInfo. Native selections: scalar names, {fieldName: [fields]}, or {field, arguments, fields} for nested arguments. Nested collections retain items/pageInfo.",
                "examples": [["code"]],
                "items": {"$ref": "#/$defs/fieldSelection"},
                "minItems": 1,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": "Maximum number of rows to return.",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Pagination offset.",
            },
        },
        "required": ["manager", "fields"],
        "additionalProperties": False,
        "$defs": {"fieldSelection": FIELD_SELECTION_SCHEMA},
    },
    "mutate": {
        "type": "object",
        "properties": {
            "mutation": {
                "type": "string",
                "description": "Allow-listed mutation name.",
            },
            "input": {
                "type": "object",
                "description": "Mutation input payload.",
                "additionalProperties": True,
            },
        },
        "required": ["mutation", "input"],
        "additionalProperties": False,
    },
}


TOOL_USAGE_EXAMPLES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("search_managers", {"query": "parts"}),
    ("get_manager_schema", {"manager": "PartManager"}),
    (
        "find_path",
        {"from_manager": "PartManager", "to_manager": "MaterialManager"},
    ),
    (
        "query",
        {
            "manager": "PartManager",
            "filters": {"material": {"name": "Steel"}},
            "fields": ["name", {"material": ["name"]}],
            "limit": 5,
            "offset": 0,
        },
    ),
    (
        "query",
        {
            "manager": "ProjectManager",
            "filters": {"partsList": {"any": {"material": {"name": "Cobalt"}}}},
            "fields": ["name"],
            "limit": 10,
        },
    ),
    (
        "query",
        {
            "manager": "ProjectManager",
            "filters": {"name": "Apollo"},
            "fields": [
                "name",
                {
                    "partsList": [
                        {"items": ["name", {"material": ["name"]}]},
                        {"pageInfo": ["totalCount"]},
                    ]
                },
            ],
            "limit": 1,
        },
    ),
    (
        "query",
        {
            "manager": "ProjectManager",
            "arguments": {"filter": {"name": "Apollo"}},
            "fields": [
                "name",
                {
                    "partsList": [
                        {"items": ["name", {"material": ["name"]}]},
                        {"pageInfo": ["totalCount"]},
                    ]
                },
            ],
            "limit": 1,
        },
    ),
    (
        "mutate",
        {
            "mutation": "createPart",
            "input": {"name": "Bolt"},
        },
    ),
)
