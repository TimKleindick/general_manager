"""n8n workflow engine adapter stub."""

from __future__ import annotations

from typing import Any, Mapping

from general_manager.workflow.engine import (
    WorkflowDefinition,
    WorkflowEngineError,
    WorkflowExecution,
)


class N8nOperationNotImplementedError(WorkflowEngineError):
    """Raised when n8n workflow operations are not implemented."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"N8nWorkflowEngine.{operation} is not implemented yet.")


class N8nWorkflowEngine:
    """Placeholder adapter for delegating execution to n8n."""

    def __init__(self, *, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url
        self.api_key = api_key

    def start(
        self,
        workflow: WorkflowDefinition,
        input_data: Mapping[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkflowExecution:
        raise N8nOperationNotImplementedError("start")

    def resume(
        self,
        execution_id: str,
        signal: Mapping[str, Any] | None = None,
    ) -> WorkflowExecution:
        raise N8nOperationNotImplementedError("resume")

    def cancel(
        self, execution_id: str, *, reason: str | None = None
    ) -> WorkflowExecution:
        raise N8nOperationNotImplementedError("cancel")

    def status(self, execution_id: str) -> WorkflowExecution:
        raise N8nOperationNotImplementedError("status")
