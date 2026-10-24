"""First query-grounded identity choices do not depend on application meanings."""

import asyncio
import json

import pytest

from general_manager.chat.planned import choice_context as api
from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.selector_question import selector_question
from general_manager.chat.planned.synthesis import synthesize_answer
from general_manager.chat.providers.base import Message
from tests.unit.test_chat_planned_choice_context import envelope, history
from tests.unit.test_chat_planned_selector_evidence import choices_store, selector
from tests.unit.test_chat_planned_synthesis import _settings, _SynthesisProvider


@pytest.fixture(params=["", "Assess actual shipped pieces for the selected period."])
def configured_context(request):
    return request.param


def _choice_context(messages, configured_context, *, state="answered", requests=()):
    payload = envelope(messages, state=state)
    payload["clarification_requests"] = list(requests)
    if configured_context:
        payload["definition_bindings"] = []
    return api.validate_choices(
        payload,
        messages,
        user_text=messages[-1].content,
        configured_context=configured_context,
    )


def _synthesize(messages, context, configured_context, responses, *, store=None):
    _SynthesisProvider.calls.clear()
    _SynthesisProvider.responses = [json.dumps(value) for value in responses]
    budget = RoundBudget(())
    result = asyncio.run(
        synthesize_answer(
            messages[-1].content,
            choices_store() if store is None else store,
            {},
            _settings(),
            budget,
            configured_context=configured_context,
            conversation_context=messages,
            choice_context=context,
        )
    )
    return result, budget


def _selector_history():
    request = "Which project has shipped the most pieces?"
    question = selector_question(
        request, selector()["clarification"], choices_store().records[0]
    )
    return [
        Message("user", request),
        Message(
            "assistant", question.answer, clarification_metadata=question.as_metadata()
        ),
        Message("user", "Use Alpha."),
    ]


def test_first_grounded_identity_choice_survives_answered_analytical_choices(
    configured_context,
):
    messages = history()
    context = _choice_context(messages, configured_context)
    assert context.allowed_topics == ("record_selector",)
    for topic in ("criterion", "metric", "horizon"):
        with pytest.raises(api.ChoiceValidationError):
            context.check_clarification([topic])

    result, budget = _synthesize(
        messages, context, configured_context, [selector(), selector()]
    )
    assert result.answer == 'Which Project record do you mean: "Alpha", "Beta"?'
    assert result.evidence_ids == ("choices",)
    assert budget.global_used == 1


def test_first_grounded_identity_choice_also_survives_unrelated_prior_choices(
    configured_context,
):
    messages = history("Now assess the shipped pieces for a different project.")
    payload = envelope(messages, applicability="unrelated")
    if configured_context:
        payload["definition_bindings"] = []
    context = api.validate_choices(
        payload, messages, configured_context=configured_context
    )
    result, budget = _synthesize(
        messages, context, configured_context, [selector(), selector()]
    )
    assert result.answer == 'Which Project record do you mean: "Alpha", "Beta"?'
    assert result.evidence_ids == ("choices",)
    assert budget.global_used == 1


@pytest.mark.parametrize("state", ["answered", "open", "conflicting"])
def test_prior_identity_question_suppresses_implicit_selector_retry(
    configured_context, state
):
    messages = _selector_history()
    context = _choice_context(messages, configured_context, state=state)
    with pytest.raises(api.ChoiceValidationError):
        context.check_clarification(["record_selector"])
    answer = {"answer": "Alpha has 42 shipped pieces.", "evidence_ids": ["choices"]}
    result, budget = _synthesize(
        messages, context, configured_context, [selector(), answer]
    )
    assert result.answer == answer["answer"]
    assert result.evidence_ids == ("choices",)
    assert budget.global_used == 2


@pytest.mark.parametrize("state", ["open", "conflicting"])
def test_explicitly_requested_open_identity_question_keeps_its_bound_retry(
    configured_context, state
):
    messages = _selector_history()
    request = {
        "topic": "record_selector",
        "question_id": api.choice_questions(messages)[0]["question_id"],
        "scope_quote": {"index": 2, "quote": messages[2].content},
    }
    context = _choice_context(
        messages, configured_context, state=state, requests=[request]
    )
    result, budget = _synthesize(
        messages, context, configured_context, [selector(), selector()]
    )
    assert result.answer == 'Which Project record do you mean: "Alpha", "Beta"?'
    assert result.evidence_ids == ("choices",)
    assert budget.global_used == 1


@pytest.mark.parametrize(
    "damage", ["absent", "invented", "nonidentity", "partial", "unselected", "unbound"]
)
def test_first_identity_choice_still_requires_a_valid_query_witness(
    configured_context, damage
):
    messages = history()
    context = _choice_context(messages, configured_context)
    context.check_clarification(["record_selector"])
    value = selector()
    store = choices_store()
    if damage == "absent":
        value["clarification"].pop("selector")
    elif damage == "invented":
        value["clarification"]["selector"]["evidence_id"] = "invented"
    elif damage == "nonidentity":
        value["clarification"]["selector"]["field"] = "amount"
    elif damage == "partial":
        store = choices_store(complete=False)
    elif damage == "unselected":
        store = choices_store(selected=False)
    else:
        store = choices_store(identity="legacy")
    answer = {"answer": "Verified current records.", "evidence_ids": ["choices"]}
    result, budget = _synthesize(
        messages, context, configured_context, [value, answer], store=store
    )
    assert result.answer == answer["answer"]
    assert result.evidence_ids == ("choices",)
    assert budget.global_used == 2
