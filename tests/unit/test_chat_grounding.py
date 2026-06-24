from __future__ import annotations

from general_manager.chat.grounding import (
    build_empty_response_recovery_message,
    build_query_required_recovery_message,
    build_missing_tool_recovery_message,
    should_recover_answer_without_query,
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


def test_schema_only_data_answer_triggers_query_recovery() -> None:
    assert (
        should_recover_answer_without_query(
            user_text="Which projects would be affected if cobalt parts were updated?",
            assistant_text="Apollo would be affected.",
            tool_calls=[{"name": "get_manager_schema", "args": {}}],
        )
        is True
    )


def test_recover_answer_without_query_for_path_only_record_answer() -> None:
    assert should_recover_answer_without_query(
        user_text=(
            "Find records in SyntheticManager08 related to the first "
            "SyntheticManager01 item."
        ),
        assistant_text="I found a path, but no records yet.",
        tool_calls=[{"name": "find_path", "result": {"path": ["synthetic08"]}}],
    )


def test_recover_answer_without_query_for_successful_find_path_result() -> None:
    assert should_recover_answer_without_query(
        user_text="Find records in TargetManager related to SourceManager.",
        assistant_text="I found a path, but no records yet.",
        tool_calls=[{"name": "find_path", "result": ["target"]}],
    )


def test_no_query_recovery_after_empty_find_path_result() -> None:
    assert (
        should_recover_answer_without_query(
            user_text="Find records in TargetManager related to SourceManager.",
            assistant_text="No path was found between those managers.",
            tool_calls=[{"name": "find_path", "result": {"path": []}}],
        )
        is False
    )


def test_no_query_recovery_for_find_path_without_result() -> None:
    assert (
        should_recover_answer_without_query(
            user_text="Find records in TargetManager related to SourceManager.",
            assistant_text="I found a path, but no records yet.",
            tool_calls=[{"name": "find_path"}],
        )
        is False
    )


def test_no_query_recovery_when_metadata_precedes_empty_find_path_result() -> None:
    assert (
        should_recover_answer_without_query(
            user_text="Find records in TargetManager related to SourceManager.",
            assistant_text="I cannot continue from that path result.",
            tool_calls=[
                {"name": "search_managers", "result": [{"manager": "TargetManager"}]},
                {"name": "find_path", "result": {"path": []}},
            ],
        )
        is False
    )


def test_answer_after_query_does_not_trigger_query_recovery() -> None:
    assert (
        should_recover_answer_without_query(
            user_text="Which projects use cobalt?",
            assistant_text="Apollo uses cobalt.",
            tool_calls=[{"name": "query", "args": {"manager": "ProjectManager"}}],
        )
        is False
    )


def test_mutation_tool_does_not_trigger_query_recovery() -> None:
    assert (
        should_recover_answer_without_query(
            user_text="Create records in PartManager.",
            assistant_text="Created the requested record.",
            tool_calls=[{"name": "mutate", "args": {"mutation": "createPart"}}],
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


def test_empty_response_recovery_message_continues_after_tool_result() -> None:
    message = build_empty_response_recovery_message(
        "What projects contain parts with cobalt?"
    )

    assert "previous tool result is not a final answer" in message
    assert "call query" in message
    assert "What projects contain parts with cobalt?" in message


def test_query_required_recovery_message_requires_data_query() -> None:
    message = build_query_required_recovery_message(
        "Which projects would be affected if cobalt parts were updated?"
    )

    assert "Schema and path tools are not data queries" in message
    assert "Call query" in message
    assert "Which projects would be affected if cobalt parts were updated?" in message
