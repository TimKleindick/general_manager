"""Stable, transport-neutral public events for planned chat turns."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from general_manager.chat.providers.base import TokenUsage


PLANNED_PUBLIC_MESSAGES = {
    "invalid_plan": "I could not prepare a safe plan for that request.",
    "manager_unresolved": "I could not resolve the required application data.",
    "dependency_blocked": "A required part of the request could not be completed.",
    "budget_exhausted": "The request reached its execution limit.",
    "deadline_exceeded": "The request reached its time limit.",
    "provider_failed": "The provider could not complete the request.",
    "synthesis_failed": "I could not produce a grounded answer from the available data.",
}


def _valid_reason(reason: object) -> str:
    if not isinstance(reason, str) or reason not in PLANNED_PUBLIC_MESSAGES:
        raise ValueError("planned chat reason must be a stable public reason.")  # noqa: TRY003
    return reason


def _usage(usage: TokenUsage) -> dict[str, int]:
    if not isinstance(usage, TokenUsage):
        raise TypeError("usage must be a TokenUsage.")  # noqa: TRY003
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


def _unresolved(items: Iterable[object]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in items:
        task_id: object
        reason: object
        if isinstance(item, Mapping):
            task_id, reason = item.get("task_id"), item.get("reason")
        elif isinstance(item, tuple) and len(item) == 2:
            task_id, reason = item
        else:
            raise TypeError("unresolved items must be task/reason pairs.")  # noqa: TRY003
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("unresolved task IDs must be non-empty strings.")  # noqa: TRY003
        result.append({"task_id": task_id, "reason": _valid_reason(reason)})
    return result


def planned_done_event(
    usage: TokenUsage,
    *,
    resolved: int,
    total: int,
    unresolved: Iterable[object],
) -> dict[str, Any]:
    """Build the sole terminal event for a grounded complete or partial turn."""
    if (
        isinstance(resolved, bool)
        or isinstance(total, bool)
        or not isinstance(resolved, int)
        or not isinstance(total, int)
        or resolved < 0
        or total < 0
        or resolved > total
    ):
        raise ValueError("planned coverage must satisfy 0 <= resolved <= total.")  # noqa: TRY003
    safe_unresolved = _unresolved(unresolved)
    return {
        "type": "done",
        "usage": _usage(usage),
        "orchestration": {
            "status": "complete" if resolved == total else "partial",
            "coverage": {"resolved": resolved, "total": total},
            "unresolved": safe_unresolved,
        },
    }


def planned_error_event(reason: str) -> dict[str, str]:
    """Build the sole terminal event when no grounded answer exists."""
    reason = _valid_reason(reason)
    return {"type": "error", "code": reason, "message": PLANNED_PUBLIC_MESSAGES[reason]}


def planned_tool_call_event(
    task_id: str, call_id: str, name: str, args: Mapping[str, Any]
) -> dict[str, Any]:
    """Build an actual planned tool-call event with its owning task ID."""
    return {
        "type": "tool_call",
        "task_id": task_id,
        "id": call_id,
        "name": name,
        "args": dict(args),
    }


def planned_tool_result_event(
    task_id: str, call_id: str, name: str, result: Any
) -> dict[str, Any]:
    """Build the matching actual planned tool-result event."""
    return {
        "type": "tool_result",
        "task_id": task_id,
        "id": call_id,
        "name": name,
        "result": result,
    }


__all__ = [
    "PLANNED_PUBLIC_MESSAGES",
    "planned_done_event",
    "planned_error_event",
    "planned_tool_call_event",
    "planned_tool_result_event",
]
