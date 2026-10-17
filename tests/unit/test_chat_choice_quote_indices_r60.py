"""Quote discovery and rejection use the same original USER positions."""

import asyncio
from copy import deepcopy
import json

import pytest

from general_manager.chat.planned import choice_context as api
from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.planner import plan_request
from general_manager.chat.providers.base import Message
from tests.unit.test_chat_planned_choice_context import (
    envelope as answered_envelope,
    history as answered_history,
)
from tests.unit.test_chat_planned_planner import _plan, _PlannerProvider, _settings


DEFINITION = "The opportunity gap is forecast minus the existing plan, in pieces."
REQUEST = "Which customers have a positive opportunity gap for 2031?"


def _envelope(index):
    return {
        "plan": _plan(),
        "choices": [],
        "clarification_requests": [],
        "definition_bindings": [
            {
                "topic": "metric",
                "definition_quote": DEFINITION,
                "scope_quote": {"index": index, "quote": REQUEST},
            }
        ],
    }


@pytest.mark.parametrize("include_configured_system", [False, True])
def test_planner_publishes_user_positions_and_repairs_the_exact_bad_index(
    include_configured_system,
):
    messages = [
        Message("system", "Native schema instructions."),
        Message("user", REQUEST),
    ]
    if include_configured_system:
        messages.insert(0, Message("system", DEFINITION))
    original = list(messages)
    _PlannerProvider.calls.clear()
    _PlannerProvider.responses = [json.dumps(_envelope(0)), json.dumps(_envelope(1))]
    budget = RoundBudget(())

    result = asyncio.run(
        plan_request(
            REQUEST,
            messages,
            _settings(),
            budget,
            {},
            configured_context=DEFINITION,
        )
    )

    assert result.choice_context is not None
    assert budget.global_used == len(_PlannerProvider.calls) == 2
    for call in _PlannerProvider.calls:
        reference = json.loads(call[-1].content.removeprefix("REFERENCE_DATA="))
        assert reference.get("user_sources") == [{"index": 1, "content": REQUEST}]
        assert [row["role"] for row in reference["conversation_context"]] == [
            "system",
            "user",
        ]
        index_schema = reference["required_json_schema"]["properties"][
            "definition_bindings"
        ]["items"]["properties"]["scope_quote"]["properties"]["index"]
        assert "user_sources" in index_schema.get("description", "")
        assert "conversation_context" in index_schema.get("description", "")
    repair = json.loads(
        _PlannerProvider.calls[1][-1].content.removeprefix("REFERENCE_DATA=")
    )["previous_rejection"]
    assert repair["path"] == "$.definition_bindings[0].scope_quote.index"
    assert "USER" in repair["expected"] and "1" in repair["expected"]
    assert messages == original


def _mixed_history():
    return [
        Message("system", "Native schema instructions."),
        Message("user", REQUEST),
        Message("assistant", "Earlier information is only context."),
        Message("tool", "Earlier tool result.", tool_name="query"),
        Message("user", REQUEST),
    ]


@pytest.mark.parametrize("index", [0, 1, 2, 3, True, 50])
def test_bad_source_index_is_rejected_without_renumbering_or_output_repair(index):
    messages = _mixed_history()
    payload = _envelope(index)
    original = deepcopy(payload)
    with pytest.raises(api.ChoiceValidationError) as caught:
        api.validate_choices(
            payload, messages, user_text=REQUEST, configured_context=DEFINITION
        )
    assert caught.value.path == "$.definition_bindings[0].scope_quote.index"
    assert "USER" in caught.value.expected and "4" in caught.value.expected
    assert payload == original


def test_exact_latest_user_source_is_accepted_with_all_roles_present():
    messages = _mixed_history()
    context = api.validate_choices(
        _envelope(4), messages, user_text=REQUEST, configured_context=DEFINITION
    )
    context.verify_messages(messages, user_text=REQUEST, configured_context=DEFINITION)
    assert context.as_mapping()["user_sources"] == [
        {"index": 1, "content": REQUEST},
        {"index": 4, "content": REQUEST},
    ]


def test_wrong_quote_keeps_its_source_index_and_reports_the_quote_path():
    payload = _envelope(4)
    payload["definition_bindings"][0]["scope_quote"]["quote"] = "A different request."
    with pytest.raises(api.ChoiceValidationError) as caught:
        api.validate_choices(
            payload,
            _mixed_history(),
            user_text=REQUEST,
            configured_context=DEFINITION,
        )
    assert caught.value.path == "$.definition_bindings[0].scope_quote.quote"
    assert "USER" in caught.value.expected and "4" in caught.value.expected


def test_new_clarification_index_uses_the_same_diagnostic_owner():
    payload = _envelope(4)
    payload["definition_bindings"] = []
    payload["clarification_requests"] = [
        {
            "topic": "unit",
            "question_id": None,
            "scope_quote": {"index": 0, "quote": REQUEST},
        }
    ]
    with pytest.raises(api.ChoiceValidationError) as caught:
        api.validate_choices(
            payload,
            _mixed_history(),
            user_text=REQUEST,
            configured_context=DEFINITION,
        )
    assert caught.value.path == "$.clarification_requests[0].scope_quote.index"
    assert "USER" in caught.value.expected and "4" in caught.value.expected


def test_prior_question_decision_reports_its_exact_invalid_quote_path():
    messages = answered_history()
    payload = answered_envelope(messages)
    payload["choices"][0]["user_quotes"][0]["index"] = 1
    with pytest.raises(api.ChoiceValidationError) as caught:
        api.validate_choices(payload, messages)
    assert caught.value.path == "$.choices[0].user_quotes[0].index"
    assert "USER" in caught.value.expected and "2" in caught.value.expected


def test_standalone_python_entry_publishes_the_actual_appended_user_source():
    _PlannerProvider.calls.clear()
    _PlannerProvider.responses = [json.dumps(_envelope(1))]
    result = asyncio.run(
        plan_request(
            REQUEST,
            [Message("system", "Native schema instructions.")],
            _settings(),
            RoundBudget(()),
            {},
            configured_context=DEFINITION,
        )
    )
    assert result.choice_context is not None
    reference = json.loads(
        _PlannerProvider.calls[0][-1].content.removeprefix("REFERENCE_DATA=")
    )
    assert reference.get("user_sources") == [{"index": 1, "content": REQUEST}]
