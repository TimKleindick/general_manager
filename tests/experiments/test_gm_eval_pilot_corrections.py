"""Independent pilot regressions; all traces and judgments here are synthetic."""

from copy import deepcopy
import json

import pytest

from experiments.gm_eval import adjudication
from experiments.gm_eval.reporting import case_result, summarize
from tests.experiments.test_gm_eval_adjudication import (
    expectation,
    saved_run,
    request,
    response,
)


@pytest.mark.parametrize(
    "statuses,expected,missing",
    [
        (["model_task_failure"], "model_task_failure", [1]),
        (["model_task_failure", "model_task_failure"], "model_task_failure", []),
        (["passed", "model_task_failure"], "model_task_failure", []),
        (["model_task_failure", "transport_failure"], "transport_failure", []),
        (["passed"], "unscored", [1]),
    ],
)
def test_case_classification_includes_task_failures(statuses, expected, missing):
    turns = [
        {
            "case_id": "E009",
            "turn_index": i,
            "primary_failure": status,
            "dimensions": {},
        }
        for i, status in enumerate(statuses)
    ]
    row = case_result(turns, expected_turns=2)
    assert row["status"] == expected
    assert row["missing_turns"] == missing
    summary = summarize([row], mode="live", catalog_size=121)
    assert summary["status_counts"] == {expected: 1}
    assert summary["assessable_cases"] == int(
        expected in {"passed", "model_task_failure"}
    )


def search_run(output=None, *, name="search_managers", matched=True):
    run = saved_run()
    run["trace"]["provider_calls"] = []
    result = [{"manager": "Shipment"}] if output is None else output
    args = (
        {"query": "shipment"} if name == "search_managers" else {"manager": "Shipment"}
    )
    events = [
        {
            "type": "tool_call",
            "task_id": "read",
            "id": "discovery-1",
            "name": name,
            "args": args,
        },
        {
            "type": "tool_result",
            "task_id": "read" if matched else "wrong-task",
            "id": "discovery-1",
            "name": name,
            "result": result,
        },
    ]
    run["turns"][0]["events"][:0] = events
    run["turns"][0]["tool_calls"] = [
        e for e in run["turns"][0]["events"] if e["type"] == "tool_call"
    ]
    run["turns"][0]["tool_results"] = [
        e for e in run["turns"][0]["events"] if e["type"] == "tool_result"
    ]
    return run


@pytest.mark.parametrize(
    "name,output",
    [
        ("search_managers", [{"manager": "Shipment"}]),
        (
            "get_manager_schema",
            {"manager": "Shipment", "fields": {"code": {"type": "String"}}},
        ),
    ],
)
def test_actual_discovery_is_bound_to_successful_call_and_task(name, output):
    packet = adjudication.build_adjudication_request(
        expectation(), search_run(output, name=name), 0
    )
    discovery = packet["observation"]["trace"]["discovery"]
    assert len(discovery) == 1
    assert discovery[0]["candidates"] == ["Shipment"]
    assert discovery[0]["selected"] == ["Shipment", "Project"]
    assert discovery[0]["source_call_id"] == "discovery-1"
    assert discovery[0]["task_id"] == "read"


@pytest.mark.parametrize(
    "output", [{"status": "error"}, [], [{"manager": "WrongManager"}], "Shipment"]
)
def test_failed_or_wrong_search_never_invents_selected_candidates(output):
    packet = adjudication.build_adjudication_request(
        expectation(), search_run(output), 0
    )
    discovery = packet["observation"]["trace"]["discovery"]
    assert not any("Shipment" in row["candidates"] for row in discovery)


def test_wrong_task_result_cannot_supply_discovery():
    with pytest.raises(adjudication.AdjudicationError, match="unpaired_result"):
        adjudication.build_adjudication_request(
            expectation(), search_run(matched=False), 0
        )


def test_incomplete_data_linkage_is_judge_error_not_candidate_failure():
    packet = request()
    raw = json.loads(response(packet))
    raw["fact_support"]["values"]["evidence_ids"] = []
    result = adjudication.parse_adjudication_response(
        packet, json.dumps(raw), judge_id="test"
    )
    assert packet["packet"]["grounding_contract"]["data_fields"]
    assert result["status"] == "judge_failure"
    assert result["failure_flags"][-1]["reason"] == "incomplete_data_fact_support"


def test_explicit_unsupported_claim_remains_candidate_grounding_failure():
    packet = request()
    raw = json.loads(response(packet))
    raw["fact_support"]["values"].update(status="unsupported", evidence_ids=[])
    result = adjudication.parse_adjudication_response(
        packet, json.dumps(raw), judge_id="test"
    )
    assert result["status"] == "completed"
    score = adjudication.score_adjudicated(
        expectation(),
        result,
        registry={"Shipment": "DatabaseInterface", "Project": "DatabaseInterface"},
    )
    assert score["dimensions"]["A"]["status"] == "fail"


@pytest.mark.parametrize(
    "code,category",
    [
        ("synthesis_failed", "model_task_failure"),
        ("deadline_exceeded", "transport_failure"),
        ("budget_exhausted", "budget_exhausted"),
    ],
)
def test_terminal_without_answer_does_not_manufacture_judge_failure(code, category):
    run = saved_run()
    run["turns"][0].update(
        answer="",
        events=[{"type": "error", "code": code}],
        tool_calls=[],
        tool_results=[],
        failure_flags=[{"category": category, "phase": "executor", "reason": code}],
    )
    score = adjudication.score_terminal_no_answer(expectation(), run, 0)
    assert score["primary_failure"] == category
    assert score["judge_requested"] is False
    assert score["failures"] == run["turns"][0]["failure_flags"]
    assert all(row["status"] == "unscored" for row in score["dimensions"].values())
    assert not score["scored"] and not score["passed"]


def test_no_answer_path_rejects_actual_answer_or_missing_terminal_failure():
    run = saved_run()
    with pytest.raises(adjudication.AdjudicationError):
        adjudication.score_terminal_no_answer(expectation(), run, 0)
    run["turns"][0]["answer"] = ""
    with pytest.raises(adjudication.AdjudicationError):
        adjudication.score_terminal_no_answer(expectation(), run, 0)


@pytest.mark.parametrize(
    "visible,durable,expected_count",
    [(True, True, 1), (False, True, 0), (True, False, 0)],
)
def test_historical_discovery_requires_exact_durable_visible_source(
    visible, durable, expected_count
):
    run = search_run()
    first = run["turns"][0]
    output = first["events"][1]["result"]
    if durable:
        first["durable_messages"].append(
            {
                "role": "tool",
                "tool_name": "search_managers",
                "tool_args": {"query": "shipment"},
                "tool_result": deepcopy(output),
            }
        )
    second = deepcopy(saved_run()["turns"][0])
    second["turn"] = 2
    second["history"] = (
        [
            {
                "role": "assistant",
                "content": "Historical tool data (search_managers): "
                + json.dumps(output),
            }
        ]
        if visible
        else []
    )
    run["turns"].append(second)
    contract = expectation()
    contract["turn_index"] = 1
    packet = adjudication.build_adjudication_request(contract, run, 1)
    history = [
        row
        for row in packet["observation"]["trace"]["discovery"]
        if row.get("origin") == "history"
    ]
    assert len(history) == expected_count
    if history:
        assert (
            history[0]["source_turn"] == 0 and history[0]["call_id"] == "turn-1/call-1"
        )


def test_data_support_schema_rejects_present_without_source_ids():
    packet = request()
    value = json.loads(response(packet))
    value["fact_support"]["values"]["evidence_ids"] = []
    assert not adjudication._matches_shape(value, packet["packet"]["response_schema"])


@pytest.mark.parametrize(
    "role",
    [
        "executor",
        "fallback",
        "simple_executor",
        "complex_executor",
        "fallback_executor",
    ],
)
def test_no_answer_reports_executor_block_instead_of_invented_synthesizer_phase(role):
    run = saved_run()
    turn = run["turns"][0]
    turn.update(
        answer="",
        events=[{"type": "error", "code": "synthesis_failed"}],
        tool_calls=[],
        tool_results=[],
        failure_flags=["model_task_failure"],
    )
    run["trace"]["provider_calls"].append(
        {
            "turn": 1,
            "role": role,
            "text": '{"action":"block","reason":"synthesis_failed"}',
        }
    )
    result = adjudication.score_terminal_no_answer(expectation(), run, 0)
    assert result["failure_phase"] == role
    assert result["failures"][0]["phase"] == role
    assert result["terminal"]["code"] == "synthesis_failed"


def _history_only_followup():
    run = search_run()
    first = run["turns"][0]
    first["durable_messages"] = deepcopy(first["durable_messages"])
    first["durable_messages"].insert(
        0,
        {
            "role": "tool",
            "tool_name": "search_managers",
            "tool_args": deepcopy(first["events"][0]["args"]),
            "tool_result": deepcopy(first["events"][1]["result"]),
        },
    )
    second = deepcopy(first)
    second.update(
        turn=2,
        events=[
            event
            for event in second["events"]
            if event["type"] not in {"tool_call", "tool_result"}
        ],
        tool_calls=[],
        tool_results=[],
        durable_messages=[],
        history=[
            {
                "role": "assistant",
                "content": f"Historical tool data ({row['tool_name']}): "
                + json.dumps(row["tool_result"]),
            }
            for row in first["durable_messages"]
        ],
    )
    run["turns"].append(second)
    return run


def _score_history_only_followup(run):
    contract = expectation()
    contract["turn_index"] = 1
    packet = adjudication.build_adjudication_request(contract, run, 1)
    raw = json.loads(response(packet))
    if not packet["packet"]["evidence"]:
        for field in packet["packet"]["grounding_contract"]["data_fields"]:
            raw["fact_support"][field]["status"] = "unsupported"
    parsed = adjudication.parse_adjudication_response(
        packet, json.dumps(raw), judge_id="synthetic-history-review"
    )
    assert parsed["status"] == "completed"
    score = adjudication.score_adjudicated(
        contract, parsed, registry={"Shipment": "Database", "Project": "Database"}
    )
    return packet, score


def test_history_only_followup_credits_verified_selection_through_discovery_score():
    run = _history_only_followup()
    packet, score = _score_history_only_followup(run)
    trace = packet["observation"]["trace"]
    assert run["turns"][1]["tool_calls"] == []
    assert trace["evidence"] and all(
        row["origin"] == "history" for row in trace["evidence"]
    )
    assert trace["discovery"][0]["candidates"] == ["Shipment"]
    assert trace["discovery"][0]["selected"] == ["Shipment", "Project"]
    assert score["dimensions"]["D"]["status"] == "pass"
    assert score["primary_failure"] == "passed"


@pytest.mark.parametrize(
    "mutation",
    ["missing", "changed", "not_persisted", "changed_persistence", "ambiguous"],
)
def test_history_candidates_cannot_replace_unverified_query_selection(mutation):
    run = _history_only_followup()
    first, second = run["turns"]
    if mutation == "missing":
        second["history"].pop()
    elif mutation == "changed":
        second["history"][-1]["content"] = second["history"][-1]["content"].replace(
            '"120"', '"999"'
        )
    elif mutation == "not_persisted":
        first["durable_messages"].pop()
    elif mutation == "changed_persistence":
        first["durable_messages"][-1]["tool_args"]["filters"] = {"code": "wrong"}
    else:
        call = deepcopy(first["events"][2])
        result = deepcopy(first["events"][3])
        call["id"] = result["id"] = "ambiguous-query"
        call["args"]["filters"] = {"code": "S01"}
        first["events"][4:4] = [call, result]
        first["tool_calls"].append(call)
        first["tool_results"].append(result)
        first["durable_messages"].append(
            {
                "role": "tool",
                "tool_name": "query",
                "tool_args": deepcopy(call["args"]),
                "tool_result": deepcopy(result["result"]),
            }
        )
    packet, score = _score_history_only_followup(run)
    trace = packet["observation"]["trace"]
    assert trace["evidence"] == []
    assert trace["discovery"][0]["candidates"] == ["Shipment"]
    assert trace["discovery"][0]["selected"] == []
    assert score["dimensions"]["D"]["status"] == "fail"
    checks = score["dimensions"]["D"]["checks"]
    assert any(
        row["field"].endswith(".candidates") and row["status"] == "pass"
        for row in checks
    )
    assert any(
        row["field"].endswith(".selected") and row["status"] == "fail" for row in checks
    )
