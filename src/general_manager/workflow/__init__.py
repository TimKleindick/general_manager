"""Workflow subsystem protocols, registries, and built-in backends."""

from general_manager.workflow.actions import (
    Action,
    ActionAlreadyRegisteredError,
    ActionExecutionError,
    ActionNotFoundError,
    ActionRegistry,
)
from general_manager.workflow.backend_registry import (
    configure_workflow_engine,
    configure_workflow_engine_from_settings,
    get_workflow_engine,
)
from general_manager.workflow.engine import (
    WorkflowCancelledError,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowExecution,
    WorkflowState,
)
from general_manager.workflow.event_registry import (
    DatabaseEventRegistry,
    EventRegistry,
    WorkflowEvent,
    WorkflowEventHandler,
    EventPredicate,
    DeadLetterHandler,
    RetryPredicate,
    configure_event_registry,
    configure_event_registry_from_settings,
    get_event_registry,
    publish_sync,
)
from general_manager.workflow.events import (
    manager_created_event,
    manager_deleted_event,
    manager_updated_event,
)
from general_manager.workflow.signal_bridge import (
    connect_workflow_signal_bridge,
    configure_workflow_signal_bridge_from_settings,
    disconnect_workflow_signal_bridge,
)

__all__ = [
    "Action",
    "ActionAlreadyRegisteredError",
    "ActionExecutionError",
    "ActionNotFoundError",
    "ActionRegistry",
    "DatabaseEventRegistry",
    "DeadLetterHandler",
    "EventPredicate",
    "EventRegistry",
    "RetryPredicate",
    "WorkflowCancelledError",
    "WorkflowDefinition",
    "WorkflowEngine",
    "WorkflowEvent",
    "WorkflowEventHandler",
    "WorkflowExecution",
    "WorkflowState",
    "configure_event_registry",
    "configure_event_registry_from_settings",
    "configure_workflow_engine",
    "configure_workflow_engine_from_settings",
    "configure_workflow_signal_bridge_from_settings",
    "connect_workflow_signal_bridge",
    "disconnect_workflow_signal_bridge",
    "get_event_registry",
    "get_workflow_engine",
    "manager_created_event",
    "manager_deleted_event",
    "manager_updated_event",
    "publish_sync",
]
