"""Behavior is judged semantically with exact current-answer/context binding."""

import json
import pytest
from experiments.gm_eval import adjudication
from tests.experiments.test_gm_eval_adjudication import expectation, saved_run
from tests.experiments.test_gm_eval_scoring import REGISTRY


def request(answer="The ranking follows the requested 2027 revenue metric."):
    run = saved_run(two_turns=True)
    run["case_id"] = "E007"
    run["turns"][0]["user"] = "What is our most important project?"
    turn = run["turns"][1]
    turn["user"] = "Rank by planned net revenue in euros in 2027."
    turn["history"] = [
        {"role": "user", "content": run["turns"][0]["user"]},
        {"role": "assistant", "content": "Which metric and time period should I use?"},
        {"role": "user", "content": turn["user"]},
    ]
    turn["answer"] = answer
    turn["events"] = [{"type": "text_chunk", "content": answer}, {"type": "done"}]
    return adjudication.build_adjudication_request(expectation("E007", 1), run, 1)


def response(req, *, passed=True, repetitions=None):
    packet = req["packet"]
    assert "no_repeated_clarification" in {c["id"] for c in packet["semantic_checks"]}
    checks = {
        c["id"]: {
            "passed": True,
            "reason": "Explicit offline semantic control.",
            "evidence_ids": ["answer"],
        }
        for c in packet["semantic_checks"]
    }
    checks["no_repeated_clarification"].update(
        passed=passed,
        context_sha256=packet["context_sha256"],
        repetitions=repetitions or [],
    )
    facts = {name: None for name in packet["fact_fields"]}
    return {
        "schema_version": req["schema_version"],
        "status": "completed",
        "request_sha256": req["request_sha256"],
        "answer_sha256": packet["answer_sha256"],
        "facts": facts,
        "fact_support": {
            name: {"status": "absent", "answer_quotes": [], "evidence_ids": []}
            for name in facts
        },
        "citations": [],
        "semantic_checks": checks,
    }


def repetition(question="Which year should I use?"):
    return {
        "answer_quote": question,
        "resolved_by": {"source": "visible_history", "index": 2, "quote": "in 2027"},
    }


def parse(req, value):
    return adjudication.parse_adjudication_response(
        req, json.dumps(value), judge_id="offline-control"
    )


def semantic_status(req, result):
    scored = adjudication.score_adjudicated(
        expectation("E007", 1), result, registry=REGISTRY
    )
    return next(
        c["status"]
        for c in scored["dimensions"]["Q"]["checks"]
        if c["field"] == "no_repeated_clarification"
    )


def test_followup_contract_uses_mandatory_behavior_not_absent_fact():
    contract = expectation("E007", 1)
    assert "clarification_repeated" not in contract["facts"]
    assert any(
        c["id"] == "no_repeated_clarification" and c["dimension"] == "Q"
        for c in contract["semantic_checks"]
    )


@pytest.mark.parametrize(
    "answer",
    [
        "The ranking follows the requested 2027 revenue metric.",
        "Which project population should I include?",
    ],
)
def test_no_question_and_new_unresolved_question_do_not_imply_repetition(answer):
    req = request(answer)
    result = parse(req, response(req))
    assert result["status"] == "completed"
    assert semantic_status(req, result) == "pass"


def test_repeating_an_answered_question_fails_q():
    req = request("Which year should I use?")
    result = parse(req, response(req, passed=False, repetitions=[repetition()]))
    assert result["status"] == "completed"
    assert semantic_status(req, result) == "fail"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "null",
        "true_with_repetition",
        "false_without_repetition",
        "fake_answer_quote",
        "fake_context_quote",
        "assistant_choice",
        "missing_binding",
        "stale_binding",
        "unknown_field",
        "legacy_fact",
        "missing_reason",
    ],
)
def test_missing_contradictory_or_unbound_behavior_is_unscored(mutation):
    req = request("Which year should I use?")
    value = response(req, passed=False, repetitions=[repetition()])
    check = value["semantic_checks"]["no_repeated_clarification"]
    if mutation == "missing":
        del value["semantic_checks"]["no_repeated_clarification"]
    elif mutation == "null":
        check["passed"] = None
    elif mutation == "true_with_repetition":
        check["passed"] = True
    elif mutation == "false_without_repetition":
        check["repetitions"] = []
    elif mutation == "fake_answer_quote":
        check["repetitions"][0]["answer_quote"] = "P02 wins"
    elif mutation == "fake_context_quote":
        check["repetitions"][0]["resolved_by"]["quote"] = "in 2029"
    elif mutation == "assistant_choice":
        check["repetitions"][0]["resolved_by"] = {
            "source": "visible_history",
            "index": 1,
            "quote": "Which metric",
        }
    elif mutation == "missing_binding":
        del check["context_sha256"]
    elif mutation == "stale_binding":
        check["context_sha256"] = "0" * 64
    elif mutation == "unknown_field":
        check["claim"] = "P02 wins"
    elif mutation == "legacy_fact":
        value["facts"]["clarification_repeated"] = True
    elif mutation == "missing_reason":
        check["reason"] = ""
    assert parse(req, value)["status"] == "judge_failure"


@pytest.mark.parametrize("changed", ["question", "history", "answer", "judgment"])
def test_scorer_rejects_stale_binding_even_after_initial_validation(changed):
    req = request()
    result = parse(req, response(req))
    assert result["status"] == "completed"
    if changed == "question":
        result["observation"]["trace"]["conversation_context"]["current_question"] = (
            "Use 2029."
        )
    elif changed == "history":
        result["observation"]["trace"]["conversation_context"]["visible_history"][2][
            "content"
        ] = "Use 2029."
    elif changed == "answer":
        result["observation"]["answer"] = "Which year?"
    else:
        result["semantic_judgment"]["checks"]["no_repeated_clarification"][
            "context_sha256"
        ] = "0" * 64
    assert semantic_status(req, result) == "unscored"


@pytest.mark.parametrize(
    "question", ["Welches Jahr soll ich verwenden?", "Quelle année dois-je utiliser ?"]
)
def test_repetition_witness_is_language_independent(question):
    req = request(question)
    witness = {
        "answer_quote": question,
        "resolved_by": {
            "source": "current_question",
            "index": None,
            "quote": "in 2027",
        },
    }
    result = parse(req, response(req, passed=False, repetitions=[witness]))
    assert result["status"] == "completed"
    assert semantic_status(req, result) == "fail"


@pytest.mark.parametrize(
    "damage",
    [
        "boolean_index",
        "negative_index",
        "fractional_index",
        "duplicate_witness",
        "duplicate_reference",
        "missing_answer_reference",
    ],
)
def test_invalid_witness_indices_and_references_remain_unscored(damage):
    req = request("Which year should I use?")
    value = response(req, passed=False, repetitions=[repetition()])
    check = value["semantic_checks"]["no_repeated_clarification"]
    if damage in {"boolean_index", "negative_index", "fractional_index"}:
        check["repetitions"][0]["resolved_by"]["index"] = {
            "boolean_index": True,
            "negative_index": -1,
            "fractional_index": 2.5,
        }[damage]
    elif damage == "duplicate_witness":
        check["repetitions"].append(repetition())
    elif damage == "duplicate_reference":
        check["evidence_ids"] = ["answer", "answer"]
    else:
        check["evidence_ids"] = []
    assert parse(req, value)["status"] == "judge_failure"


def test_eval_source_manifest_covers_new_behavior_contracts():
    from experiments.gm_eval.cli import source_manifest

    sources = source_manifest()
    assert "experiments/gm_eval/semantic_contracts.py" in sources
    assert "src/general_manager/chat/planned/clarification.py" in sources
    assert "src/general_manager/chat/planned/synthesis.py" in sources
