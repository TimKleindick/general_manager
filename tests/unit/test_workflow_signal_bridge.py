from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from general_manager.workflow.event_registry import InMemoryEventRegistry
from general_manager.workflow.signal_bridge import (
    _handle_post_data_change,
    connect_workflow_signal_bridge,
    configure_workflow_signal_bridge_from_settings,
    disconnect_workflow_signal_bridge,
    workflow_signal_bridge_enabled,
)


def test_post_change_records_bounded_workflow_latency() -> None:
    with (
        patch(
            "general_manager.workflow.signal_bridge.perf_counter",
            side_effect=(40.0, 41.25),
        ),
        patch(
            "general_manager.workflow.signal_bridge.record_data_change_phase",
        ) as record_phase,
    ):
        result = _handle_post_data_change(
            sender=object(),
            instance=None,
            action="update",
            database_alias="analytics",
        )

    assert result is None
    record_phase.assert_called_once_with("workflow", 1.25, "analytics")


def test_post_change_records_workflow_latency_without_suppressing_exceptions() -> None:
    failure = RuntimeError("event conversion failed")

    with (
        patch(
            "general_manager.workflow.signal_bridge.perf_counter",
            side_effect=(50.0, 50.5),
        ),
        patch(
            "general_manager.workflow.signal_bridge.record_data_change_phase",
        ) as record_phase,
        patch(
            "general_manager.workflow.signal_bridge._manager_change_to_event",
            side_effect=failure,
        ),
        pytest.raises(RuntimeError) as raised,
    ):
        _handle_post_data_change(
            sender=object(),
            instance=object(),
            action="update",
            database_alias="analytics",
        )

    assert raised.value is failure
    record_phase.assert_called_once_with("workflow", 0.5, "analytics")


def test_workflow_signal_bridge_enabled_prefers_nested_setting() -> None:
    django_settings = SimpleNamespace(
        GENERAL_MANAGER={"WORKFLOW_SIGNAL_BRIDGE": 0},
        WORKFLOW_SIGNAL_BRIDGE=True,
    )

    assert workflow_signal_bridge_enabled(django_settings) is False


def test_workflow_signal_bridge_enabled_uses_top_level_when_nested_missing() -> None:
    django_settings = SimpleNamespace(
        GENERAL_MANAGER={"OTHER": True},
        WORKFLOW_SIGNAL_BRIDGE="yes",
    )

    assert workflow_signal_bridge_enabled(django_settings) is True


def test_workflow_signal_bridge_enabled_ignores_non_mapping_general_manager() -> None:
    django_settings = SimpleNamespace(
        GENERAL_MANAGER=["not", "a", "mapping"],
        WORKFLOW_SIGNAL_BRIDGE=False,
    )

    assert workflow_signal_bridge_enabled(django_settings) is False


def test_connect_workflow_signal_bridge_configures_registry_and_connects_signal() -> (
    None
):
    registry = InMemoryEventRegistry()

    with (
        patch(
            "general_manager.workflow.event_registry.configure_event_registry"
        ) as configure_registry,
        patch("general_manager.workflow.signal_bridge.post_data_change") as signal,
    ):
        connect_workflow_signal_bridge(registry=registry)

    configure_registry.assert_called_once_with(registry)
    signal.connect.assert_called_once()
    _, kwargs = signal.connect.call_args
    assert kwargs["weak"] is False
    assert kwargs["dispatch_uid"] == "general_manager_workflow_signal_bridge"


def test_disconnect_workflow_signal_bridge_disconnects_by_dispatch_uid() -> None:
    with patch("general_manager.workflow.signal_bridge.post_data_change") as signal:
        disconnect_workflow_signal_bridge()

    signal.disconnect.assert_called_once_with(
        dispatch_uid="general_manager_workflow_signal_bridge"
    )


def test_configure_workflow_signal_bridge_from_settings_connects_or_disconnects() -> (
    None
):
    enabled_settings = SimpleNamespace(GENERAL_MANAGER={"WORKFLOW_SIGNAL_BRIDGE": True})
    disabled_settings = SimpleNamespace(
        GENERAL_MANAGER={"WORKFLOW_SIGNAL_BRIDGE": False}
    )

    with (
        patch(
            "general_manager.workflow.signal_bridge.connect_workflow_signal_bridge"
        ) as connect_bridge,
        patch(
            "general_manager.workflow.signal_bridge.disconnect_workflow_signal_bridge"
        ) as disconnect_bridge,
    ):
        configure_workflow_signal_bridge_from_settings(enabled_settings)
        configure_workflow_signal_bridge_from_settings(disabled_settings)

    connect_bridge.assert_called_once_with()
    disconnect_bridge.assert_called_once_with()
