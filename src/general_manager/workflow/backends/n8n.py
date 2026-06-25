"""n8n workflow engine adapter stub."""

from __future__ import annotations

from collections.abc import Mapping

from general_manager.workflow.engine import (
    WorkflowDefinition,
    WorkflowEngineError,
    WorkflowExecution,
)

type WorkflowPayload = Mapping[str, object]


class N8nOperationNotImplementedError(WorkflowEngineError):
    """Raised when an n8n workflow operation is not implemented.

    The error message includes the attempted `N8nWorkflowEngine` method name.
    """

    def __init__(self, operation: str) -> None:
        super().__init__(f"N8nWorkflowEngine.{operation} is not implemented yet.")


class N8nWorkflowEngine:
    """Configuration holder for the future n8n workflow adapter.

    The constructor stores `base_url` and optional `api_key`, but no network
    operations are implemented yet. `start()`, `resume()`, `cancel()`, and
    `status()` all raise `N8nOperationNotImplementedError`.
    """

    def __init__(self, *, base_url: str, api_key: str | None = None) -> None:
        """Store the n8n endpoint configuration for future adapter work."""
        self.base_url = base_url
        self.api_key = api_key

    def start(
        self,
        workflow: WorkflowDefinition,
        input_data: WorkflowPayload | None = None,
        *,
        correlation_id: str | None = None,
        metadata: WorkflowPayload | None = None,
    ) -> WorkflowExecution:
        """Raise because starting n8n workflows is not implemented."""
        raise N8nOperationNotImplementedError("start")

    def resume(
        self,
        execution_id: str,
        signal: WorkflowPayload | None = None,
    ) -> WorkflowExecution:
        """Raise because resuming n8n workflows is not implemented."""
        raise N8nOperationNotImplementedError("resume")

    def cancel(
        self, execution_id: str, *, reason: str | None = None
    ) -> WorkflowExecution:
        """Raise because cancelling n8n workflows is not implemented."""
        raise N8nOperationNotImplementedError("cancel")

    def status(self, execution_id: str) -> WorkflowExecution:
        """Raise because reading n8n workflow status is not implemented."""
        raise N8nOperationNotImplementedError("status")
