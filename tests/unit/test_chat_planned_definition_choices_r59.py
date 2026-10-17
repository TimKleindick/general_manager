"""Source-bound application meanings also constrain first-turn questions."""

import asyncio
from copy import deepcopy
import json

import pytest

from general_manager.chat.planned import choice_context as api
from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.planner import plan_request
from general_manager.chat.planned.synthesis import synthesize_answer
from general_manager.chat.providers.base import Message
from tests.unit.test_chat_planned_planner import _plan, _settings, _PlannerProvider
from tests.unit.test_chat_planned_synthesis import (
    _settings as synthesis_settings,
    _store,
    _SynthesisProvider,
)


DEFINITION = "The opportunity gap is forecast minus the existing plan, in pieces."
REQUEST = "Which customers have a positive opportunity gap for 2031?"


def envelope(*, bindings=True, requests=()):
    return {
        "plan": _plan(),
        "choices": [],
        "definition_bindings": [
            {
                "topic": "metric",
                "definition_quote": DEFINITION,
                "scope_quote": {"index": 0, "quote": REQUEST},
            }
        ]
        if bindings
        else [],
        "clarification_requests": [
            {
                "topic": topic,
                "question_id": None,
                "scope_quote": {"index": 0, "quote": REQUEST},
            }
            for topic in requests
        ],
    }


def test_first_turn_plan_requires_definition_scope_before_execution():
    # A formerly accepted bare schema-only clarification plan must be corrected
    # before it can terminate the request without gathering business evidence.
    schema_only = _plan()
    requirement = schema_only["tasks"][0]["requirements"][0]
    requirement["kind"] = "schema"
    _PlannerProvider.calls.clear()
    _PlannerProvider.responses = [json.dumps(schema_only), json.dumps(envelope())]
    budget = RoundBudget(())
    result = asyncio.run(
        plan_request(
            REQUEST,
            [Message("user", REQUEST)],
            _settings(),
            budget,
            {},
            configured_context=DEFINITION,
        )
    )
    assert len(_PlannerProvider.calls) == budget.global_used == 2
    assert result.plan.tasks[0].requirements[0].kind == "query"
    assert result.choice_context is not None
    assert result.choice_context.allowed_topics == ("record_selector",)
    with pytest.raises(api.ChoiceValidationError):
        result.choice_context.check_clarification(["metric"])


def test_resolved_definition_cannot_simultaneously_be_an_open_question():
    with pytest.raises(api.ChoiceValidationError):
        api.validate_choices(
            envelope(requests=["metric"]),
            [Message("user", REQUEST)],
            user_text=REQUEST,
            configured_context=DEFINITION,
        )


@pytest.mark.parametrize(
    "damage", ["quote", "assistant", "scope", "duplicate", "identity", "missing"]
)
def test_definition_binding_rejects_forged_or_unscoped_authority(damage):
    payload = envelope()
    binding = payload["definition_bindings"][0]
    if damage == "quote":
        binding["definition_quote"] = "Invented default"
    elif damage == "assistant":
        binding["definition_quote"] = "An assistant asserted a different metric."
    elif damage == "scope":
        binding["scope_quote"]["quote"] = "Another request"
    elif damage == "duplicate":
        payload["definition_bindings"].append(deepcopy(binding))
    elif damage == "identity":
        binding["topic"] = "record_selector"
    else:
        payload.pop("definition_bindings")
    with pytest.raises(api.ChoiceValidationError):
        api.validate_choices(
            payload,
            [
                Message("user", REQUEST),
                Message("assistant", "An assistant asserted a different metric."),
            ],
            user_text=REQUEST,
            configured_context=DEFINITION,
        )


def test_unresolved_unit_remains_askable_without_promoting_a_default():
    payload = envelope(bindings=False, requests=["unit"])
    context = api.validate_choices(
        payload,
        [Message("user", REQUEST)],
        user_text=REQUEST,
        configured_context="Piece counts and weights are different metrics.",
    )
    context.check_clarification(["unit"])
    assert context.allowed_topics == ("unit", "record_selector")


def test_changed_application_definition_invalidates_bound_scope():
    messages = [Message("user", REQUEST)]
    context = api.validate_choices(
        envelope(), messages, user_text=REQUEST, configured_context=DEFINITION
    )
    context.verify_messages(messages, user_text=REQUEST, configured_context=DEFINITION)
    with pytest.raises(api.ChoiceValidationError):
        context.verify_messages(
            messages, user_text=REQUEST, configured_context="Different meaning."
        )


def test_synthesis_cannot_reopen_first_turn_resolved_metric():
    messages = [Message("user", REQUEST)]
    context = api.validate_choices(
        envelope(), messages, user_text=REQUEST, configured_context=DEFINITION
    )
    _SynthesisProvider.calls.clear()
    _SynthesisProvider.responses = [
        json.dumps({"clarification": {"language": "en", "requirements": ["metric"]}}),
        json.dumps(
            {
                "answer": "No matching rows were returned.",
                "evidence_ids": ["ev-query-1"],
            }
        ),
    ]
    budget = RoundBudget(())
    result = asyncio.run(
        synthesize_answer(
            REQUEST,
            _store(),
            {},
            synthesis_settings(),
            budget,
            configured_context=DEFINITION,
            conversation_context=messages,
            choice_context=context,
        )
    )
    assert result.answer == "No matching rows were returned."
    assert len(_SynthesisProvider.calls) == budget.global_used == 2
    assert result.evidence_ids == ("ev-query-1",)


def test_initial_request_without_history_still_has_a_real_user_scope():
    _PlannerProvider.responses = [json.dumps(envelope())]
    result = asyncio.run(
        plan_request(
            REQUEST, [], _settings(), RoundBudget(()), {}, configured_context=DEFINITION
        )
    )
    result.choice_context.verify_messages(
        [], user_text=REQUEST, configured_context=DEFINITION
    )


def test_first_record_choice_keeps_its_current_query_witness_owner():
    context = api.validate_choices(
        envelope(),
        [Message("user", REQUEST)],
        user_text=REQUEST,
        configured_context=DEFINITION,
    )
    # Meaning bindings cannot predict an identity ambiguity discovered by a
    # later query. The selector action still requires that actual query witness.
    context.check_clarification(["record_selector"])
    assert "metric" not in context.allowed_topics
