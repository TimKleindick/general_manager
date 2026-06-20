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


def build_missing_tool_recovery_message(user_text: str) -> str:
    """Build a provider-facing correction for missing tool use."""
    return (
        "Do not answer from memory. Call the available tools before answering this "
        f"data question: {user_text}"
    )
