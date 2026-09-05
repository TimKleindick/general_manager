from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from general_manager.workflow.event_registry import (
    DatabaseEventRegistry,
    DurableHandlerRegistrationIdRequiredError,
    InMemoryEventRegistry,
    InvalidDurableHandlerRegistrationIdError,
    WorkflowHandlerRegistrationConflictError,
    WorkflowEvent,
    WorkflowEventHandler,
)
from general_manager.workflow.signal_bridge import (
    _handle_post_data_change,
    connect_workflow_signal_bridge,
    configure_workflow_signal_bridge_from_settings,
    disconnect_workflow_signal_bridge,
    workflow_signal_bridge_enabled,
)


class _EqualCallable:
    def __init__(self, calls: list[str], label: str) -> None:
        self._calls = calls
        self._label = label

    def __call__(self, _event: WorkflowEvent) -> None:
        self._calls.append(self._label)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _EqualCallable)


class _BoundHandler:
    def __init__(self, calls: list[str], label: str) -> None:
        self._calls = calls
        self._label = label

    def handle(self, _event: WorkflowEvent) -> None:
        self._calls.append(self._label)


def _module_level_handler(_event: WorkflowEvent) -> None:
    pass


def _identity_handler(handler: WorkflowEventHandler) -> WorkflowEventHandler:
    return handler


_module_level_lambda = _identity_handler(lambda _event: None)


class _SpoofedBoundCallable:
    def __init__(self, calls: list[str], label: str, shared_self: object) -> None:
        self._calls = calls
        self._label = label
        self.__func__ = _module_level_handler
        self.__self__ = shared_self

    def __call__(self, _event: WorkflowEvent) -> None:
        self._calls.append(self._label)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _SpoofedBoundCallable)


def _make_closure_handler() -> WorkflowEventHandler:
    captured_event_ids: list[str] = []

    def handler(event: WorkflowEvent) -> None:
        captured_event_ids.append(event.event_id)

    return handler


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


def test_memory_registry_keeps_equal_but_distinct_callable_objects() -> None:
    calls: list[str] = []
    first = _EqualCallable(calls, "first")
    second = _EqualCallable(calls, "second")
    registry = InMemoryEventRegistry()

    registry.register("manager_updated", handler=first)
    registry.register("manager_updated", handler=second)

    assert registry.publish(
        WorkflowEvent(
            event_id="equal-callables",
            event_type="general_manager.manager.updated",
            event_name="manager_updated",
            payload={},
        )
    )
    assert calls == ["first", "second"]


def test_spoofed_bound_method_attributes_do_not_change_callable_identity() -> None:
    calls: list[str] = []
    shared_self = object()
    first = _SpoofedBoundCallable(calls, "first", shared_self)
    second = _SpoofedBoundCallable(calls, "second", shared_self)
    memory_registry = InMemoryEventRegistry()

    memory_registry.register("manager_updated", handler=first)
    memory_registry.register("manager_updated", handler=second)

    assert memory_registry.publish(
        WorkflowEvent(
            event_id="spoofed-bound-methods",
            event_type="general_manager.manager.updated",
            event_name="manager_updated",
            payload={},
        )
    )
    assert calls == ["first", "second"]

    database_registry = DatabaseEventRegistry()
    database_registry.register(
        "manager_updated",
        handler=first,
        registration_id="spoofed-bound-method",
    )
    with pytest.raises(WorkflowHandlerRegistrationConflictError):
        database_registry.register(
            "manager_updated",
            handler=second,
            registration_id="spoofed-bound-method",
        )


def test_database_registry_requires_stable_id_for_dynamic_registration_callables() -> (
    None
):
    registry = DatabaseEventRegistry()

    with pytest.raises(DurableHandlerRegistrationIdRequiredError):
        registry.register("manager_updated", handler=_BoundHandler([], "first").handle)

    with pytest.raises(DurableHandlerRegistrationIdRequiredError):
        registry.register("manager_updated", handler=_make_closure_handler())

    with pytest.raises(DurableHandlerRegistrationIdRequiredError):
        registry.register(
            "manager_updated",
            handler=_module_level_handler,
            when=lambda _event: True,
        )

    with pytest.raises(DurableHandlerRegistrationIdRequiredError):
        registry.register("manager_updated", handler=_module_level_lambda)


@pytest.mark.parametrize("registration_id", ["", "   ", "x" * 256, 1])
def test_database_registry_rejects_invalid_explicit_registration_id(
    registration_id: object,
) -> None:
    with pytest.raises(InvalidDurableHandlerRegistrationIdError):
        DatabaseEventRegistry().register(
            "manager_updated",
            handler=_make_closure_handler(),
            registration_id=registration_id,  # type: ignore[arg-type]
        )


def test_durable_registration_id_is_idempotent_only_for_identical_configuration() -> (
    None
):
    registry = DatabaseEventRegistry()
    handler = _BoundHandler([], "first")

    registry.register(
        "manager_updated",
        handler=handler.handle,
        registration_id="project-status-workflow",
    )
    registry.register(
        "manager_updated",
        handler=handler.handle,
        registration_id="project-status-workflow",
    )

    assert (
        len(
            registry._get_entries(
                WorkflowEvent(
                    event_id="idempotent-registration",
                    event_type="general_manager.manager.updated",
                    event_name="manager_updated",
                    payload={},
                )
            )
        )
        == 1
    )

    with pytest.raises(WorkflowHandlerRegistrationConflictError):
        registry.register(
            "general_manager.manager.deleted",
            handler=handler.handle,
            registration_id="project-status-workflow",
        )

    with pytest.raises(WorkflowHandlerRegistrationConflictError):
        registry.register(
            "manager_updated",
            handler=handler.handle,
            registration_id="project-status-workflow",
            retries=1,
        )


def test_database_registry_uses_module_level_handler_default_across_registries() -> (
    None
):
    event = WorkflowEvent(
        event_id="module-level-handler",
        event_type="general_manager.manager.updated",
        event_name="manager_updated",
        payload={},
    )
    first_registry = DatabaseEventRegistry()
    first_registry.register("manager_updated", handler=_module_level_handler)
    fresh_registry = DatabaseEventRegistry()
    fresh_registry.register("manager_updated", handler=_module_level_handler)

    assert (
        first_registry._get_entries(event)[0].registration_id
        == fresh_registry._get_entries(event)[0].registration_id
    )


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
