"""Bounded clarification decisions use user sources, not task objectives."""

import asyncio
from copy import deepcopy
import json

import pytest

from general_manager.chat.providers.base import Message
from general_manager.chat.planned import choice_context as api
from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.planner import plan_request
from general_manager.chat.planned.synthesis import synthesize_answer
from tests.unit.test_chat_planned_planner import _plan, _settings, _PlannerProvider
from tests.unit.test_chat_planned_synthesis import (
    _settings as synthesis_settings,
    _store,
    _SynthesisProvider,
)


def history(reply="Use actual shipped pieces in 2023\u20132025, not revenue."):
    return [
        Message("user", "What is the outlook for customer XYZ?"),
        Message(
            "assistant",
            "Which criterion should I use? Which metric should I use? What time period should I consider?",
        ),
        Message("user", reply),
    ]


def envelope(messages, *, state="answered", applicability="same_scope"):
    questions = api.choice_questions(messages)
    return {
        "plan": _plan(),
        "choices": [
            {
                "question_id": q["question_id"],
                "state": state,
                "applicability": applicability,
                "user_quotes": [{"index": 2, "quote": messages[2].content}]
                if state != "open" or applicability != "same_scope"
                else [],
            }
            for q in questions
        ],
        "clarification_requests": [],
    }


def test_runtime_question_migration_is_exact_and_role_bound():
    questions = api.choice_questions(history())
    assert [q["topic"] for q in questions] == ["criterion", "metric", "horizon"]
    assert all(q["question_index"] == 1 and q["scope_index"] == 0 for q in questions)
    for message in [
        Message("user", history()[1].content),
        Message("tool", history()[1].content),
        Message("assistant", "Quoted example: " + history()[1].content),
    ]:
        assert api.choice_questions([history()[0], message, history()[2]]) == ()


def test_persisted_question_metadata_version_is_json_type_exact():
    from dataclasses import replace

    messages = history()
    metadata = api.clarification_metadata(messages[1].content, messages[0].content)
    metadata["gm_clarification"]["version"] = 1.0
    messages[1] = replace(messages[1], clarification_metadata=metadata)
    with pytest.raises(api.ChoiceValidationError):
        api.choice_questions(messages)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "question",
        "assistant",
        "quote",
        "scope",
        "state",
        "empty_answer",
    ],
)
def test_decision_contract_rejects_forged_or_incomplete_bindings(mutation):
    messages = history()
    value = envelope(messages)
    decision = value["choices"][0]
    if mutation == "missing":
        value["choices"].pop()
    elif mutation == "duplicate":
        value["choices"][1] = deepcopy(decision)
    elif mutation == "question":
        decision["question_id"] = "foreign"
    elif mutation == "assistant":
        decision["user_quotes"][0]["index"] = 1
    elif mutation == "quote":
        decision["user_quotes"][0]["quote"] = "invented answer"
    elif mutation == "scope":
        decision["user_quotes"][0] = {"index": 0, "quote": messages[0].content}
    elif mutation == "state":
        decision["state"] = "maybe"
    else:
        decision["user_quotes"] = []
    with pytest.raises(api.ChoiceValidationError):
        api.validate_choices(value, messages)


def test_answered_decision_cannot_be_requested_under_same_scope():
    messages = history()
    value = envelope(messages)
    value["clarification_requests"] = [
        {
            "topic": "criterion",
            "question_id": value["choices"][0]["question_id"],
            "scope_quote": {"index": 2, "quote": messages[2].content},
        }
    ]
    with pytest.raises(api.ChoiceValidationError):
        api.validate_choices(value, messages)
    value["clarification_requests"][0]["question_id"] = None
    with pytest.raises(api.ChoiceValidationError):
        api.validate_choices(value, messages)


@pytest.mark.parametrize(
    "state,reply",
    [
        ("open", "Pieces; I do not know the period."),
        ("conflicting", "Use revenue and also use pieces instead of revenue."),
    ],
)
def test_partial_or_conflicting_reply_can_leave_a_bound_choice_open(state, reply):
    messages = history(reply)
    value = envelope(messages, state=state)
    value["clarification_requests"] = [
        {
            "topic": "horizon",
            "question_id": value["choices"][2]["question_id"],
            "scope_quote": {"index": 2, "quote": reply},
        }
    ]
    context = api.validate_choices(value, messages)
    context.check_clarification(["horizon"])
    with pytest.raises(api.ChoiceValidationError):
        context.check_clarification(["metric"])


def test_new_scope_does_not_inherit_old_answer_lock():
    messages = history("Now assess customer ABC; I have not chosen its metric.")
    value = envelope(messages, applicability="changed_scope")
    value["clarification_requests"] = [
        {
            "topic": "metric",
            "question_id": None,
            "scope_quote": {"index": 2, "quote": messages[2].content},
        }
    ]
    api.validate_choices(value, messages).check_clarification(["metric"])


def test_repeated_question_id_cannot_override_earlier_answer_for_current_scope():
    messages = [
        *history(),
        Message("assistant", "Which criterion should I use?"),
        Message("user", "The same actual pieces."),
    ]
    questions = api.choice_questions(messages)
    value = {
        "plan": _plan(),
        "choices": [
            {
                "question_id": q["question_id"],
                "state": "answered" if q["question_index"] == 1 else "open",
                "applicability": "same_scope",
                "user_quotes": [{"index": 2, "quote": messages[2].content}]
                if q["question_index"] == 1
                else [],
            }
            for q in questions
        ],
        "clarification_requests": [
            {
                "topic": "criterion",
                "question_id": questions[-1]["question_id"],
                "scope_quote": {"index": 4, "quote": messages[4].content},
            }
        ],
    }
    with pytest.raises(api.ChoiceValidationError):
        api.validate_choices(value, messages)


def test_planner_repairs_legacy_unbound_plan_inside_existing_budget():
    messages = history()
    _PlannerProvider.calls.clear()
    _PlannerProvider.responses = [json.dumps(_plan()), json.dumps(envelope(messages))]
    budget = RoundBudget(())
    result = asyncio.run(
        plan_request(messages[-1].content, messages, _settings(), budget, {})
    )
    assert len(_PlannerProvider.calls) == 2 and budget.global_used == 2
    assert result.choice_context is not None
    result.choice_context.check_clarification([])
    first = json.loads(
        _PlannerProvider.calls[0][-1].content.removeprefix("REFERENCE_DATA=")
    )
    assert len(first["choice_questions"]) == 3
    repair = json.loads(
        _PlannerProvider.calls[1][-1].content.removeprefix("REFERENCE_DATA=")
    )
    assert repair["previous_rejection"]["path"] == "$.choices"


def test_synthesis_repeated_answered_question_is_corrected_without_extra_retry():
    messages = history()
    context = api.validate_choices(envelope(messages), messages)
    _SynthesisProvider.calls.clear()
    _SynthesisProvider.responses = [
        json.dumps(
            {"clarification": {"language": "en", "requirements": ["criterion"]}}
        ),
        json.dumps(
            {
                "answer": "The actual shipped pieces were assessed for the selected period.",
                "evidence_ids": ["ev-query-1"],
            }
        ),
    ]
    budget = RoundBudget(())
    result = asyncio.run(
        synthesize_answer(
            messages[-1].content,
            _store(),
            {"resolved": 1, "total": 1, "unresolved": []},
            synthesis_settings(),
            budget,
            conversation_context=messages,
            choice_context=context,
        )
    )
    assert result.evidence_ids == ("ev-query-1",)
    assert len(_SynthesisProvider.calls) == 2 and budget.global_used == 2
    assert any(
        "unrequested_or_answered_clarification" in m.content
        for m in _SynthesisProvider.calls[1]
    )


def test_choice_context_cannot_be_reused_for_a_different_reply():
    messages = history()
    context = api.validate_choices(envelope(messages), messages)
    changed = history("Assess a different customer instead.")
    _SynthesisProvider.calls.clear()
    from general_manager.chat.planned.synthesis import SynthesisFailedError

    with pytest.raises(SynthesisFailedError):
        asyncio.run(
            synthesize_answer(
                changed[-1].content,
                _store(),
                {"resolved": 1, "total": 1, "unresolved": []},
                synthesis_settings(),
                RoundBudget(()),
                conversation_context=changed,
                choice_context=context,
            )
        )
    assert _SynthesisProvider.calls == []


def test_direct_synthesis_with_prior_questions_requires_decision_context():
    from general_manager.chat.planned.synthesis import SynthesisFailedError

    messages = history()
    _SynthesisProvider.calls.clear()
    with pytest.raises(SynthesisFailedError):
        asyncio.run(
            synthesize_answer(
                messages[-1].content,
                _store(),
                {"resolved": 1, "total": 1, "unresolved": []},
                synthesis_settings(),
                RoundBudget(()),
                conversation_context=messages,
            )
        )
    assert _SynthesisProvider.calls == []


def test_requested_write_preserves_original_mutation_contract_with_prior_questions():
    messages = history("Delete the selected customer.")
    _PlannerProvider.calls.clear()
    _PlannerProvider.responses = [json.dumps({"intent": "mutation", "tasks": []})]
    result = asyncio.run(
        plan_request(messages[-1].content, messages, _settings(), RoundBudget(()), {})
    )
    assert result.plan.intent == "mutation" and result.choice_context is None
    assert len(_PlannerProvider.calls) == 1


@pytest.mark.django_db
def test_structured_question_metadata_survives_real_chat_persistence():
    from general_manager.chat.models import (
        ChatConversation,
        append_chat_message,
        get_conversation_messages,
        provider_messages_from_context,
    )

    messages = history()
    conversation = ChatConversation.for_actor(user=None, session_key="choice-v41")
    append_chat_message(conversation, role="user", content=messages[0].content)
    metadata = api.clarification_metadata(messages[1].content, messages[0].content)
    append_chat_message(
        conversation,
        role="assistant",
        content=messages[1].content,
        tool_result=metadata,
    )
    append_chat_message(conversation, role="user", content=messages[2].content)
    restored = provider_messages_from_context(get_conversation_messages(conversation))
    assert restored[1].clarification_metadata == metadata
    assert api.choice_questions(restored) == api.choice_questions(messages)
    corrupted = deepcopy(metadata)
    corrupted["gm_clarification"]["topics"] = ["unit"]
    from dataclasses import replace

    restored[1] = replace(restored[1], clarification_metadata=corrupted)
    with pytest.raises(api.ChoiceValidationError):
        api.choice_questions(restored)
