"""Preserve the evaluated role instructions and verbatim application context."""

from dataclasses import replace
from hashlib import sha256

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
    "role,expected_digest",
    [
        ("planner", "b345fb35d4c5e544ae824bf3e08319c3eafa8eee590fe156462a6538b75272ab"),
        (
            "executor",
            "9fb129f5e168d96b8107c88404190dd09cb9e6ea9bd79a34ce2b5a34fe480042",
        ),
        (
            "synthesis",
            "334e0b7bc297f54c6c538e6ffca3eb5bfa8ad5191fb66a3fa608b7397031b2db",
        ),
    ],
)
def test_role_system_instructions_match_evaluated_contract(role, expected_digest):
    # Assembled from the independently frozen 7e465eb2 evaluation source. Changes
    # to this contract need explicit review; application context is not hashed.
    messages = role_messages(role)
    systems = [message.content for message in messages if message.role == "system"]
    assert systems.count(APPLICATION) == 1
    instructions = [text for text in systems if text != APPLICATION]
    assert sha256("\n".join(instructions).encode()).hexdigest() == expected_digest
