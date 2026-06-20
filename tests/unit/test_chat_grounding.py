from __future__ import annotations

from general_manager.chat.grounding import (
    build_missing_tool_recovery_message,
    should_recover_missing_tool_call,
)


def test_data_question_without_tools_triggers_recovery() -> None:
    assert (
        should_recover_missing_tool_call(
            user_text="Which materials have density above 7?",
            assistant_text="Steel and Cobalt are dense.",
            tool_calls=[],
        )
        is True
    )


def test_conceptual_question_does_not_trigger_recovery() -> None:
    assert (
        should_recover_missing_tool_call(
            user_text="What can this assistant do?",
            assistant_text="I can inspect managers and query data.",
            tool_calls=[],
        )
        is False
    )


def test_existing_tool_call_does_not_trigger_recovery() -> None:
    assert (
        should_recover_missing_tool_call(
            user_text="List parts",
            assistant_text="Bolt",
            tool_calls=[{"name": "query", "args": {"manager": "PartManager"}}],
        )
        is False
    )


def test_recovery_message_is_short_and_tool_directive() -> None:
    message = build_missing_tool_recovery_message(
        "Which materials have density above 7?"
    )

    assert "Do not answer from memory" in message
    assert "Call the available tools" in message
    assert "Which materials have density above 7?" in message
