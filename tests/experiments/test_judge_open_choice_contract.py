"""Bound open decisions guide semantics without supplying candidate claims."""

from copy import deepcopy
import pytest

from experiments.gm_eval import adjudication
from experiments.gm_eval.scoring import make_judge_packet
from tests.experiments.test_gm_eval_adjudication import expectation, saved_run
from tests.experiments.test_gm_eval_judge_precision_r26 import (
    _packet,
    _claims,
    _parse_score,
)


def test_open_choice_groups_reach_judge_without_gold_values_or_answer_repair():
    contract = expectation("E055")
    original = deepcopy(contract)
    packet = make_judge_packet(contract, {"answer": "What period?", "trace": {}})
    assert packet["clarification_contract"] == {
        "version": "bound-open-choices/1",
        "required_topic_groups": [["criterion", "metric"], ["horizon"]],
    }
    assert packet["answer"] == "What period?"
    assert contract == original
    assert "facts" not in packet
    assert packet["semantic_checks"] == contract["semantic_checks"]


def test_defined_followup_never_gets_an_open_choice_contract():
    packet = make_judge_packet(expectation("E055", 1), {"answer": "Wrong", "trace": {}})
    assert "clarification_contract" not in packet


def test_new_judge_guidance_is_request_bound_and_archived_messages_stay_legacy():
    contract = expectation("E055")
    run = saved_run()
    run["case_id"] = contract["case_id"]
    run["turns"][0]["user"] = "What is the outlook for this customer?"
    run["turns"][0]["answer"] = "Which metric and period?"
    for event in run["turns"][0]["events"]:
        if event.get("type") == "text_chunk":
            event["content"] = run["turns"][0]["answer"]
    request = adjudication.build_adjudication_request(contract, run, 0)
    messages = adjudication.judge_messages(request)
    assert "BOUND_OPEN_CHOICE_CONTRACT" in messages[0].content
    legacy = deepcopy(request)
    legacy["packet"].pop("clarification_contract")
    legacy["request_sha256"] = adjudication._request_digest(legacy)
    assert (
        "BOUND_OPEN_CHOICE_CONTRACT"
        not in adjudication.judge_messages(legacy)[0].content
    )
    assert request["request_sha256"] != legacy["request_sha256"]


def test_required_topic_is_not_filled_from_contract_or_automatic_semantic_pass():
    contract = expectation("E055")
    packet = _packet(contract, "What period?", [])
    value = _claims(
        packet,
        {
            "response_mode": "clarification",
            "clarification_topics": ["horizon"],
            "result_assertion": "none",
        },
    )
    parsed, score = _parse_score(contract, packet, value)
    assert parsed["status"] == "completed"
    assert score["dimensions"]["Q"]["status"] == "fail"
    value["semantic_checks"]["clarification_suitable"].update(
        passed=False,
        reason="An extra question was already resolved by the visible user.",
    )
    parsed, score = _parse_score(contract, packet, value)
    assert (
        parsed["semantic_judgment"]["checks"]["clarification_suitable"]["passed"]
        is False
    )
    assert score["dimensions"]["Q"]["status"] == "fail"


@pytest.mark.parametrize("damage", ["version", "empty", "unknown", "duplicate"])
def test_malformed_open_choice_contract_is_rejected_before_judge(damage):
    contract = expectation("E055")
    packet = _packet(contract, "Which metric and period?", [])
    scope = packet["packet"]["clarification_contract"]
    if damage == "version":
        scope["version"] = "unknown"
    else:
        scope["required_topic_groups"] = {
            "empty": [],
            "unknown": [["hidden_topic"]],
            "duplicate": [["metric", "metric"]],
        }[damage]
    packet["request_sha256"] = adjudication._request_digest(packet)
    with pytest.raises(adjudication.AdjudicationError):
        adjudication.judge_messages(packet)


def test_changed_open_choices_without_rehash_are_rejected():
    packet = _packet(expectation("E055"), "Which metric and period?", [])
    packet["packet"]["clarification_contract"]["required_topic_groups"] = [["unit"]]
    with pytest.raises(adjudication.AdjudicationError):
        adjudication.judge_messages(packet)
