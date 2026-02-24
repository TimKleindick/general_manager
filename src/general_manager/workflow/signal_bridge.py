"""Bridge GeneralManager mutation signals into workflow events."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, TypedDict

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

_RESERVED_KEYS = {"creator_id", "history_comment", "ignore_permission", "signal"}


class _CommonEventKwargs(TypedDict):
    manager: str
    identification: Mapping[str, Any] | None
    source: str | None
    metadata: Mapping[str, Any] | None
    event_name: str
    event_id: str | None
    occurred_at: datetime | None


def _resolve_old_value_from_history(instance: GeneralManager, field_name: str) -> Any:
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
    instance: Any,
    action: str | None,
    old_relevant_values: Mapping[str, Any] | None,
    kwargs: Mapping[str, Any],
) -> WorkflowEvent | None:
    if not isinstance(instance, GeneralManager):
        return None

    relevant_fields = {
        key: value
        for key, value in kwargs.items()
        if key not in _RESERVED_KEYS and not key.startswith("_")
    }
    if action in {"create", "update"} and not relevant_fields:
        return None

    common_kwargs: _CommonEventKwargs = {
        "manager": instance.__class__.__name__,
        "identification": instance.identification,
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
    sender: Any,
    instance: Any = None,
    action: str | None = None,
    old_relevant_values: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    del sender
    event = _manager_change_to_event(
        instance=instance,
        action=action,
        old_relevant_values=old_relevant_values,
        kwargs=kwargs,
    )
    if event is None:
        return
    get_event_registry().publish(event)


def connect_workflow_signal_bridge(*, registry: EventRegistry | None = None) -> None:
    """
    Connect manager mutation signal bridging into workflow events.

    If a registry is provided, it becomes the active global registry.
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
    """Disconnect manager mutation signal bridging."""
    post_data_change.disconnect(dispatch_uid=_DISPATCH_UID)


def workflow_signal_bridge_enabled(django_settings: Any) -> bool:
    """Return True when workflow signal bridge is enabled in settings."""
    config = getattr(django_settings, _SETTINGS_KEY, {})
    if isinstance(config, Mapping):
        if _WORKFLOW_SIGNAL_BRIDGE_KEY in config:
            return bool(config[_WORKFLOW_SIGNAL_BRIDGE_KEY])
    return bool(getattr(django_settings, _WORKFLOW_SIGNAL_BRIDGE_KEY, False))


def configure_workflow_signal_bridge_from_settings(django_settings: Any) -> None:
    """Connect or disconnect bridge based on Django settings."""
    if workflow_signal_bridge_enabled(django_settings):
        connect_workflow_signal_bridge()
        return
    disconnect_workflow_signal_bridge()
