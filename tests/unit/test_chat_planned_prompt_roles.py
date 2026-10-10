"""Application instructions retain their role; tool data never acquires it."""

import json

from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import EvidenceRequirement, PlannedTask
from general_manager.chat.planned.planner import _request_messages
from general_manager.chat.planned.scheduler import _executor_messages
from general_manager.chat.planned.synthesis import _messages
from general_manager.chat.providers.base import Message

CONFIG = "Answer in German. Fiscal year ends in March."
HISTORY = [
    Message(role="user", content="Use shipped pieces in 2023-2025."),
    Message(role="assistant", content="Unverified prior claim."),
]


def test_planner_preserves_server_instruction_role_without_promoting_schema():
    messages = _request_messages(
        "Compare",
        [
            Message(role="system", content=CONFIG),
            Message(role="system", content="GENERATED_SCHEMA_INJECTION"),
            *HISTORY,
        ],
        {"description": "SCHEMA_INJECTION"},
        correction=False,
        configured_context=CONFIG,
    )
    systems = [m.content for m in messages if m.role == "system"]
    assert CONFIG in systems
    assert not any("SCHEMA_INJECTION" in text for text in systems)
    reference = json.loads(messages[-1].content.split("=", 1)[1])
    assert not any("GENERATED_SCHEMA_INJECTION" in text for text in systems)
    assert any(
        m["content"] == "GENERATED_SCHEMA_INJECTION"
        for m in reference["conversation_context"]
    )
    assert not any(m["content"] == CONFIG for m in reference["conversation_context"])


def test_executor_receives_captured_config_and_visible_dialogue_as_separate_roles():
    task = PlannedTask(
        "t",
        "Compare",
        (),
        (EvidenceRequirement("q", "query", "Read", None),),
        ("q",),
        (),
    )
    messages = _executor_messages(
        "Continue",
        task,
        EvidenceStore(),
        configured_context=CONFIG,
        conversation_context=HISTORY,
    )
    assert CONFIG in [m.content for m in messages if m.role == "system"]
    reference = json.loads(
        next(
            m.content for m in messages if m.content.startswith("REFERENCE_DATA=")
        ).split("=", 1)[1]
    )
    assert reference["conversation_context"] == [
        {"role": m.role, "content": m.content} for m in HISTORY
    ]
    assert "Unverified prior claim." not in " ".join(
        m.content for m in messages if m.role == "system"
    )


def test_synthesizer_receives_same_config_and_history_without_claiming_evidence():
    messages = _messages("Continue", (), {}, CONFIG, HISTORY)
    assert CONFIG in [m.content for m in messages if m.role == "system"]
    reference = json.loads(messages[-1].content.split("=", 1)[1])
    assert reference["conversation_context"] == [
        {"role": m.role, "content": m.content} for m in HISTORY
    ]
    assert reference["resolved_evidence"] == []
