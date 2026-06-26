"""Tests for the root GeneralManager Django model exports."""

from __future__ import annotations

import general_manager.models as root_models
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


def test_root_models_module_exports_canonical_django_models() -> None:
    """Expose concrete app models from their canonical submodules."""
    assert root_models.__all__ == (
        "ChatConversation",
        "ChatMessage",
        "ChatPendingConfirmation",
        "SearchIndexState",
        "WorkflowDeliveryAttempt",
        "WorkflowEventRecord",
        "WorkflowExecutionRecord",
        "WorkflowOutbox",
    )
    assert root_models.ChatConversation is ChatConversation
    assert root_models.ChatMessage is ChatMessage
    assert root_models.ChatPendingConfirmation is ChatPendingConfirmation
    assert root_models.SearchIndexState is SearchIndexState
    assert root_models.WorkflowDeliveryAttempt is WorkflowDeliveryAttempt
    assert root_models.WorkflowEventRecord is WorkflowEventRecord
    assert root_models.WorkflowExecutionRecord is WorkflowExecutionRecord
    assert root_models.WorkflowOutbox is WorkflowOutbox
