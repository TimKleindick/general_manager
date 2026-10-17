"""Shared terminal categories; stored authoritative flags always take precedence."""

from collections.abc import Iterable, Mapping
from typing import Any

from general_manager.chat.audit_identity import planned_audit_lineage_id


def terminal_failure_categories(
    terminal: Mapping[str, Any],
    *,
    legacy_deadline_transport: bool = False,
    terminal_origin: str | None = None,
    reason_origins: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Classify precise terminal reasons without inferring from answer content."""
    classes = {
        "budget_exhausted": "budget_exhausted",
        "deadline_exceeded": "transport_failure"
        if legacy_deadline_transport
        else "budget_exhausted",
        "provider_failed": "transport_failure",
        "rate_limited": "transport_failure",
        "chat_error": "harness_failure",
        "invalid_plan": "model_task_failure",
        "synthesis_failed": "model_task_failure",
        "manager_unresolved": "model_task_failure",
        "dependency_blocked": "model_task_failure",
    }
    reasons = (
        [(terminal.get("code"), terminal_origin)]
        if terminal.get("type") == "error"
        else []
    )
    reasons.extend(
        (item.get("reason"), (reason_origins or {}).get(item.get("task_id")))
        for item in terminal.get("orchestration", {}).get("unresolved", [])
    )
    result: dict[str, str] = {}
    for reason, origin in reasons:
        if not isinstance(reason, str) or reason not in classes:
            continue
        category = (
            "model_task_failure"
            if origin in {"model_declared_block", "scheduler_validation_cycle"}
            else classes[reason]
        )
        # A separately proven technical failure stays authoritative when another
        # task uses the same public code for a model-declared block.
        if result.get(reason) != classes[reason]:
            result[reason] = category
    return dict(sorted(result.items()))


def runtime_failure_provenance(
    terminal: Mapping[str, Any], audit_events: Iterable[Mapping[str, Any]], *, turn: int
) -> dict[str, Any]:
    """Project matching, turn-bound runtime audit origins, never answer text."""
    origins = {
        "model_declared_block",
        "provider_exception",
        "scheduler",
        "synthesizer",
        "scheduler_validation_cycle",
    }
    events = []
    for event in audit_events:
        if (
            type(event.get("turn")) is not int
            or event["turn"] != turn
            or event.get("category") != "audit"
            or event.get("event_type")
            not in {"planned_terminal", "planned_task_progress"}
            or event.get("reason_origin") not in origins
        ):
            continue
        events.append(event)
    proofs = []
    terminal_origin = None
    if terminal.get("type") == "error":
        matched = [
            event
            for event in events
            if event["event_type"] == "planned_terminal"
            and event.get("terminal_reason") == terminal.get("code")
        ]
        if matched:
            proofs.append(matched[-1])
            terminal_origin = matched[-1]["reason_origin"]
    reason_origins = {}
    for item in terminal.get("orchestration", {}).get("unresolved", []):
        task_id, reason = item.get("task_id"), item.get("reason")
        if not isinstance(task_id, str):
            continue
        audit_task_id = planned_audit_lineage_id(task_id)
        if audit_task_id is None:
            continue
        matched = [
            event
            for event in events
            if event["event_type"] == "planned_task_progress"
            and event.get("task_id") == audit_task_id
            and event.get("terminal_reason") == reason
        ]
        if matched:
            proofs.append(matched[-1])
            reason_origins[task_id] = matched[-1]["reason_origin"]
    return {
        "turn": turn,
        "terminal_origin": terminal_origin,
        "reason_origins": reason_origins,
        "audit_events": [
            {
                key: event[key]
                for key in (
                    "turn",
                    "category",
                    "event_type",
                    "task_id",
                    "terminal_reason",
                    "reason_origin",
                )
                if key in event
            }
            for event in proofs
        ],
    }
