"""Built-in workflow engine backends."""

from general_manager.workflow.backends.local import LocalWorkflowEngine
from general_manager.workflow.backends.n8n import N8nWorkflowEngine

__all__ = ["LocalWorkflowEngine", "N8nWorkflowEngine"]
