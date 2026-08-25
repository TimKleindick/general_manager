"""Public event contracts for planned chat orchestration."""

from __future__ import annotations

import json

import pytest

from general_manager.chat.planned.events import (
    planned_done_event,
    planned_error_event,
    planned_tool_call_event,
    planned_tool_result_event,
)
from general_manager.chat.providers.base import TokenUsage


def test_partial_done_event_contains_coverage_without_private_data() -> None:
    event = planned_done_event(
        TokenUsage(input_tokens=2, output_tokens=3),
        resolved=2,
        total=3,
        unresolved=[("task_3", "deadline_exceeded")],
    )

    assert event == {
        "type": "done",
        "usage": {"input_tokens": 2, "output_tokens": 3},
        "orchestration": {
            "status": "partial",
            "coverage": {"resolved": 2, "total": 3},
            "unresolved": [{"task_id": "task_3", "reason": "deadline_exceeded"}],
        },
    }
    assert "profile" not in json.dumps(event)


def test_complete_done_event_omits_unresolved_tasks() -> None:
    event = planned_done_event(TokenUsage(), resolved=1, total=1, unresolved=[])

    assert event["orchestration"] == {
        "status": "complete",
        "coverage": {"resolved": 1, "total": 1},
        "unresolved": [],
    }


@pytest.mark.parametrize(
    "reason",
    (
        "invalid_plan",
        "manager_unresolved",
        "dependency_blocked",
        "budget_exhausted",
        "deadline_exceeded",
        "provider_failed",
        "synthesis_failed",
    ),
)
def test_error_event_uses_only_stable_reason(reason: str) -> None:
    event = planned_error_event(reason)

    assert event["type"] == "error"
    assert event["code"] == reason
    assert isinstance(event["message"], str)


def test_tool_event_builders_add_task_id_and_preserve_legacy_fields() -> None:
    call = planned_tool_call_event("task_1", "call-1", "query", {"limit": 1})
    result = planned_tool_result_event(
        "task_1", "call-1", "query", {"status": "success", "data": []}
    )

    assert call == {
        "type": "tool_call",
        "task_id": "task_1",
        "id": "call-1",
        "name": "query",
        "args": {"limit": 1},
    }
    assert result["type"] == "tool_result"
    assert result["task_id"] == call["task_id"]
    assert result["id"] == call["id"]
