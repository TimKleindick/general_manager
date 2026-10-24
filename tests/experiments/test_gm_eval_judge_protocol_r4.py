"""Offline protocol failures must never retain independently scored answers."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from experiments.gm_eval.adjudication import (
    adjudicate_turn,
    build_adjudication_request,
    parse_adjudication_response,
    score_adjudicated,
)
from experiments.gm_eval.catalog import load_catalog
from experiments.gm_eval.oracle import expected_turn
from tests.experiments.test_gm_eval_adjudication import (
    expectation,
    request,
    response,
    saved_run,
)


REGISTRY = {"Shipment": "Database", "Project": "Database"}


def validated_result() -> dict[str, Any]:
    packet = request()
    result = parse_adjudication_response(
        packet, response(packet), judge_id="offline-review"
    )
    assert result["status"] == "completed"
    return result


@pytest.mark.parametrize("coordinate", ["case_id", "turn_index"])
def test_foreign_adjudication_scope_cannot_score_current_answer(
    coordinate: str,
) -> None:
    result = validated_result()
    result[coordinate] = "E999" if coordinate == "case_id" else 99
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "judge_failure"
    assert scored["scored"] is False
    assert scored["passed"] is False
    assert scored["dimensions"]["C"]["status"] == "unscored"
    assert scored["dimensions"]["A"]["status"] == "unscored"
    assert result == original


@pytest.mark.parametrize("turn_index", [False, 0.0, "0", None])
def test_adjudication_turn_binding_requires_integer_type(turn_index: Any) -> None:
    result = validated_result()
    result["turn_index"] = turn_index
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "judge_failure"
    assert scored["scored"] is False
    assert scored["dimensions"]["C"]["status"] == "unscored"
    assert result == original


@pytest.mark.parametrize("observation", [None, ["invalid"], 42, "invalid"])
@pytest.mark.parametrize("status", ["completed", "judge_failure"])
def test_malformed_envelope_observation_fails_closed(
    observation: Any, status: str
) -> None:
    result = validated_result()
    result.update(observation=observation, status=status)
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "judge_failure"
    assert scored["scored"] is False
    assert scored["passed"] is False
    assert scored["dimensions"]["C"]["status"] == "unscored"
    assert result == original


@pytest.mark.parametrize("trace", [True, False, 12, 0, "invalid", "", [1], [], None])
@pytest.mark.parametrize("status", ["completed", "judge_failure"])
def test_nonobject_saved_trace_is_explicit_protocol_failure(
    trace: Any, status: str
) -> None:
    result = validated_result()
    result["status"] = status
    result["observation"]["trace"] = trace
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "judge_failure"
    assert scored["scored"] is False
    assert scored["passed"] is False
    assert scored["dimensions"]["C"]["status"] == "unscored"
    assert scored["dimensions"]["A"]["status"] == "unscored"
    assert any(
        flag["category"] == "judge_failure"
        and flag["phase"] == "adjudication"
        and flag["reason"] == "invalid_adjudication_trace"
        for flag in scored["failures"]
    )
    assert result == original


@pytest.mark.parametrize("missing", [False, True])
def test_empty_or_missing_legacy_trace_remains_incomplete_measurement(
    missing: bool,
) -> None:
    result = validated_result()
    if missing:
        result["observation"].pop("trace")
    else:
        result["observation"]["trace"] = {}
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "judge_failure"
    assert scored["scored"] is False
    assert scored["passed"] is False
    assert not any(
        flag.get("reason") == "invalid_adjudication_trace"
        for flag in scored["failures"]
    )
    assert result == original


def test_invalid_saved_trace_retains_valid_candidate_failure_flags() -> None:
    result = validated_result()
    result["observation"]["trace"] = ["invalid"]
    flag = {
        "category": "transport_failure",
        "phase": "executor",
        "reason": "Recorded candidate failure",
    }
    result["failure_flags"] = [flag]
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "transport_failure"
    assert scored["scored"] is False
    assert scored["passed"] is False
    assert flag in scored["failures"]
    assert any(
        row["category"] == "judge_failure"
        and row["reason"] == "invalid_adjudication_trace"
        for row in scored["failures"]
    )
    assert result == original


@pytest.mark.parametrize("status", ["judge_failure", "ungradable", "unknown", None])
def test_noncompleted_envelope_cannot_reuse_retained_extraction(status: Any) -> None:
    result = validated_result()
    result["status"] = status
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "judge_failure"
    assert scored["scored"] is False
    assert scored["passed"] is False
    assert scored["dimensions"]["C"]["status"] == "unscored"
    assert scored["dimensions"]["A"]["status"] == "unscored"
    assert result == original


def test_judge_failure_flag_cannot_be_overridden_by_completed_envelope() -> None:
    result = validated_result()
    result["failure_flags"] = [
        {
            "category": "judge_failure",
            "phase": "adjudication",
            "reason": "invalid_adjudication_response",
        }
    ]
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "judge_failure"
    assert scored["scored"] is False
    assert scored["dimensions"]["C"]["status"] == "unscored"
    assert scored["dimensions"]["A"]["status"] == "unscored"
    assert result == original


def test_completed_envelope_preserves_valid_numeric_scoring() -> None:
    result = validated_result()
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "passed"
    assert scored["scored"] is True
    assert scored["passed"] is True


@pytest.mark.parametrize(
    "flags",
    [
        None,
        False,
        12,
        {},
        "invalid",
        (),
        [None],
        [12],
        [{}],
        [{"category": "unknown", "reason": "Recorded failure"}],
        [{"category": "judge_failure"}],
        [{"category": "transport_failure", "reason": ""}],
        [{"category": "transport_failure", "reason": "   "}],
        [{"category": "transport_failure", "reason": False}],
        [{"category": "transport_failure", "reason": "Recorded", "phase": None}],
        [{"category": "transport_failure", "reason": "Recorded", "phase": 12}],
        [{"category": "transport_failure", "reason": "Recorded"}, None],
    ],
)
def test_malformed_saved_failure_flags_cannot_score_or_crash(flags: Any) -> None:
    result = validated_result()
    result["failure_flags"] = flags
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "judge_failure"
    assert scored["scored"] is False
    assert scored["passed"] is False
    assert scored["dimensions"]["C"]["status"] == "unscored"
    assert scored["dimensions"]["A"]["status"] == "unscored"
    assert any(
        flag["category"] == "judge_failure"
        and flag["phase"] == "adjudication"
        and flag["reason"] == "invalid_candidate_failure_flags"
        for flag in scored["failures"]
    )
    assert result == original


@pytest.mark.parametrize("missing", [False, True])
def test_empty_or_absent_legacy_failure_flags_preserve_valid_answer(
    missing: bool,
) -> None:
    result = validated_result()
    if missing:
        result.pop("failure_flags")
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "passed"
    assert scored["scored"] is True
    assert scored["passed"] is True
    assert result == original


def test_valid_saved_failure_list_retains_categories_and_optional_phase() -> None:
    result = validated_result()
    flags = [
        {"category": "transport_failure", "phase": "executor", "reason": "Recorded"},
        {"category": "budget_exhausted", "reason": "Recorded candidate budget"},
    ]
    result["failure_flags"] = flags
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "transport_failure"
    assert scored["scored"] is True
    assert scored["passed"] is False
    assert scored["dimensions"]["C"]["status"] == "pass"
    assert all(flag in scored["failures"] for flag in flags)
    assert result == original


def test_valid_false_judgment_remains_candidate_failure() -> None:
    packet = request()
    value = json.loads(response(packet))
    value["semantic_checks"]["answer_supported"]["passed"] = False
    result = parse_adjudication_response(
        packet, json.dumps(value), judge_id="offline-review"
    )
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert result["status"] == "completed"
    assert scored["primary_failure"] == "model_task_failure"
    assert scored["scored"] is True
    assert scored["dimensions"]["A"]["status"] == "fail"


@pytest.mark.parametrize(
    "category",
    [
        "transport_failure",
        "budget_exhausted",
        "interface_capability_gap",
        "harness_failure",
    ],
)
def test_bound_completed_candidate_failure_preserves_valid_answer_facts(
    category: str,
) -> None:
    run = saved_run()
    flags = [{"category": category, "phase": "candidate", "reason": "Recorded failure"}]
    run["turns"][0]["failure_flags"] = flags
    packet = build_adjudication_request(expectation(), run, 0)
    result = parse_adjudication_response(
        packet, response(packet), judge_id="offline-review"
    )
    original = deepcopy(result)
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert result["status"] == "completed"
    assert result["failure_flags"] == flags
    assert scored["primary_failure"] == category
    assert scored["scored"] is True
    assert scored["passed"] is False
    assert scored["dimensions"]["C"]["status"] == "pass"
    assert result == original


@pytest.mark.parametrize("mutation", ["missing", "invalid_status", "invalid_check"])
def test_completed_envelope_cannot_grant_invalid_semantic_judgment(
    mutation: str,
) -> None:
    result = validated_result()
    if mutation == "missing":
        result["semantic_judgment"] = None
    elif mutation == "invalid_status":
        result["semantic_judgment"]["status"] = "ungradable"
    else:
        result["semantic_judgment"]["checks"]["answer_supported"]["passed"] = "true"
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "judge_failure"
    assert scored["scored"] is False
    assert scored["passed"] is False
    assert scored["dimensions"]["A"]["status"] == "unscored"


@pytest.mark.parametrize("mutation", ["answer", "trace", "extraction", "judgment"])
def test_completed_envelope_preserves_stale_binding_rejection(mutation: str) -> None:
    result = validated_result()
    if mutation == "answer":
        result["observation"]["answer"] += " Different answer."
    elif mutation == "trace":
        result["observation"]["trace"]["tool_calls"][0]["output"]["data"][0][
            "quantity"
        ] = "999"
    elif mutation == "extraction":
        result["observation"]["extraction"]["evidence_trace_sha256"] = "0" * 64
    else:
        result["semantic_judgment"]["answer_sha256"] = "0" * 64
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert scored["primary_failure"] == "judge_failure"
    assert scored["scored"] is False
    assert scored["passed"] is False
    assert scored["dimensions"]["C"]["status"] == (
        "pass" if mutation == "judgment" else "unscored"
    )
    assert scored["dimensions"]["A"]["status"] == "unscored"


@pytest.mark.parametrize(
    "mutation",
    [
        "status_label",
        "status_type",
        "version_type",
        "boolean_number",
        "fact_boolean_number",
        "fact_support_label",
        "fact_support_extra",
        "semantic_extra",
        "citation_extra",
        "facts_extra",
        "foreign_answer",
        "foreign_request",
        "foreign_trace",
        "foreign_context",
        "foreign_evidence",
        "unknown_fact_reference",
        "unknown_semantic_reference",
        "missing_semantic_check",
        "missing_fact_support",
    ],
)
def test_malformed_saved_protocol_is_unscored_without_provider(
    mutation: str,
) -> None:
    packet = request()
    value = json.loads(response(packet))
    if mutation == "status_label":
        value["status"] = "PASS"
    elif mutation == "status_type":
        value["status"] = True
    elif mutation == "version_type":
        value["schema_version"] = 1.5
    elif mutation == "boolean_number":
        value["semantic_checks"]["answer_supported"]["passed"] = 1
    elif mutation == "fact_boolean_number":
        value["facts"]["values"]["P01"] = True
    elif mutation == "fact_support_label":
        value["fact_support"]["response_mode"]["status"] = "validated"
    elif mutation == "fact_support_extra":
        value["fact_support"]["response_mode"]["score"] = True
    elif mutation == "semantic_extra":
        value["semantic_checks"]["answer_supported"]["score"] = True
    elif mutation == "citation_extra":
        value["citations"][0]["score"] = True
    elif mutation == "facts_extra":
        value["facts"]["score_override"] = True
    elif mutation == "foreign_answer":
        value["answer_sha256"] = "0" * 64
    elif mutation == "foreign_request":
        value["request_sha256"] = "0" * 64
    elif mutation == "foreign_trace":
        packet["observation"]["trace"]["tool_calls"][0]["output"]["data"][0][
            "quantity"
        ] = "999"
    elif mutation == "foreign_context":
        packet["observation"]["trace"]["conversation_context"]["current_question"] = (
            "A different question"
        )
    elif mutation == "foreign_evidence":
        value["citations"][0]["evidence_id"] = "turn-99/call-1/Shipment"
    elif mutation == "unknown_fact_reference":
        value["fact_support"]["response_mode"]["evidence_ids"] = ["invented"]
    elif mutation == "unknown_semantic_reference":
        value["semantic_checks"]["answer_supported"]["evidence_ids"] = ["invented"]
    elif mutation == "missing_semantic_check":
        value["semantic_checks"].pop("answer_supported")
    else:
        value["fact_support"].pop("response_mode")
    result = asyncio.run(
        adjudicate_turn(packet, response=json.dumps(value), judge_id="offline-review")
    )
    scored = score_adjudicated(expectation(), result, registry=REGISTRY)
    assert result["status"] == "judge_failure"
    assert result["judge_calls"] == 0
    assert result["judge_usage"] is None
    assert result["observation"]["facts"] is None
    assert "extraction" not in result["observation"]
    assert result["semantic_judgment"] is None
    assert scored["primary_failure"] == "judge_failure"
    assert scored["scored"] is False
    assert scored["passed"] is False


@pytest.mark.parametrize("turn_index", [0, 1])
def test_e018_exact_saved_measurement_preserves_unscored_and_pass_turns(
    turn_index: int,
) -> None:
    """No raw failed Judge response was saved: replay only the captured envelope."""
    path = (
        Path(__file__).resolve().parents[3]
        / "judge-review-r4-v1/evidence/E018-report.json"
    )
    if not path.is_file():
        pytest.skip("Exact saved E018 report is external review evidence")
    raw = path.read_bytes()
    report = json.loads(raw)
    result = report["judgments"][turn_index]
    original = deepcopy(result)
    case = next(row for row in load_catalog() if row["id"] == "E018")
    scored = score_adjudicated(
        expected_turn(case, turn_index),
        result,
        registry=report["run"]["registry_interfaces"],
    )
    if turn_index == 0:
        assert report["run"]["turns"][0]["answer"] == "Quel critère dois-je utiliser ?"
        assert result["status"] == "judge_failure"
        assert result["failure_flags"][0]["reason"] == "invalid_adjudication_response"
        assert result["observation"]["facts"] is None
        assert "extraction" not in result["observation"]
        assert result["semantic_judgment"] is None
        assert scored["primary_failure"] == "judge_failure"
        assert scored["scored"] is False
        assert scored["passed"] is False
        assert scored["dimensions"]["Q"]["status"] == "unscored"
        assert scored["dimensions"]["A"]["status"] == "unscored"
    else:
        assert result["status"] == "completed"
        assert scored["primary_failure"] == "passed"
        assert scored["scored"] is True
        assert all(row["status"] == "pass" for row in scored["dimensions"].values())
    for name in (
        "dimensions",
        "primary_failure",
        "classification",
        "secondary_flags",
        "failures",
        "scored",
        "passed",
    ):
        assert scored[name] == report["turn_scores"][turn_index][name]
    assert result == original
    assert path.read_bytes() == raw
    assert report["automatic_retries"] == 0
