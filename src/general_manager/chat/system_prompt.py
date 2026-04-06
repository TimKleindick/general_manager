"""System prompt builder for chat."""

from __future__ import annotations

from general_manager.chat.schema_index import build_schema_index
from general_manager.chat.settings import get_chat_settings
from general_manager.utils.path_mapping import PathMap


TOOL_DESCRIPTIONS: dict[str, str] = {
    "search_managers": "Search exposed managers by text.",
    "get_manager_schema": "Inspect one manager's fields, relations, and filters.",
    "find_path": "Find a relationship traversal path between exposed managers.",
    "query": "Execute a structured read query via GraphQL.",
    "mutate": "Execute an allow-listed mutation via GraphQL.",
}


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
