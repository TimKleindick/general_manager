"""Bridge GeneralManager mutation signals into workflow events."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TypedDict, cast

from general_manager.cache.signals import post_data_change
from general_manager.manager.general_manager import GeneralManager
from general_manager.workflow.event_registry import (
    EventRegistry,
    WorkflowEvent,
    get_event_registry,
)
from general_manager.workflow.events import (
    manager_created_event,
    manager_deleted_event,
    manager_updated_event,
)

_SETTINGS_KEY = "GENERAL_MANAGER"
_WORKFLOW_SIGNAL_BRIDGE_KEY = "WORKFLOW_SIGNAL_BRIDGE"
_DISPATCH_UID = "general_manager_workflow_signal_bridge"

_RESERVED_KEYS = {
    "creator_id",
    "history_comment",
    "identification",
    "ignore_permission",
    "previous_instance",
    "signal",
}

type SignalPayloadValue = object
type SignalPayload = Mapping[str, SignalPayloadValue]
type SignalPayloadDict = dict[str, SignalPayloadValue]


class _CommonEventKwargs(TypedDict):
    manager: str
    identification: SignalPayload | None
    source: str | None
    metadata: SignalPayload | None
    event_name: str
    event_id: str | None
    occurred_at: datetime | None


def _resolve_old_value_from_history(
    instance: GeneralManager, field_name: str
) -> SignalPayloadValue:
    model_instance = getattr(instance._interface, "_instance", None)
    history = getattr(model_instance, "history", None)
    if history is None:
        return None
    try:
        rows = list(history.order_by("-history_date")[:2])
    except Exception:  # noqa: BLE001  # pragma: no cover - defensive fallback
        return None
    if len(rows) < 2:
        return None
    return getattr(rows[1], field_name, None)


def _manager_change_to_event(
    *,
    instance: object,
    action: str | None,
    old_relevant_values: SignalPayload | None,
    kwargs: SignalPayload,
) -> WorkflowEvent | None:
    """Convert a GeneralManager mutation signal payload into a workflow event.

    Unknown actions and unsupported instances return `None`. Event ids and
    timestamps are left to the manager event helper defaults.
    """
    if not isinstance(instance, GeneralManager):
        return None

    relevant_fields: SignalPayloadDict = {
        key: value
        for key, value in kwargs.items()
        if key not in _RESERVED_KEYS and not key.startswith("_")
    }
    if action in {"create", "update"} and not relevant_fields:
        return None

    identification = kwargs.get("identification")
    if identification is None:
        identification = instance.identification
    identification_mapping = cast(SignalPayload | None, identification)
    common_kwargs: _CommonEventKwargs = {
        "manager": instance.__class__.__name__,
        "identification": identification_mapping,
        "source": "general_manager.cache.signals.post_data_change",
        "metadata": {"action": action},
        "event_name": "manager_updated",
        "event_id": None,
        "occurred_at": None,
    }
    if action == "create":
        common_kwargs["event_name"] = "manager_created"
        return manager_created_event(values=relevant_fields, **common_kwargs)
    if action == "update":
        old_values = dict(old_relevant_values or {})
        for field_name in relevant_fields:
            if field_name not in old_values or old_values[field_name] is None:
                history_value = _resolve_old_value_from_history(instance, field_name)
                if history_value is not None:
                    old_values[field_name] = history_value
        return manager_updated_event(
            changes=relevant_fields,
            old_values=old_values,
            **common_kwargs,
        )
    if action == "delete":
        common_kwargs["event_name"] = "manager_deleted"
        return manager_deleted_event(**common_kwargs)
    return None


def _handle_post_data_change(
    sender: object,
    instance: object | None = None,
    action: str | None = None,
    old_relevant_values: SignalPayload | None = None,
    **kwargs: SignalPayloadValue,
) -> None:
    """Publish a workflow event for supported manager mutation signals.

    The Django signal sender is ignored. When `instance` is missing, the bridge
    uses `previous_instance` from the signal kwargs. Exceptions raised by
    `publish()` propagate to the signal caller.
    """
    del sender
    event_instance = (
        instance if instance is not None else kwargs.get("previous_instance")
    )
    event = _manager_change_to_event(
        instance=event_instance,
        action=action,
        old_relevant_values=old_relevant_values,
        kwargs=kwargs,
    )
    if event is None:
        return
    get_event_registry().publish(event)


def connect_workflow_signal_bridge(*, registry: EventRegistry | None = None) -> None:
    """Connect manager mutation signal bridging into workflow events.

    If `registry` is provided, it becomes the active global registry before the
    receiver is connected. The receiver is connected with a stable dispatch uid
    and `weak=False`, so repeated calls replace the same receiver registration.
    """
    if registry is not None:
        from general_manager.workflow.event_registry import configure_event_registry

        configure_event_registry(registry)
    post_data_change.connect(
        _handle_post_data_change,
        weak=False,
        dispatch_uid=_DISPATCH_UID,
    )


def disconnect_workflow_signal_bridge() -> None:
    """Disconnect the workflow signal bridge receiver by dispatch uid."""
    post_data_change.disconnect(dispatch_uid=_DISPATCH_UID)


def workflow_signal_bridge_enabled(django_settings: object) -> bool:
    """Return whether workflow signal bridge is enabled in settings.

    Nested `GENERAL_MANAGER["WORKFLOW_SIGNAL_BRIDGE"]` takes precedence over the
    top-level setting when `GENERAL_MANAGER` is a mapping. Values are interpreted
    with Python `bool(...)`; missing settings default to `False`.
    """
    config = getattr(django_settings, _SETTINGS_KEY, {})
    if isinstance(config, Mapping):
        if _WORKFLOW_SIGNAL_BRIDGE_KEY in config:
            return bool(config[_WORKFLOW_SIGNAL_BRIDGE_KEY])
    return bool(getattr(django_settings, _WORKFLOW_SIGNAL_BRIDGE_KEY, False))


def configure_workflow_signal_bridge_from_settings(django_settings: object) -> None:
    """Connect or disconnect the bridge based on Django settings."""
    if workflow_signal_bridge_enabled(django_settings):
        connect_workflow_signal_bridge()
        return
    disconnect_workflow_signal_bridge()
