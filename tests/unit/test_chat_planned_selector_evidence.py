"""Concrete selector questions use current selected query identities, never free claims."""

import asyncio
from copy import deepcopy
import json

import pytest

from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    canonical_call_identity,
)
from general_manager.chat.planned.synthesis import synthesize_answer
from general_manager.chat.providers.base import Message
from tests.unit.test_chat_planned_synthesis import _settings, _SynthesisProvider


def choices_store(*, complete=True, selected=True, field="name", identity="canonical"):
    store = EvidenceStore()
    args = {
        "manager": "Project",
        "fields": ["id", "code", field] if selected else ["id"],
        "filters": {"name_Icontains": "a"},
    }
    store.add(
        EvidenceRecord.create(
            "choices",
            "task_1",
            "query",
            canonical_call_identity("query", args)
            if identity == "canonical"
            else "query",
            {"tool": "query", "manager": "Project"},
            {
                "data": [
                    {"id": 1, "code": "P1", "name": "Alpha", "amount": 42},
                    {"id": 2, "code": "P2", "name": "Beta", "amount": 99},
                ],
                "complete": complete,
                "has_more": not complete,
                "total_count": 2,
            },
        )
    )
    return store


def selector(language="en", evidence_id="choices", field="name"):
    return {
        "clarification": {
            "language": language,
            "requirements": ["record_selector"],
            "selector": {"evidence_id": evidence_id, "field": field},
        }
    }


def run(responses, store=None, **kwargs):
    _SynthesisProvider.calls = []
    _SynthesisProvider.responses = [json.dumps(x) for x in responses]
    return asyncio.run(
        synthesize_answer(
            "Which of these should I use?",
            store or choices_store(),
            {},
            _settings(),
            RoundBudget(()),
            **kwargs,
        )
    )


@pytest.mark.parametrize(
    "language,question",
    [
        ("en", 'Which Project record do you mean: "Alpha", "Beta"?'),
        ("de", 'Welchen Datensatz aus Project meinst du: "Alpha", "Beta"?'),
        ("fr", 'De quel enregistrement de Project s\'agit-il : "Alpha", "Beta" ?'),
    ],
)
def test_genuine_identity_ambiguity_renders_current_choices_and_returns_grounding(
    language, question
):
    result = run([selector(language)] * 2)
    assert result.answer == question
    assert result.evidence_ids == ("choices",)
    assert len(_SynthesisProvider.calls) == 1


def test_clear_followup_rejects_bare_selector_and_uses_existing_bounded_fallback():
    bare = {"clarification": {"language": "en", "requirements": ["record_selector"]}}
    answer = {"answer": "Alpha now has 42 units.", "evidence_ids": ["choices"]}
    context = [
        Message(role="user", content="List these projects."),
        Message(
            role="assistant", content="Alpha and Beta. Alpha used to have 12 units."
        ),
    ]
    result = run([bare, answer], conversation_context=context)
    assert result.answer == answer["answer"] and result.evidence_ids == ("choices",)
    assert len(_SynthesisProvider.calls) == 2
    reference = json.loads(_SynthesisProvider.calls[0][-1].content.split("=", 1)[1])
    assert reference["conversation_context"][1]["content"] == context[1].content
    assert reference["resolved_evidence"][0]["payload"]["data"][0]["amount"] == 42


@pytest.mark.parametrize(
    "invalid",
    [
        {"evidence_id": "invented", "field": "name"},
        {"evidence_id": "choices", "field": "amount"},
        {"evidence_id": "choices", "field": "project.name"},
        {
            "evidence_id": "choices",
            "field": "name",
            "values": ["Invented", "42 EUR winner"],
        },
    ],
)
def test_invented_or_nonidentity_selector_never_reaches_question(invalid):
    value = selector()
    value["clarification"]["selector"] = invalid
    answer = {"answer": "Verified current records.", "evidence_ids": ["choices"]}
    result = run([value, answer])
    assert result.answer == answer["answer"]
    assert len(_SynthesisProvider.calls) == 2


@pytest.mark.parametrize(
    "store",
    [
        choices_store(complete=False),
        choices_store(selected=False),
        choices_store(identity="legacy"),
    ],
)
def test_partial_unselected_or_unbound_query_cannot_supply_identity_choices(store):
    answer = {"answer": "Verified current records.", "evidence_ids": ["choices"]}
    result = run([selector(), answer], store)
    assert result.answer == answer["answer"]
    assert len(_SynthesisProvider.calls) == 2


def test_unresolved_other_user_decisions_remain_valid_even_with_complete_data():
    value = {"clarification": {"language": "en", "requirements": ["metric", "horizon"]}}
    result = run([value])
    assert (
        result.answer
        == "Which metric should I use? What time period should I consider?"
    )
    assert result.evidence_ids == ()
    assert len(_SynthesisProvider.calls) == 1


def test_numeric_identity_bool_and_missing_labels_are_not_printed_as_choices():
    original = choices_store().records[0]
    for rows in [
        [{"name": "Alpha"}],
        [{"name": "Alpha"}, {"name": "Alpha"}],
        [{"name": True}, {"name": "Beta"}],
    ]:
        payload = deepcopy(original.payload())
        payload["data"] = rows
        payload["total_count"] = len(rows)
        store = EvidenceStore()
        store.add(
            EvidenceRecord.create(
                "choices",
                "task_1",
                "query",
                original.call_identity,
                original.provenance,
                payload,
            )
        )
        answer = {"answer": "Need verified identities.", "evidence_ids": ["choices"]}
        assert run([selector(), answer], store).answer == answer["answer"]


def test_selector_rejects_query_with_wrong_tool_provenance():
    original = choices_store().records[0]
    store = EvidenceStore()
    store.add(
        EvidenceRecord.create(
            "choices",
            "task_1",
            "query",
            original.call_identity,
            {"tool": "calculate", "manager": "Project"},
            original.payload(),
        )
    )
    answer = {"answer": "Verified current records.", "evidence_ids": ["choices"]}
    assert run([selector(), answer], store).answer == answer["answer"]
