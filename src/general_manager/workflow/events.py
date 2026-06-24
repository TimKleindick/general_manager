"""Helper constructors for common GeneralManager workflow events."""

from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import Mapping
from uuid import uuid4

from general_manager.workflow.event_registry import WorkflowEvent

type WorkflowPayloadValue = object
type WorkflowPayload = dict[str, WorkflowPayloadValue]


def _event_id(value: str | None) -> str:
    return value or str(uuid4())


def _occurred_at(value: datetime | None) -> datetime:
    return value or datetime.now(UTC)


def _field_diffs(
    changes: Mapping[str, WorkflowPayloadValue],
    old_values: Mapping[str, WorkflowPayloadValue] | None = None,
) -> dict[str, WorkflowPayload]:
    old_values = old_values or {}
    return {
        field_name: {"old": old_values.get(field_name), "new": new_value}
        for field_name, new_value in changes.items()
    }


def manager_created_event(
    *,
    manager: str,
    values: Mapping[str, WorkflowPayloadValue],
    identification: Mapping[str, WorkflowPayloadValue] | None = None,
    event_name: str = "manager_created",
    event_id: str | None = None,
    source: str | None = None,
    occurred_at: datetime | None = None,
    metadata: Mapping[str, WorkflowPayloadValue] | None = None,
) -> WorkflowEvent:
    """Build a workflow event for a manager create operation.

    `event_id` defaults to a UUID4 string and `occurred_at` defaults to the
    current UTC time. The payload always contains `manager` and a shallow copy of
    `values`; `identification` is included only when provided. Metadata is
    shallow-copied into the resulting `WorkflowEvent`.
    """
    payload: WorkflowPayload = {"manager": manager, "values": dict(values)}
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
    changes: Mapping[str, WorkflowPayloadValue],
    old_values: Mapping[str, WorkflowPayloadValue] | None = None,
    identification: Mapping[str, WorkflowPayloadValue] | None = None,
    event_name: str = "manager_updated",
    event_id: str | None = None,
    source: str | None = None,
    occurred_at: datetime | None = None,
    metadata: Mapping[str, WorkflowPayloadValue] | None = None,
) -> WorkflowEvent:
    """Build a workflow event for a manager update operation.

    The payload contains `manager` and `changes`, where each changed field maps
    to `{"old": old_values.get(field), "new": value}`. Missing old values are
    represented as `None`. `identification` is included only when provided.
    `event_id`, `occurred_at`, and metadata follow `manager_created_event`.
    """
    payload: WorkflowPayload = {
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
    identification: Mapping[str, WorkflowPayloadValue] | None = None,
    event_name: str = "manager_deleted",
    event_id: str | None = None,
    source: str | None = None,
    occurred_at: datetime | None = None,
    metadata: Mapping[str, WorkflowPayloadValue] | None = None,
) -> WorkflowEvent:
    """Build a workflow event for a manager delete operation.

    The payload always contains `manager` and includes `identification` only when
    provided. `event_id`, `occurred_at`, and metadata follow
    `manager_created_event`.
    """
    payload: WorkflowPayload = {"manager": manager}
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
