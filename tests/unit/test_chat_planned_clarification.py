"""A structured clarification cannot smuggle free result claims past grounding."""

import asyncio
import json
from copy import deepcopy
import pytest
from experiments.gm_eval.adjudication import _matches_shape
from general_manager.chat.planned.synthesis import (
    _messages,
    _parse_result,
    _InvalidSynthesisResponseError,
    synthesize_answer,
)
from general_manager.chat.planned.budget import RoundBudget
from tests.unit.test_chat_planned_synthesis import _settings, _store, _SynthesisProvider

QUESTIONS = {
    "en": "Which criterion should I use? What time period should I consider?",
    "de": "Welches Kriterium soll ich verwenden? Welchen Zeitraum soll ich betrachten?",
    "fr": "Quel critère dois-je utiliser ? Quelle période dois-je considérer ?",
}


def clarification(language="en"):
    return {
        "clarification": {
            "language": language,
            "requirements": ["criterion", "horizon"],
        }
    }


@pytest.mark.parametrize("language", QUESTIONS)
def test_structured_requirements_render_without_free_claims(language):
    answer, ids = _parse_result(
        json.dumps(clarification(language)), frozenset({"schema-1"})
    )
    assert answer == QUESTIONS[language]
    assert ids == ()


@pytest.mark.parametrize("language", QUESTIONS)
def test_invalid_free_reply_uses_existing_fallback_for_structured_question(language):
    _SynthesisProvider.calls = []
    _SynthesisProvider.responses = [
        json.dumps({"answer": "Is P02 the winner with 42 EUR?", "evidence_ids": []}),
        json.dumps(clarification(language)),
    ]
    result = asyncio.run(
        synthesize_answer("Clarify", _store(), {}, _settings(), RoundBudget(()))
    )
    assert result.answer == QUESTIONS[language] and result.evidence_ids == ()
    assert len(_SynthesisProvider.calls) == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"answer": "P02 wins. Which period?", "evidence_ids": []},
        {"answer": "42?", "evidence_ids": []},
        {"mode": "clarification", "answer": "P02 wins?", "evidence_ids": []},
        {
            "clarification": {
                "language": "en",
                "requirements": ["P02 wins with 42 EUR?"],
            }
        },
        {"clarification": {"language": "en", "requirements": [42]}},
        {"clarification": {"language": "en", "requirements": [{"criterion": "P02"}]}},
        {
            "clarification": {
                "language": "en",
                "requirements": ["criterion", "criterion"],
            }
        },
        {"clarification": {"language": "en", "requirements": []}},
        {
            "clarification": {
                "language": "en",
                "requirements": ["criterion"],
                "answer": "P02 wins",
            }
        },
        {"clarification": {"language": "P02 wins", "requirements": ["criterion"]}},
        {"clarification": {"requirements": ["criterion"]}},
        {"clarification": None},
        {
            "clarification": {"language": "en", "requirements": ["criterion"]},
            "answer": "P02 wins",
        },
        {
            "clarification": {"language": "en", "requirements": ["criterion"]},
            "evidence_ids": ["invented"],
        },
        {"answer": "P02 wins", "evidence_ids": ["invented"]},
        {"answer": "P02 wins", "evidence_ids": ["schema-1", "schema-1"]},
        {
            "answer": "P02 wins",
            "evidence_ids": ["schema-1"],
            "clarification": {"language": "en", "requirements": ["criterion"]},
        },
    ],
)
def test_disguised_results_mixed_shapes_and_invalid_ids_remain_rejected(bad):
    with pytest.raises(_InvalidSynthesisResponseError):
        _parse_result(json.dumps(bad), frozenset({"schema-1"}))


def test_duplicate_json_keys_are_not_a_clarification_bypass():
    with pytest.raises(_InvalidSynthesisResponseError):
        _parse_result(
            '{"clarification":{"language":"en","language":"de","requirements":["criterion"]}}',
            frozenset({"schema-1"}),
        )


def test_published_schema_and_parser_agree_on_answer_reference_admission():
    records = _store().records
    message = _messages("Read", records, {})[-1]
    schema = json.loads(message.content.split("=", 1)[1])["required_json_schema"]
    valid = {"answer": "No parts found.", "evidence_ids": ["ev-query-1"]}
    assert _matches_shape(valid, schema)
    assert _matches_shape(clarification(), schema)
    for ids in ([], ["invented"], ["ev-query-1", "ev-query-1"]):
        bad = deepcopy(valid)
        bad["evidence_ids"] = ids
        assert not _matches_shape(bad, schema)
        with pytest.raises(_InvalidSynthesisResponseError):
            _parse_result(json.dumps(bad), frozenset({"ev-query-1"}))
