"""Privacy contracts for planned-chat diagnostics and terminal errors."""

from __future__ import annotations

import hashlib
import json

import pytest
from django.test.utils import override_settings

from general_manager.chat.audit import emit_planned_audit_event
from general_manager.chat.errors import PLANNED_PUBLIC_MESSAGES, planned_public_error


@pytest.fixture
def audit_sink() -> list[dict[str, object]]:
    return []


@override_settings(
    GENERAL_MANAGER={
        "CHAT": {
            "audit": {
                "enabled": True,
                "level": "tool_calls",
                "redact_fields": ["secret"],
                "max_result_size": 16,
            }
        }
    }
)
def test_planned_diagnostics_allow_only_safe_progress_fields(
    audit_sink: list[dict[str, object]],
) -> None:
    """Removing the allowlist must not leak private turn state to the sink."""
    canonical_identity = '{"args":{"api_token":"secret"},"name":"query"}'

    emit_planned_audit_event(
        "task_progress",
        {
            "plan_id": "plan_7",
            "task_id": "task_2",
            "root_task_id": "task_1",
            "parent_task_id": "task_1",
            "role": "complex_executor",
            "profile": "strong_local",
            "trust_group": "local",
            "match_sources": ["catalog_alias", "schema_field"],
            "canonical_call_identity": canonical_identity,
            "duplicate": False,
            "progress": "evidence_added",
            "local_passes": 3,
            "subtree_rounds_used": 2,
            "subtree_rounds_remaining": 13,
            "global_rounds_used": 4,
            "global_rounds_remaining": 27,
            "stage_latency_ms": 125,
            "task_latency_ms": 82,
            "input_tokens": 12,
            "output_tokens": 8,
            "reported_cost": "0.003",
            "evidence_counts": {"query": 1, "schema": 0},
            "coverage": {"resolved": 1, "total": 2},
            "terminal_reason": "deadline_exceeded",
            "result": {"secret": "must not reach the audit sink", "rows": [1] * 30},
            "hidden_manager": "HiddenManager",
            "raw_plan": {"objective": "not logged"},
            "exception": "Traceback: private provider detail",
        },
        sink=audit_sink.append,
    )

    assert audit_sink == [
        {
            "event_type": "planned_task_progress",
            "plan_id": hashlib.sha256(b"plan_7").hexdigest(),
            "task_id": hashlib.sha256(b"task_2").hexdigest(),
            "root_task_id": hashlib.sha256(b"task_1").hexdigest(),
            "parent_task_id": hashlib.sha256(b"task_1").hexdigest(),
            "role": "complex_executor",
            "match_sources": ["catalog_alias", "schema_field"],
            "call_hash": hashlib.sha256(canonical_identity.encode()).hexdigest(),
            "duplicate": False,
            "progress": "evidence_added",
            "local_passes": 3,
            "subtree_rounds_used": 2,
            "subtree_rounds_remaining": 13,
            "global_rounds_used": 4,
            "global_rounds_remaining": 27,
            "stage_latency_ms": 125,
            "task_latency_ms": 82,
            "input_tokens": 12,
            "output_tokens": 8,
            "reported_cost": "0.003",
            "evidence_counts": {"query": 1, "schema": 0},
            "coverage": {"resolved": 1, "total": 2},
            "terminal_reason": "deadline_exceeded",
        }
    ]
    serialized = json.dumps(audit_sink)
    for marker in (
        "api_token",
        "must not reach",
        "HiddenManager",
        "strong_local",
        "trust_group",
        "raw_plan",
        "Traceback",
        canonical_identity,
        "plan_7",
        "task_2",
        "task_1",
    ):
        assert marker not in serialized


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
def test_planned_error_reasons_are_stable_and_sanitized(reason: str) -> None:
    """Changing a planned reason must retain its public-safe terminal event."""
    assert planned_public_error(reason).as_event() == {
        "type": "error",
        "code": reason,
        "message": PLANNED_PUBLIC_MESSAGES[reason],
    }


def test_unexpected_errors_keep_the_generic_public_mapping() -> None:
    """An arbitrary implementation error must not acquire a planned public code."""
    assert planned_public_error("private_provider_exception").as_event() == {
        "type": "error",
        "code": "chat_error",
        "message": "Chat request failed.",
    }


@override_settings(
    GENERAL_MANAGER={"CHAT": {"audit": {"enabled": True, "level": "tool_calls"}}}
)
def test_planned_diagnostics_omit_incomplete_coverage_mapping(
    audit_sink: list[dict[str, object]],
) -> None:
    """An incomplete coverage mapping must be discarded, not crash auditing."""
    emit_planned_audit_event(
        "coverage", {"coverage": {"resolved": 1}}, sink=audit_sink.append
    )

    assert audit_sink == [{"event_type": "planned_coverage"}]
