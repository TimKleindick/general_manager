"""Root Django model exports for the GeneralManager app.

Importing `general_manager.models` exposes the concrete search and workflow
models that belong to this Django app. The classes are imported from their
canonical `general_manager.search.models` and `general_manager.workflow.models`
modules and re-exported for Django model discovery and stable root-module
imports.

This module defines no public callables, accepts no application inputs, returns
no application outputs, and wraps no import or Django app-registry errors.
"""

from general_manager.chat.models import (
    ChatConversation,
    ChatMessage,
    ChatPendingConfirmation,
)
from general_manager.search.models import SearchIndexState
from general_manager.workflow.models import (
    WorkflowDeliveryAttempt,
    WorkflowEventRecord,
    WorkflowExecutionRecord,
    WorkflowOutbox,
)

__all__: tuple[str, ...] = (
    "ChatConversation",
    "ChatMessage",
    "ChatPendingConfirmation",
    "SearchIndexState",
    "WorkflowDeliveryAttempt",
    "WorkflowEventRecord",
    "WorkflowExecutionRecord",
    "WorkflowOutbox",
)
