"""Additive abstention never repairs or conceals an existing candidate failure."""

from copy import deepcopy
import json
from typing import Any

import pytest

from experiments.gm_eval import adjudication as api
from experiments.gm_eval.reporting import case_result, summarize
from tests.experiments.test_gm_eval_adjudication import expectation, response, saved_run


def review(
    *, verified: bool = False, wrong: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    run = saved_run()
    turn = run["turns"][0]
    turn["answer"] += " Aurora also holds 7 kg in reserve."
    turn["events"][-2]["content"] = turn["answer"]
    request = api.build_adjudication_request(expectation(), run, 0)
    value = json.loads(response(request, wrong=wrong))
    value["additional_quantities"] = {
        "reviewed_entire_answer": True,
        "claims": [
            {
                "answer_quote": "Aurora also holds 7 kg in reserve.",
                "verifiable": verified,
                "reason": "No reserve measurement in the eligible evidence.",
                "evidence_ids": [row["id"] for row in request["packet"]["evidence"]]
                if verified
                else [],
            }
        ],
    }
    return request, value


def score(request: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    parsed = api.parse_adjudication_response(
        request, json.dumps(value), judge_id="offline-contract-test"
    )
    return api.score_adjudicated(
        expectation(), parsed, registry={"Shipment": "Database", "Project": "Database"}
    )


def test_additional_unverifiable_quantity_abstains_without_a_second_judge() -> None:
    request, value = review()
    parsed = api.parse_adjudication_response(
        request, json.dumps(value), judge_id="offline-contract-test"
    )
    assert parsed["status"] == "completed"
    assert parsed["judge_calls"] == 0
    result = score(request, value)
    assert result["primary_failure"] == "unscored"
    assert not result["passed"] and not result["scored"]
    assert result["quantity_coverage"]["status"] == "unverifiable"


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "bad_quote",
        "bad_reference",
        "incomplete",
        "wrong_type",
        "numeric_true",
    ],
)
@pytest.mark.parametrize("wrong", [False, True])
def test_bad_additional_coverage_never_discards_an_existing_failure(
    damage: str, wrong: bool
) -> None:
    request, value = review(wrong=wrong)
    if damage == "missing":
        value.pop("additional_quantities")
    elif damage == "bad_quote":
        value["additional_quantities"]["claims"][0]["answer_quote"] = (
            "Not in this answer"
        )
    elif damage == "bad_reference":
        value["additional_quantities"]["claims"][0].update(
            verifiable=True, evidence_ids=["absent-source"]
        )
    elif damage == "incomplete":
        value["additional_quantities"]["reviewed_entire_answer"] = False
    elif damage == "numeric_true":
        value["additional_quantities"]["reviewed_entire_answer"] = 1
    else:
        value["additional_quantities"] = "looks fine"
    result = score(request, value)
    assert result["primary_failure"] == ("model_task_failure" if wrong else "unscored")
    assert not result["passed"]


def test_existing_answer_supported_failure_stays_failed() -> None:
    request, value = review()
    value["semantic_checks"]["answer_supported"]["passed"] = False
    assert score(request, value)["primary_failure"] == "model_task_failure"


def test_absent_additional_claims_preserve_a_pass() -> None:
    request = api.build_adjudication_request(expectation(), saved_run(), 0)
    value = json.loads(response(request))
    assert score(request, value)["passed"]


def test_stale_quantity_binding_cannot_authorize_a_pass() -> None:
    request, value = review(verified=True)
    parsed = api.parse_adjudication_response(
        request, json.dumps(value), judge_id="offline-contract-test"
    )
    assert parsed["quantity_coverage"]["status"] == "verified"
    parsed["quantity_coverage"]["answer_sha256"] = "0" * 64
    result = api.score_adjudicated(
        expectation(), parsed, registry={"Shipment": "Database", "Project": "Database"}
    )
    assert result["primary_failure"] == "unscored"


@pytest.mark.parametrize(
    "damage", ["status", "missing_review", "bad_quote", "bad_reference"]
)
@pytest.mark.parametrize("wrong", [False, True])
def test_saved_coverage_is_revalidated_before_accepting_verified(
    damage: str, wrong: bool
) -> None:
    request, value = review(wrong=wrong)
    parsed = api.parse_adjudication_response(
        request, json.dumps(value), judge_id="offline-contract-test"
    )
    coverage = parsed["quantity_coverage"]
    coverage["status"] = "verified"
    if damage == "missing_review":
        coverage.pop("review")
    elif damage == "bad_quote":
        coverage["review"]["claims"][0].update(
            verifiable=True,
            answer_quote="stale answer",
            evidence_ids=[request["packet"]["evidence"][0]["id"]],
        )
    elif damage == "bad_reference":
        coverage["review"]["claims"][0].update(
            verifiable=True, evidence_ids=["foreign-source"]
        )
    result = api.score_adjudicated(
        expectation(), parsed, registry={"Shipment": "Database", "Project": "Database"}
    )
    assert result["primary_failure"] == ("model_task_failure" if wrong else "unscored")


def test_verified_additional_quantity_uses_the_existing_single_judge_call() -> None:
    import asyncio
    from experiments.gm_eval.profiles import siwc_factory
    from tests.experiments.test_gm_eval_quantity_basis_contract import WireReplay

    run = saved_run()
    turn = run["turns"][0]
    extra = "This shipment contains 120 pieces."
    turn["answer"] += " " + extra
    turn["events"][-2]["content"] = turn["answer"]
    request = api.build_adjudication_request(expectation(), run, 0)
    value = json.loads(response(request))
    value["additional_quantities"]["claims"] = [
        {
            "answer_quote": extra,
            "verifiable": True,
            "reason": "The same Shipment S01 quantity is 120 pieces.",
            "evidence_ids": [row["id"] for row in request["packet"]["evidence"]],
        }
    ]
    replay = WireReplay(json.dumps(value))
    provider = siwc_factory(replay)({"model": "offline", "reasoning": "medium"})
    parsed = asyncio.run(
        api.adjudicate_turn(request, judge_id="offline-wire", judge=provider)
    )
    assert len(replay.bodies) == parsed["judge_calls"] == 1
    assert api.score_adjudicated(
        expectation(), parsed, registry={"Shipment": "Database", "Project": "Database"}
    )["passed"]
    wire = json.dumps(replay.bodies[0])
    assert "SAME subject/entity, metric, unit, period/population and arithmetic" in wire
    assert "A matching number in another row, field or tool call is not support" in wire


def test_archived_request_retains_original_quantity_policy_and_instruction() -> None:
    old = expectation()
    old["adjudication_schema_version"] = "1.5"
    packet = api.build_adjudication_request(old, saved_run(), 0)
    assert packet["schema_version"] == "1.5"
    assert (
        "additional_quantities"
        not in packet["packet"]["response_schema"]["oneOf"][0]["properties"]
    )
    assert api._QUANTITY_INSTRUCTION not in api.judge_messages(packet)[0].content
    parsed = api.parse_adjudication_response(
        packet, response(packet), judge_id="offline-archive"
    )
    assert "quantity_coverage" not in parsed
    assert api.score_adjudicated(
        old, parsed, registry={"Shipment": "Database", "Project": "Database"}
    )["passed"]


@pytest.mark.parametrize("reverse", [False, True])
def test_case_failure_precedes_new_abstention_and_denominator_is_retained(
    reverse: bool,
) -> None:
    request, value = review()
    unknown = score(request, value)
    failed = score(*review(wrong=True))
    failed["turn_index"] = 1
    turns = [unknown, failed]
    if reverse:
        turns.reverse()
    result = case_result(turns, expected_turns=2)
    assert result["status"] == "model_task_failure"
    rows = [dict(deepcopy(result), case_id=f"E{index:03d}") for index in range(1, 122)]
    report = summarize(rows, mode="live", catalog_size=121)
    assert report["catalog_cases"] == report["attempted_cases"] == 121
    assert report["status_counts"] == {"model_task_failure": 121}
