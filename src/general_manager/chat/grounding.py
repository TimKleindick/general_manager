"""Conservative grounding checks for chat tool use."""

from __future__ import annotations

from typing import Any

DATA_QUESTION_MARKERS = (
    "which ",
    "list ",
    "show ",
    "find ",
    "how many ",
    "what materials",
    "what parts",
    "what projects",
    "density",
    "manager",
    "records",
)

METADATA_TOOL_NAMES = frozenset(
    {
        "search_managers",
        "get_manager_schema",
        "find_path",
    }
)


def should_recover_missing_tool_call(
    *,
    user_text: str,
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
) -> bool:
    """Return true when a likely data question received text without any tool call."""
    if tool_calls:
        return False
    if not assistant_text.strip():
        return False
    normalized = user_text.strip().lower()
    return any(marker in normalized for marker in DATA_QUESTION_MARKERS)


def should_recover_answer_without_query(
    *,
    user_text: str,
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
) -> bool:
    """Return true when a data answer used metadata tools but no data query."""
    if not tool_calls:
        return False
    if any(call.get("name") == "query" for call in tool_calls):
        return False
    if not any(call.get("name") in METADATA_TOOL_NAMES for call in tool_calls):
        return False
    if not assistant_text.strip():
        return False
    normalized = user_text.strip().lower()
    return any(marker in normalized for marker in DATA_QUESTION_MARKERS)


def build_missing_tool_recovery_message(user_text: str) -> str:
    """Build a provider-facing correction for missing tool use."""
    return (
        "Do not answer from memory. Call the available tools before answering this "
        f"data question: {user_text}"
    )


def build_empty_response_recovery_message(user_text: str) -> str:
    """Build a provider-facing correction for an empty response after tool use."""
    return (
        "The previous tool result is not a final answer. If the user asked for "
        "application rows, call query with the discovered manager and path. "
        f"Otherwise answer from the tool result. User question: {user_text}"
    )


def build_query_required_recovery_message(user_text: str) -> str:
    """Build a correction for data answers based only on schema/path tools."""
    return (
        "Schema and path tools are not data queries. Call query before answering "
        f"with application records for this question: {user_text}"
    )
