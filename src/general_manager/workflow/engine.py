"""Workflow engine protocol and shared execution models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol, runtime_checkable, Literal


WorkflowState = Literal[
    "pending",
    "running",
    "waiting",
    "failed",
    "cancelled",
    "completed",
]

ACTIVE_WORKFLOW_STATES: tuple[WorkflowState, ...] = ("pending", "running", "waiting")
TERMINAL_WORKFLOW_STATES: tuple[WorkflowState, ...] = (
    "failed",
    "cancelled",
    "completed",
)


WorkflowHandler = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]


@dataclass(frozen=True)
class WorkflowDefinition:
    """Workflow declaration metadata."""

    workflow_id: str
    version: str = "1"
    description: str | None = None
    handler: WorkflowHandler | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowExecution:
    """Execution record returned by workflow engines."""

    execution_id: str
    workflow_id: str
    state: WorkflowState
    input_data: Mapping[str, Any] = field(default_factory=dict)
    output_data: Mapping[str, Any] | None = None
    correlation_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class WorkflowEngine(Protocol):
    """Protocol for workflow orchestration backends."""

    def start(
        self,
        workflow: WorkflowDefinition,
        input_data: Mapping[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkflowExecution:
        """Start a workflow execution."""

    def resume(
        self,
        execution_id: str,
        signal: Mapping[str, Any] | None = None,
    ) -> WorkflowExecution:
        """Resume a waiting execution with an external signal."""

    def cancel(
        self, execution_id: str, *, reason: str | None = None
    ) -> WorkflowExecution:
        """Cancel an active workflow execution."""

    def status(self, execution_id: str) -> WorkflowExecution:
        """Return current workflow execution status."""


class WorkflowEngineError(RuntimeError):
    """Base class for workflow engine failures."""


class WorkflowExecutionNotFoundError(WorkflowEngineError):
    """Raised when an execution id cannot be resolved."""

    def __init__(self, execution_id: str) -> None:
        super().__init__(f"Workflow execution '{execution_id}' was not found.")


class WorkflowCancelledError(WorkflowEngineError):
    """Raised when an operation targets a cancelled workflow."""

    def __init__(self, execution_id: str) -> None:
        super().__init__(f"Workflow execution '{execution_id}' is cancelled.")


class WorkflowInvalidStateError(WorkflowEngineError):
    """Raised when an operation is not valid for the current workflow state."""

    def __init__(
        self,
        execution_id: str,
        *,
        operation: str,
        state: WorkflowState,
        expected_states: tuple[WorkflowState, ...],
    ) -> None:
        expected = ", ".join(expected_states)
        super().__init__(
            f"Workflow execution '{execution_id}' cannot {operation} from state "
            f"'{state}'. Expected one of: {expected}."
        )
