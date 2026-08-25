"""Audit helpers for chat interactions."""

from __future__ import annotations

from contextlib import suppress
import hashlib
import json
import math
from typing import Any

from django.utils.module_loading import import_string

from general_manager.chat.settings import get_chat_settings


_PLANNED_ROLES = frozenset(
    (
        "planner",
        "simple_executor",
        "complex_executor",
        "synthesizer",
        "fallback_executor",
    )
)
_PLANNED_MATCH_SOURCES = frozenset(
    (
        "exact_name",
        "exact_alias",
        "catalog_domain",
        "catalog_alias",
        "catalog_use_when",
        "schema_description",
        "schema_field",
        "schema_filter",
        "schema_relation",
    )
)
_PLANNED_PROGRESS = frozenset(
    (
        "candidate_changed",
        "duplicate_rejected",
        "evidence_added",
        "no_progress",
        "task_blocked",
        "task_resolved",
    )
)
_PLANNED_EVENT_FIELDS = {
    "plan": frozenset(("plan_id", "task_count", "role", "trust_group_valid")),
    "task_progress": frozenset(
        (
            "plan_id",
            "task_id",
            "root_task_id",
            "parent_task_id",
            "role",
            "match_sources",
            "canonical_call_identity",
            "call_hash",
            "duplicate",
            "progress",
            "local_passes",
            "subtree_rounds_used",
            "subtree_rounds_remaining",
            "global_rounds_used",
            "global_rounds_remaining",
            "stage_latency_ms",
            "task_latency_ms",
            "input_tokens",
            "output_tokens",
            "reported_cost",
            "evidence_counts",
            "coverage",
            "terminal_reason",
        )
    ),
    "route": frozenset(
        (
            "plan_id",
            "task_id",
            "root_task_id",
            "role",
            "route",
            "escalated",
            "trust_group_valid",
        )
    ),
    "candidate": frozenset(
        (
            "plan_id",
            "task_id",
            "match_sources",
            "candidate_count",
            "local_passes",
        )
    ),
    "tool_call": frozenset(
        (
            "plan_id",
            "task_id",
            "canonical_call_identity",
            "call_hash",
            "duplicate",
        )
    ),
    "tool_result": frozenset(
        (
            "plan_id",
            "task_id",
            "call_hash",
            "duplicate",
            "progress",
            "evidence_counts",
        )
    ),
    "budget": frozenset(
        (
            "plan_id",
            "task_id",
            "subtree_rounds_used",
            "subtree_rounds_remaining",
            "global_rounds_used",
            "global_rounds_remaining",
        )
    ),
    "latency": frozenset(
        ("plan_id", "task_id", "stage", "stage_latency_ms", "task_latency_ms")
    ),
    "usage": frozenset(
        ("plan_id", "task_id", "role", "input_tokens", "output_tokens", "reported_cost")
    ),
    "evidence": frozenset(("plan_id", "task_id", "evidence_counts", "progress")),
    "coverage": frozenset(("plan_id", "coverage", "terminal_reason")),
    "terminal": frozenset(("plan_id", "coverage", "terminal_reason")),
}
_PLANNED_REASONS = frozenset(
    (
        "invalid_plan",
        "manager_unresolved",
        "dependency_blocked",
        "budget_exhausted",
        "deadline_exceeded",
        "provider_failed",
        "synthesis_failed",
    )
)


def _should_emit(event_type: str, level: str) -> bool:
    if level == "off":
        return False
    if level == "messages":
        return event_type in {"user_message", "assistant_message"}
    return True


def _redact(value: Any, redact_fields: set[str]) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(field in lowered for field in redact_fields):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact(item, redact_fields)
        return redacted
    if isinstance(value, list):
        return [_redact(item, redact_fields) for item in value]
    return value


def _truncate_result(value: Any, max_result_size: int) -> str:
    serialized = json.dumps(value, sort_keys=True)
    if len(serialized) <= max_result_size:
        return serialized
    return f"{serialized[:max_result_size]}..."


def _resolve_sink() -> Any:
    audit_settings = get_chat_settings()["audit"]
    logger_path = audit_settings.get("logger")
    if logger_path is None:
        return None
    if callable(logger_path):
        return logger_path
    return import_string(str(logger_path))


def emit_chat_audit_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    sink: Any | None = None,
) -> None:
    """Emit one sanitized chat audit event when chat audit logging is enabled."""
    audit_settings = get_chat_settings()["audit"]
    if not audit_settings.get("enabled"):
        return
    level = str(audit_settings.get("level", "off"))
    if not _should_emit(event_type, level):
        return

    redact_fields = {
        str(field).lower() for field in audit_settings.get("redact_fields", [])
    }
    sanitized = _redact(payload, redact_fields)
    if "result" in sanitized:
        sanitized["result"] = _truncate_result(
            sanitized["result"],
            int(audit_settings.get("max_result_size", 4096)),
        )
    event = {"event_type": event_type, **sanitized}

    with suppress(Exception):
        target = sink if sink is not None else _resolve_sink()
        if target is None:
            return
        if callable(target):
            target(event)


def _planned_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _planned_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    return value


def planned_audit_lineage_id(value: object) -> str | None:
    """Return a deterministic opaque audit identifier for planner-controlled IDs."""
    identifier = _planned_identifier(value)
    if identifier is None:
        return None
    return hashlib.sha256(identifier.encode()).hexdigest()


def _planned_count_mapping(value: object, allowed: set[str]) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    safe: dict[str, int] = {}
    for key, item in value.items():
        if key not in allowed or (number := _planned_nonnegative_int(item)) is None:
            return None
        safe[key] = number
    return safe


def _planned_cost(value: object) -> str | float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) and value >= 0 else None
    if isinstance(value, str) and len(value) <= 32:
        try:
            parsed = float(value)
        except ValueError:
            return None
        return value if math.isfinite(parsed) and parsed >= 0 else None
    return None


def _sanitize_planned_audit_payload(
    event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Return the category-specific planned audit allowlist, never a copy."""
    allowed = _PLANNED_EVENT_FIELDS.get(event_type)
    if allowed is None:
        return {}
    sanitized: dict[str, Any] = {}
    for key in ("plan_id", "task_id", "root_task_id", "parent_task_id"):
        if (
            key in allowed
            and (value := planned_audit_lineage_id(payload.get(key))) is not None
        ):
            sanitized[key] = value
    if "role" in allowed and payload.get("role") in _PLANNED_ROLES:
        sanitized["role"] = payload["role"]
    if "route" in allowed and payload.get("route") in {"selected", "escalated"}:
        sanitized["route"] = payload["route"]
    for key in ("duplicate", "escalated", "trust_group_valid"):
        if key in allowed and isinstance(payload.get(key), bool):
            sanitized[key] = payload[key]
    if "match_sources" in allowed and isinstance(payload.get("match_sources"), list):
        sources = payload["match_sources"]
        if all(
            isinstance(item, str) and item in _PLANNED_MATCH_SOURCES for item in sources
        ):
            sanitized["match_sources"] = list(sources)
    if "canonical_call_identity" in allowed and isinstance(
        payload.get("canonical_call_identity"), str
    ):
        sanitized["call_hash"] = hashlib.sha256(
            payload["canonical_call_identity"].encode()
        ).hexdigest()
    elif "call_hash" in allowed and isinstance(payload.get("call_hash"), str):
        call_hash = payload["call_hash"]
        if len(call_hash) == 64 and all(
            char in "0123456789abcdef" for char in call_hash
        ):
            sanitized["call_hash"] = call_hash
    if "progress" in allowed and payload.get("progress") in _PLANNED_PROGRESS:
        sanitized["progress"] = payload["progress"]
    for key in (
        "task_count",
        "candidate_count",
        "local_passes",
        "subtree_rounds_used",
        "subtree_rounds_remaining",
        "global_rounds_used",
        "global_rounds_remaining",
        "stage_latency_ms",
        "task_latency_ms",
        "input_tokens",
        "output_tokens",
    ):
        if (
            key in allowed
            and (number := _planned_nonnegative_int(payload.get(key))) is not None
        ):
            sanitized[key] = number
    if "stage" in allowed and payload.get("stage") in {
        "planning",
        "evidence",
        "synthesis",
    }:
        sanitized["stage"] = payload["stage"]
    if (
        "reported_cost" in allowed
        and (cost := _planned_cost(payload.get("reported_cost"))) is not None
    ):
        sanitized["reported_cost"] = cost
    if (
        "evidence_counts" in allowed
        and (
            evidence_counts := _planned_count_mapping(
                payload.get("evidence_counts"),
                {"schema", "path", "query", "calculation"},
            )
        )
        is not None
    ):
        sanitized["evidence_counts"] = evidence_counts
    if (
        "coverage" in allowed
        and (
            coverage := _planned_count_mapping(
                payload.get("coverage"), {"resolved", "total"}
            )
        )
        is not None
        and set(coverage) == {"resolved", "total"}
        and coverage["resolved"] <= coverage["total"]
    ):
        sanitized["coverage"] = coverage
    if (
        "terminal_reason" in allowed
        and payload.get("terminal_reason") in _PLANNED_REASONS
    ):
        sanitized["terminal_reason"] = payload["terminal_reason"]
    return sanitized


def emit_planned_audit_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    sink: Any | None = None,
) -> None:
    """Emit only category-approved planned diagnostics through the generic sink."""
    if event_type not in _PLANNED_EVENT_FIELDS:
        return
    with suppress(Exception):
        emit_chat_audit_event(
            f"planned_{event_type}",
            _sanitize_planned_audit_payload(event_type, payload),
            sink=sink,
        )
