"""System prompt builder for chat."""

from __future__ import annotations

import json

from general_manager.chat.schema_index import build_schema_index
from general_manager.chat.settings import get_chat_settings
from general_manager.chat.tool_metadata import TOOL_DESCRIPTIONS, TOOL_USAGE_EXAMPLES
from general_manager.utils.path_mapping import PathMap


def build_system_prompt() -> str:
    """Build the chat system prompt from schema metadata and relationships."""
    index = build_schema_index()
    manager_names = sorted(index.keys())
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
    lines = [
        "You are a helpful assistant grounded in the GeneralManager GraphQL schema.",
        "Available tools:",
    ]
    lines.extend(
        f"- {tool_name}: {description}"
        for tool_name, description in TOOL_DESCRIPTIONS.items()
    )
    lines.append("Tool calling rules (follow strictly in order):")
    lines.append(
        "Rule 1 DISCOVERY: Unless the user says an exact manager name like"
        " PartManager, you MUST call search_managers before calling query."
        " Domain words like parts, materials, inventory, or catalog are NOT"
        " exact manager names."
    )
    lines.append(
        "Rule 2 EXPLORATION: When the user asks to explore, discover, or"
        " understand the data model, call search_managers first, then call"
        " find_path to map relationships between the relevant managers."
    )
    lines.append(
        "Rule 3 COMPLETE ALL TOOL CALLS BEFORE ANSWERING: Call every tool"
        " you need before writing any answer text. Do not interleave text"
        " and tool calls. Never write a placeholder like [tool:query] in"
        " your answer."
    )
    lines.append(
        "Rule 4 TRUST RESULTS: When a query returns data successfully, use"
        " that data to answer. Do not retry the same question with"
        " different syntax."
    )
    lines.append(
        "Rule 5 FILTERS: Always use the flat filter keys listed in the"
        " schema, such as material__name or parts__material__name. Never"
        ' invent nested filter objects like {"material": {"name": "X"}}.'
    )
    lines.append(
        "Rule 6 NESTED FIELDS: Relation selections must be arrays:"
        ' {"parts": ["name"]}, {"material": ["name", "density"]}.'
    )
    lines.append(
        "Rule 7 SYNTAX: Pass a JSON object with the exact required keys."
        " Use exact manager names and relation names."
        " For query.fields, use strings for scalar fields and single-key"
        " objects for nested relation selections."
    )
    lines.extend(
        f"- Example tool call for {tool_name}: {json.dumps(example, sort_keys=True)}"
        for tool_name, example in TOOL_USAGE_EXAMPLES
    )
    lines.append("Exposed managers:")
    lines.extend(
        f"- {summary['manager']}: {summary['description']}"
        for summary in (index[name] for name in manager_names)
    )
    lines.append("Relationship graph:")
    if relationship_lines:
        lines.extend(f"- {line}" for line in relationship_lines)
    else:
        lines.append("- No exposed cross-manager paths available.")
    developer_prompt = str(get_chat_settings().get("system_prompt", "") or "").strip()
    if developer_prompt:
        lines.append("Developer instructions:")
        lines.append(developer_prompt)
    return "\n".join(lines)
