"""Workflow engine protocol and shared execution models."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable


WorkflowState = Literal[
    "pending",
    "running",
    "waiting",
    "failed",
    "cancelled",
    "completed",
]

ACTIVE_WORKFLOW_STATES: tuple[WorkflowState, ...] = ("pending", "running", "waiting")
ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES: tuple[WorkflowState, ...] = (
    *ACTIVE_WORKFLOW_STATES,
    "completed",
)
TERMINAL_WORKFLOW_STATES: tuple[WorkflowState, ...] = (
    "failed",
    "cancelled",
    "completed",
)


type WorkflowPayload = Mapping[str, object]
type WorkflowHandler = Callable[[WorkflowPayload], WorkflowPayload | None]


@dataclass(frozen=True)
class WorkflowDefinition:
    """Workflow declaration metadata.

    `workflow_id` is the durable backend identity. `handler` receives a workflow
    input mapping and may return an output mapping or `None`. Metadata is stored
    as provided by callers; the dataclass is frozen but does not deep-freeze
    mapping contents. The dataclass performs no custom runtime validation.
    """

    workflow_id: str
    version: str = "1"
    description: str | None = None
    handler: WorkflowHandler | None = None
    metadata: WorkflowPayload = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowExecution:
    """Execution snapshot returned by workflow engines.

    `state` is typed as one of the `WorkflowState` literals for static checking.
    The dataclass performs no custom runtime validation. `input_data`,
    `output_data`, and `metadata` are stored exactly as provided by the backend;
    the dataclass is frozen but nested mapping contents are not copied or
    deep-frozen.
    """

    execution_id: str
    workflow_id: str
    state: WorkflowState
    input_data: WorkflowPayload = field(default_factory=dict)
    output_data: WorkflowPayload | None = None
    correlation_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None
    metadata: WorkflowPayload = field(default_factory=dict)


@runtime_checkable
class WorkflowEngine(Protocol):
    """Protocol for workflow orchestration backends.

    Implementations define their own persistence, copying, handler execution,
    retry, and error-capture behavior, but they return `WorkflowExecution`
    snapshots and use the shared workflow state vocabulary.
    """

    def start(
        self,
        workflow: WorkflowDefinition,
        input_data: WorkflowPayload | None = None,
        *,
        correlation_id: str | None = None,
        metadata: WorkflowPayload | None = None,
    ) -> WorkflowExecution:
        """Start a workflow execution and return a snapshot."""

    def resume(
        self,
        execution_id: str,
        signal: WorkflowPayload | None = None,
    ) -> WorkflowExecution:
        """Resume a waiting execution with an optional external signal."""

    def cancel(
        self, execution_id: str, *, reason: str | None = None
    ) -> WorkflowExecution:
        """Cancel an active workflow execution."""

    def status(self, execution_id: str) -> WorkflowExecution:
        """Return current workflow execution status."""


class WorkflowEngineError(RuntimeError):
    """Base class for workflow engine failures."""


class WorkflowExecutionNotFoundError(WorkflowEngineError):
    """Raised when an execution id cannot be resolved.

    The error message is `Workflow execution '<id>' was not found.`.
    """

    def __init__(self, execution_id: str) -> None:
        super().__init__(f"Workflow execution '{execution_id}' was not found.")


class WorkflowCancelledError(WorkflowEngineError):
    """Raised when an operation targets a cancelled workflow.

    The error message is `Workflow execution '<id>' is cancelled.`.
    """

    def __init__(self, execution_id: str) -> None:
        super().__init__(f"Workflow execution '{execution_id}' is cancelled.")


class WorkflowInvalidStateError(WorkflowEngineError):
    """Raised when an operation is not valid for the current workflow state.

    The error message includes the execution id, attempted operation, current
    state, and a comma-joined expected state list in the format
    `Workflow execution '<id>' cannot <operation> from state '<state>'. Expected
    one of: <states>.`.
    """

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
