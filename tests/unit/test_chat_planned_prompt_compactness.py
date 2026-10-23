"""Keep assembled role instructions bounded without trimming application context."""

from dataclasses import replace

import pytest

from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import EvidenceRequirement, SchemaBinding
from general_manager.chat.planned.planner import _request_messages
from general_manager.chat.planned.scheduler import _executor_messages
from general_manager.chat.planned.synthesis import _messages
from general_manager.chat.providers.base import Message
from tests.unit.test_chat_planned_scheduler import _task


APPLICATION = "Application calendar and business definitions must remain verbatim."
HISTORY = [
    Message("user", "Assess the selected records."),
    Message("assistant", "Which metric should I use?"),
    Message("user", "Use the observed shipment quantities."),
]


def role_messages(role):
    request = HISTORY[-1].content
    if role == "planner":
        return _request_messages(
            request, HISTORY, {}, correction=False, configured_context=APPLICATION
        )
    if role == "synthesis":
        return _messages(request, (), {}, APPLICATION, HISTORY)
    task = replace(
        _task("read"),
        requirements=(
            EvidenceRequirement(
                "schema",
                "schema",
                "Observe selected definitions",
                None,
                schema=SchemaBinding("Shipment", "detail", (), "current"),
            ),
            EvidenceRequirement("rows", "query", "Read complete quantities", None),
            EvidenceRequirement(
                "total", "calculation", "Sum quantities", "sum", binding_required=True
            ),
        ),
        completion_criteria=("schema", "rows", "total"),
    )
    return _executor_messages(
        request,
        task,
        EvidenceStore(),
        configured_context=APPLICATION,
        conversation_context=HISTORY,
    )


@pytest.mark.parametrize(
    "role,maximum", [("planner", 7000), ("executor", 8500), ("synthesis", 4500)]
)
def test_role_system_instructions_fit_compactness_budget(role, maximum):
    messages = role_messages(role)
    systems = [message.content for message in messages if message.role == "system"]
    assert systems.count(APPLICATION) == 1
    instructions = [text for text in systems if text != APPLICATION]
    assert sum(map(len, instructions)) <= maximum
