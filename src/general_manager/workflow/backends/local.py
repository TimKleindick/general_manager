"""Default local workflow engine backend."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import Event, Lock
from typing import Any, Mapping
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


class LocalWorkflowEngine:
    """In-memory workflow engine for development and tests."""

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
        input_data: Mapping[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkflowExecution:
        started_at = self._utcnow()
        execution_id = str(uuid4())
        reserved_input = deepcopy(input_data or {})
        reserved_metadata = deepcopy(metadata or {})
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
        output_data: Mapping[str, Any] | None = {}
        state: WorkflowState = "completed"
        error = None
        if workflow.handler is not None:
            try:
                output_data = workflow.handler(deepcopy(input_data or {})) or {}
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
        signal: Mapping[str, Any] | None = None,
    ) -> WorkflowExecution:
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
            if signal:
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
        with self._lock:
            execution = self._executions.get(execution_id)
        if execution is None:
            raise WorkflowExecutionNotFoundError(execution_id)
        return execution
