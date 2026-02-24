"""Helper constructors for common GeneralManager workflow events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4

from general_manager.workflow.event_registry import WorkflowEvent


def _event_id(value: str | None) -> str:
    return value or str(uuid4())


def _occurred_at(value: datetime | None) -> datetime:
    return value or datetime.now(UTC)


def _field_diffs(
    changes: Mapping[str, Any],
    old_values: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    old_values = old_values or {}
    return {
        field_name: {"old": old_values.get(field_name), "new": new_value}
        for field_name, new_value in changes.items()
    }


def manager_created_event(
    *,
    manager: str,
    values: Mapping[str, Any],
    identification: Mapping[str, Any] | None = None,
    event_name: str = "manager_created",
    event_id: str | None = None,
    source: str | None = None,
    occurred_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> WorkflowEvent:
    payload: dict[str, Any] = {"manager": manager, "values": dict(values)}
    if identification is not None:
        payload["identification"] = dict(identification)
    return WorkflowEvent(
        event_id=_event_id(event_id),
        event_type="general_manager.manager.created",
        event_name=event_name,
        payload=payload,
        source=source,
        occurred_at=_occurred_at(occurred_at),
        metadata=dict(metadata or {}),
    )


def manager_updated_event(
    *,
    manager: str,
    changes: Mapping[str, Any],
    old_values: Mapping[str, Any] | None = None,
    identification: Mapping[str, Any] | None = None,
    event_name: str = "manager_updated",
    event_id: str | None = None,
    source: str | None = None,
    occurred_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> WorkflowEvent:
    payload: dict[str, Any] = {
        "manager": manager,
        "changes": _field_diffs(changes, old_values),
    }
    if identification is not None:
        payload["identification"] = dict(identification)
    return WorkflowEvent(
        event_id=_event_id(event_id),
        event_type="general_manager.manager.updated",
        event_name=event_name,
        payload=payload,
        source=source,
        occurred_at=_occurred_at(occurred_at),
        metadata=dict(metadata or {}),
    )


def manager_deleted_event(
    *,
    manager: str,
    identification: Mapping[str, Any] | None = None,
    event_name: str = "manager_deleted",
    event_id: str | None = None,
    source: str | None = None,
    occurred_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> WorkflowEvent:
    payload: dict[str, Any] = {"manager": manager}
    if identification is not None:
        payload["identification"] = dict(identification)
    return WorkflowEvent(
        event_id=_event_id(event_id),
        event_type="general_manager.manager.deleted",
        event_name=event_name,
        payload=payload,
        source=source,
        occurred_at=_occurred_at(occurred_at),
        metadata=dict(metadata or {}),
    )
