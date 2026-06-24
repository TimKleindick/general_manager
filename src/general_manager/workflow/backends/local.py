"""Default local workflow engine backend."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import Event, Lock
from collections.abc import Mapping
from uuid import uuid4

from general_manager.workflow.engine import (
    ACTIVE_WORKFLOW_STATES,
    WorkflowCancelledError,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowInvalidStateError,
    WorkflowExecutionNotFoundError,
    WorkflowState,
)

type WorkflowPayload = Mapping[str, object]


class LocalWorkflowEngine:
    """Process-local workflow engine for development and tests.

    Executions are stored in memory, so state is lost when the process exits.
    Input data, metadata, handler input, and resume signals are deep-copied at
    the engine boundary to isolate stored snapshots from caller mutation.
    """

    def __init__(self) -> None:
        self._executions: dict[str, WorkflowExecution] = {}
        self._correlation_index: dict[tuple[str, str], str] = {}
        self._correlation_events: dict[tuple[str, str], Event] = {}
        self._lock = Lock()

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC)

    def start(
        self,
        workflow: WorkflowDefinition,
        input_data: WorkflowPayload | None = None,
        *,
        correlation_id: str | None = None,
        metadata: WorkflowPayload | None = None,
    ) -> WorkflowExecution:
        """Start `workflow` and return an execution snapshot.

        Workflows without handlers complete with an empty output mapping.
        Handler exceptions are captured as failed executions. A non-empty
        `correlation_id` reuses an existing local execution for the same workflow
        id, including failed executions. Concurrent starts for the same
        correlation key wait for the in-flight start to finish and then return
        the completed or failed snapshot.
        """
        started_at = self._utcnow()
        execution_id = str(uuid4())
        source_input = {} if input_data is None else input_data
        reserved_input = deepcopy(source_input)
        reserved_metadata = deepcopy({} if metadata is None else metadata)
        wait_for_execution: Event | None = None
        if correlation_id:
            correlation_key = (workflow.workflow_id, correlation_id)
            with self._lock:
                existing_id = self._correlation_index.get(correlation_key)
                if existing_id is not None:
                    existing = self._executions.get(existing_id)
                    if existing is not None:
                        if (
                            existing.state != "pending"
                            or existing.output_data is not None
                        ):
                            return existing
                    wait_for_execution = self._correlation_events.get(correlation_key)
                else:
                    placeholder = WorkflowExecution(
                        execution_id=execution_id,
                        workflow_id=workflow.workflow_id,
                        state="pending",
                        input_data=reserved_input,
                        output_data=None,
                        correlation_id=correlation_id,
                        started_at=started_at,
                        ended_at=None,
                        error=None,
                        metadata=reserved_metadata,
                    )
                    self._correlation_index[correlation_key] = execution_id
                    self._executions[execution_id] = placeholder
                    self._correlation_events[correlation_key] = Event()
            if wait_for_execution is not None:
                wait_for_execution.wait()
                with self._lock:
                    existing_id = self._correlation_index.get(correlation_key)
                    if existing_id is None:
                        raise WorkflowExecutionNotFoundError(execution_id)
                    existing = self._executions.get(existing_id)
                    if existing is None:
                        raise WorkflowExecutionNotFoundError(existing_id)
                    return existing
        output_data: WorkflowPayload | None = {}
        state: WorkflowState = "completed"
        error = None
        if workflow.handler is not None:
            try:
                output_data = workflow.handler(deepcopy(source_input)) or {}
            except Exception as exc:  # noqa: BLE001
                state = "failed"
                error = str(exc)
                output_data = None
        execution = WorkflowExecution(
            execution_id=execution_id,
            workflow_id=workflow.workflow_id,
            state=state,
            input_data=reserved_input,
            output_data=output_data,
            correlation_id=correlation_id,
            started_at=started_at,
            ended_at=self._utcnow(),
            error=error,
            metadata=reserved_metadata,
        )
        with self._lock:
            self._executions[execution_id] = execution
            if correlation_id:
                correlation_key = (workflow.workflow_id, correlation_id)
                self._correlation_index[correlation_key] = execution_id
                event = self._correlation_events.pop(correlation_key, None)
                if event is not None:
                    event.set()
        return execution

    def resume(
        self,
        execution_id: str,
        signal: WorkflowPayload | None = None,
    ) -> WorkflowExecution:
        """Complete a waiting execution with an optional resume signal.

        Raises:
            WorkflowExecutionNotFoundError: If `execution_id` is unknown.
            WorkflowCancelledError: If the execution is already cancelled.
            WorkflowInvalidStateError: If the execution is not waiting.
        """
        with self._lock:
            execution = self._executions.get(execution_id)
            if execution is None:
                raise WorkflowExecutionNotFoundError(execution_id)
            if execution.state == "cancelled":
                raise WorkflowCancelledError(execution_id)
            if execution.state != "waiting":
                raise WorkflowInvalidStateError(
                    execution_id,
                    operation="resume",
                    state=execution.state,
                    expected_states=("waiting",),
                )
            merged_metadata = dict(execution.metadata)
            if signal is not None:
                merged_metadata["resume_signal"] = deepcopy(signal)
            updated = WorkflowExecution(
                execution_id=execution.execution_id,
                workflow_id=execution.workflow_id,
                state="completed",
                input_data=execution.input_data,
                output_data=execution.output_data,
                correlation_id=execution.correlation_id,
                started_at=execution.started_at,
                ended_at=self._utcnow(),
                error=execution.error,
                metadata=merged_metadata,
            )
            self._executions[execution_id] = updated
        return updated

    def cancel(
        self, execution_id: str, *, reason: str | None = None
    ) -> WorkflowExecution:
        """Cancel an active local execution.

        Raises:
            WorkflowExecutionNotFoundError: If `execution_id` is unknown.
            WorkflowCancelledError: If the execution is already cancelled.
            WorkflowInvalidStateError: If the execution is terminal.
        """
        with self._lock:
            execution = self._executions.get(execution_id)
            if execution is None:
                raise WorkflowExecutionNotFoundError(execution_id)
            if execution.state == "cancelled":
                raise WorkflowCancelledError(execution_id)
            if execution.state not in ACTIVE_WORKFLOW_STATES:
                raise WorkflowInvalidStateError(
                    execution_id,
                    operation="cancel",
                    state=execution.state,
                    expected_states=ACTIVE_WORKFLOW_STATES,
                )
            updated = WorkflowExecution(
                execution_id=execution.execution_id,
                workflow_id=execution.workflow_id,
                state="cancelled",
                input_data=execution.input_data,
                output_data=execution.output_data,
                correlation_id=execution.correlation_id,
                started_at=execution.started_at,
                ended_at=self._utcnow(),
                error=reason,
                metadata=execution.metadata,
            )
            self._executions[execution_id] = updated
        return updated

    def status(self, execution_id: str) -> WorkflowExecution:
        """Return the stored execution snapshot.

        Raises:
            WorkflowExecutionNotFoundError: If `execution_id` is unknown.
        """
        with self._lock:
            execution = self._executions.get(execution_id)
        if execution is None:
            raise WorkflowExecutionNotFoundError(execution_id)
        return execution
