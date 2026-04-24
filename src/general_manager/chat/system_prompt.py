"""System prompt builder for chat."""

from __future__ import annotations

import json

from general_manager.chat.schema_index import build_schema_index
from general_manager.chat.settings import get_chat_settings
from general_manager.chat.tool_metadata import TOOL_DESCRIPTIONS, TOOL_USAGE_EXAMPLES
from general_manager.utils.path_mapping import PathMap

PROMPT_MANAGER_DETAIL_LIMIT = 30


def build_system_prompt() -> str:
    """Build the chat system prompt from schema metadata and relationships."""
    index = build_schema_index()
    manager_names = sorted(index.keys())
    lines: list[str] = []
    lines.extend(_identity_section())
    lines.extend(_available_tools_section())
    lines.extend(_tool_decision_section())
    lines.extend(_query_construction_section())
    lines.extend(_answer_rules_section())
    lines.extend(_mutation_safety_section())
    lines.extend(_tool_examples_section())
    lines.extend(_schema_context_section(index, manager_names))
    lines.extend(_developer_instructions_section())
    return "\n".join(lines)


def _identity_section() -> list[str]:
    return [
        "You are a helpful assistant grounded in the GeneralManager GraphQL schema.",
        (
            "Use tools to inspect schema and retrieve live data. Do not guess "
            "data, fields, filters, or relations."
        ),
    ]


def _available_tools_section() -> list[str]:
    lines = ["Available tools:"]
    lines.extend(
        f"- {tool_name}: {description}"
        for tool_name, description in TOOL_DESCRIPTIONS.items()
    )
    return lines


def _tool_decision_section() -> list[str]:
    return [
        "Tool calling rules (follow strictly in order):",
        "Tool decision process:",
        (
            "1. If the exact manager is unknown, call search_managers first. "
            "Domain words like parts, materials, inventory, or catalog are NOT "
            "exact manager names."
        ),
        (
            "2. If fields, filters, or relation names are uncertain, call "
            "get_manager_schema before query."
        ),
        (
            "3. For cross-manager questions, call find_path after identifying "
            "the relevant managers, then query the manager that can return the "
            "requested rows."
        ),
        (
            "4. For data questions, call every needed tool before writing any "
            "answer text. Do not interleave text and tool calls."
        ),
        (
            "5. When a query returns data successfully, use that result. Do not "
            "retry the same question with different syntax."
        ),
    ]


def _query_construction_section() -> list[str]:
    return [
        "Query construction rules:",
        (
            "1. Use exact manager names, exact field names, exact relation names, "
            "and exact filter names from get_manager_schema or the schema context."
        ),
        (
            "2. Always use flat filter keys listed in the schema, such as "
            "material__name or parts__material__name. Never invent nested filter "
            'objects like {"material": {"name": "X"}}.'
        ),
        (
            "3. For query.fields, use strings for scalar fields and single-key "
            "objects for relation selections."
        ),
        (
            "4. Relation selections must be arrays: "
            '{"parts": ["name"]}, {"material": ["name", "density"]}.'
        ),
        (
            '5. Never use wildcard field selections like "*"; call '
            "get_manager_schema when you need field names."
        ),
        "6. Pass JSON objects with exactly the required tool keys.",
    ]


def _answer_rules_section() -> list[str]:
    return [
        "Answer rules:",
        "1. Answer data questions only from tool results.",
        (
            "2. Copy record values exactly from the tool JSON. For small result "
            "sets, include every returned row that answers the question."
        ),
        (
            "3. If query returns no rows, say that no matching records were found. "
            "Do not list examples from unrelated managers or previous turns."
        ),
        (
            "4. If a tool returns an error, do not answer as if data was returned. "
            "Fix the tool arguments using get_manager_schema or explain the error."
        ),
        (
            "5. If search_managers returns multiple plausible managers and the "
            "user did not give enough detail, ask a concise clarifying question."
        ),
        (
            "6. Do not write placeholders like [tool:query]. Do not expose raw "
            "GraphQL unless the user asks for implementation details."
        ),
        (
            "7. Mention manager or field names when that makes the answer easier "
            "to verify."
        ),
    ]


def _mutation_safety_section() -> list[str]:
    return [
        "Mutation safety:",
        "1. Never call mutate unless the user clearly requests a write.",
        (
            "2. Questions about what would be affected, changed, updated, or "
            "deleted are read-only analysis unless the user explicitly confirms "
            "an action."
        ),
        (
            "3. If a mutation is needed and confirmed is not already true, ask "
            "for confirmation before calling mutate."
        ),
    ]


def _tool_examples_section() -> list[str]:
    return [
        f"- Example tool call for {tool_name}: {json.dumps(example, sort_keys=True)}"
        for tool_name, example in TOOL_USAGE_EXAMPLES
    ]


def _schema_context_section(
    index: dict[str, dict[str, object]], manager_names: list[str]
) -> list[str]:
    relationship_lines = _relationship_lines(index, manager_names)
    lines = ["Exposed managers:"]
    if len(manager_names) <= PROMPT_MANAGER_DETAIL_LIMIT:
        lines.extend(
            f"- {summary['manager']}: {summary['description']}"
            for summary in (index[name] for name in manager_names)
        )
    else:
        lines.append(
            f"- {len(manager_names)} exposed managers available. "
            "Use search_managers to discover relevant managers by name, "
            "description, fields, relations, or filters before querying."
        )
    lines.append("Relationship graph:")
    if len(manager_names) > PROMPT_MANAGER_DETAIL_LIMIT:
        lines.append(
            "- Relationship graph omitted for large schemas. Use find_path "
            "after search_managers identifies the relevant managers."
        )
    elif relationship_lines:
        lines.extend(f"- {line}" for line in relationship_lines)
    else:
        lines.append("- No exposed cross-manager paths available.")
    return lines


def _relationship_lines(
    index: dict[str, dict[str, object]], manager_names: list[str]
) -> list[str]:
    relationship_lines: list[str] = []
    if not PathMap.mapping and manager_names:
        PathMap(manager_names[0])
    for (from_manager, to_manager), tracer in sorted(PathMap.mapping.items()):
        if from_manager not in index or to_manager not in index:
            continue
        path = getattr(tracer, "path", None)
        if path:
            relationship_lines.append(
                f"{from_manager} -> {to_manager}: {' -> '.join(path)}"
            )
    return relationship_lines


def _developer_instructions_section() -> list[str]:
    developer_prompt = str(get_chat_settings().get("system_prompt", "") or "").strip()
    if developer_prompt:
        return ["Developer instructions:", developer_prompt]
    return []
