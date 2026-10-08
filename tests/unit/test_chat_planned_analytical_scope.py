"""Approved analytical defaults must reach both phases without rewriting evidence."""

import json
from copy import deepcopy
import pytest
from general_manager.chat.planned import contract
from general_manager.chat.planned.planner import _request_messages
from general_manager.chat.planned.synthesis import _messages
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    canonical_call_identity,
)
from general_manager.chat.providers.base import Message


def test_ranking_default_is_shared_and_keeps_real_user_filters():
    rule = contract.RANKING_POPULATION_RULE
    assert "all accessible records" in rule
    assert "existing explicit user filters remain binding" in rule
    assert "Do not ask for a population" in rule
    prior = [Message("user", "Only assets owned by Delta; include closed records.")]
    query = "Rank those assets by booked value next calendar year."
    planner = _request_messages(query, prior, {}, correction=False)
    synthesis = _messages(query, (), {}, "", prior)
    for messages in (planner, synthesis):
        assert rule in " ".join(m.content for m in messages if m.role == "system")
        reference = json.loads(messages[-1].content.split("=", 1)[1])
        assert reference["conversation_context"] == [
            {"role": "user", "content": prior[0].content}
        ]
        assert (reference["original_request"]) == query


@pytest.mark.parametrize("clock_year", [2032, 2051])
@pytest.mark.parametrize("period_change", [False, True])
def test_explicit_current_year_and_later_period_change_reach_synthesis_exactly(
    clock_year, period_change
):
    rule = contract.CALENDAR_SCOPE_RULE
    assert "Explicit requested periods take precedence" in rule
    assert "never remove it" in rule
    assert "do not force a clarification" in rule
    assert "actually excluded" in rule
    prior = [
        Message(
            "user",
            f"Compare the completed calendar years {clock_year - 3} through {clock_year - 1}.",
        )
    ]
    latest = f"Include the current year {clock_year} as well."
    start = f"{clock_year - 3}-01-01"
    if period_change:
        prior.append(Message("user", latest))
        latest = f"Now use only {clock_year}, keeping the owner filter."
        start = f"{clock_year}-01-01"
    args = {
        "manager": "Asset",
        "fields": ["id", "year", "quantity"],
        "arguments": {
            "filter": {"ownerId": 7, "year_Gte": int(start[:4]), "year_Lte": clock_year}
        },
    }
    output = {
        "data": [{"id": 4, "year": clock_year, "quantity": 19}],
        "total_count": 1,
        "has_more": False,
    }
    record = EvidenceRecord.create(
        "q",
        "read",
        "query",
        canonical_call_identity("query", args),
        {"manager": "Asset", "tool": "query"},
        output,
    )
    before = deepcopy(record.payload())
    configured = (
        f"Business clock: {clock_year}-05-09T00:00:00Z. Calendar years use UTC."
    )
    for messages in (
        _request_messages(
            latest, prior, {}, correction=False, configured_context=configured
        ),
        _messages(latest, (record,), {}, configured, prior),
    ):
        systems = " ".join(m.content for m in messages if m.role == "system")
        assert rule in systems
        assert configured in systems
        reference = json.loads(messages[-1].content.split("=", 1)[1])
        assert (reference["original_request"]) == latest
        assert reference["conversation_context"][-1]["content"] == prior[-1].content
    synthesis_reference = json.loads(
        _messages(latest, (record,), {}, configured, prior)[-1].content.split("=", 1)[1]
    )
    evidence = synthesis_reference["resolved_evidence"][0]
    assert evidence["call_identity"] == record.call_identity
    assert evidence["payload"] == output
    assert record.payload() == before
    assert "2026" not in rule
