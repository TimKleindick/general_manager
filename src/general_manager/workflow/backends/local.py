"""Default local workflow engine backend."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Mapping
from uuid import uuid4

from general_manager.workflow.engine import (
    WorkflowCancelledError,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowExecutionNotFoundError,
    WorkflowState,
)


class LocalWorkflowEngine:
    """In-memory workflow engine for development and tests."""

    def __init__(self) -> None:
        self._executions: dict[str, WorkflowExecution] = {}
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
        execution_id = str(uuid4())
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
            input_data=deepcopy(input_data or {}),
            output_data=output_data,
            correlation_id=correlation_id,
            started_at=self._utcnow(),
            ended_at=self._utcnow(),
            error=error,
            metadata=deepcopy(metadata or {}),
        )
        with self._lock:
            self._executions[execution_id] = execution
        return execution

    def resume(
        self,
        execution_id: str,
        signal: Mapping[str, Any] | None = None,
    ) -> WorkflowExecution:
        execution = self.status(execution_id)
        if execution.state == "cancelled":
            raise WorkflowCancelledError(execution_id)
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
        with self._lock:
            self._executions[execution_id] = updated
        return updated

    def cancel(
        self, execution_id: str, *, reason: str | None = None
    ) -> WorkflowExecution:
        execution = self.status(execution_id)
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
        with self._lock:
            self._executions[execution_id] = updated
        return updated

    def status(self, execution_id: str) -> WorkflowExecution:
        with self._lock:
            execution = self._executions.get(execution_id)
        if execution is None:
            raise WorkflowExecutionNotFoundError(execution_id)
        return execution
