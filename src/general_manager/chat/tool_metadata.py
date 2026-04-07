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


TOOL_DESCRIPTIONS: dict[str, str] = {
    "search_managers": "Search exposed managers by text.",
    "get_manager_schema": "Inspect one manager's fields, relations, and filters.",
    "find_path": "Find a relationship traversal path between exposed managers.",
    "query": "Execute a structured read query via GraphQL.",
    "mutate": "Execute an allow-listed mutation via GraphQL.",
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
            }
        },
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
            "filters": {
                "type": "object",
                "description": "GraphQL filter arguments keyed by filter name.",
                "additionalProperties": True,
            },
            "fields": {
                "type": "array",
                "description": "Fields to select. Use strings for scalar fields and single-key objects for nested relations.",
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
            "confirmed": {
                "type": "boolean",
                "description": "Set true only after the user clearly confirms the write action.",
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
            "filters": {"material__name": "Steel"},
            "fields": ["name", {"material": ["name"]}],
            "limit": 5,
            "offset": 0,
        },
    ),
    (
        "query",
        {
            "manager": "ProjectManager",
            "filters": {"parts__material__name": "Cobalt"},
            "fields": ["name"],
            "limit": 10,
        },
    ),
    (
        "query",
        {
            "manager": "ProjectManager",
            "filters": {"name": "Apollo"},
            "fields": ["name", {"parts": ["name", {"material": ["name"]}]}],
            "limit": 1,
        },
    ),
    (
        "mutate",
        {
            "mutation": "createPart",
            "input": {"name": "Bolt"},
            "confirmed": True,
        },
    ),
)
