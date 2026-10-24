"""Forecast assumptions remain precise, closed and bound to visible user choices."""

import json
from copy import deepcopy

import pytest

from experiments.gm_eval.adjudication import _matches_shape
from general_manager.chat.planned import choice_context as api
from general_manager.chat.planned.synthesis import (
    _messages,
    _parse_result,
    _InvalidSynthesisResponseError,
)
from general_manager.chat.providers.base import Message
from tests.unit.test_chat_planned_choice_context import envelope
from tests.unit.test_chat_planned_synthesis import _store

TOPICS = ["forecast_method", "future_pricing", "reporting_currency"]
QUESTIONS = {
    "en": "Which forecast method should I use? Which future prices or pricing assumptions should I use? Which reporting currency should I use?",
    "de": "Welche Prognosemethode soll ich verwenden? Welche zukünftigen Preise oder Preisannahmen soll ich verwenden? Welche Berichtswährung soll ich verwenden?",
    "fr": "Quelle méthode de prévision dois-je utiliser ? Quels prix futurs ou quelles hypothèses de prix dois-je utiliser ? Quelle devise de présentation dois-je utiliser ?",
}


@pytest.mark.parametrize("language", QUESTIONS)
def test_precise_forecast_questions_agree_with_published_schema(language):
    value = {"clarification": {"language": language, "requirements": TOPICS}}
    schema = json.loads(
        _messages("Clarify forecast assumptions", _store().records, {})[
            -1
        ].content.split("=", 1)[1]
    )["required_json_schema"]
    assert _matches_shape(value, schema)
    assert _parse_result(json.dumps(value), frozenset({"schema-1"})) == (
        QUESTIONS[language],
        (),
    )
    bad = deepcopy(value)
    bad["clarification"]["answer"] = "A record wins with 42 EUR."
    assert not _matches_shape(bad, schema)
    with pytest.raises(_InvalidSynthesisResponseError):
        _parse_result(json.dumps(bad), frozenset({"schema-1"}))


def history(reply="Use a linear forecast, constant current prices and EUR."):
    return [
        Message("user", "Forecast future revenue."),
        Message("assistant", QUESTIONS["en"]),
        Message("user", reply),
    ]


@pytest.mark.parametrize("topic", TOPICS)
@pytest.mark.parametrize("new_question", [False, True])
def test_answered_forecast_choice_cannot_be_reopened_in_same_scope(topic, new_question):
    messages = history()
    questions = api.choice_questions(messages)
    assert [question["topic"] for question in questions] == TOPICS
    value = envelope(messages)
    question = next(q for q in questions if q["topic"] == topic)
    value["clarification_requests"] = [
        {
            "topic": topic,
            "question_id": None if new_question else question["question_id"],
            "scope_quote": {"index": 2, "quote": messages[2].content},
        }
    ]
    with pytest.raises(api.ChoiceValidationError):
        api.validate_choices(value, messages)


def test_partial_forecast_reply_keeps_only_the_bound_open_topic():
    messages = history(
        "Keep current prices and report EUR; I have not chosen a forecast method."
    )
    value = envelope(messages)
    assert len(value["choices"]) == 3
    value["choices"][0].update(state="open", user_quotes=[])
    value["clarification_requests"] = [
        {
            "topic": "forecast_method",
            "question_id": value["choices"][0]["question_id"],
            "scope_quote": {"index": 2, "quote": messages[2].content},
        }
    ]
    context = api.validate_choices(value, messages)
    context.check_clarification(["forecast_method"])
    with pytest.raises(api.ChoiceValidationError):
        context.check_clarification(["future_pricing"])
    forged = deepcopy(value)
    forged["choices"][1]["user_quotes"][0]["quote"] = "Use invented future prices."
    with pytest.raises(api.ChoiceValidationError):
        api.validate_choices(forged, messages)


def test_metric_scope_change_can_make_price_assumptions_irrelevant():
    messages = history("Use shipment quantities only; do not estimate prices.")
    value = envelope(messages, applicability="changed_scope")
    assert len(value["choices"]) == 3
    context = api.validate_choices(value, messages)
    assert context.as_mapping()["clarification_requests"] == []
    with pytest.raises(api.ChoiceValidationError):
        context.check_clarification(["future_pricing"])
