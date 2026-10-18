"""Offline audit regressions; injected judgments are never live model scores."""

from copy import deepcopy
import json

import pytest

from experiments.gm_eval import (
    adjudication,
    catalog,
    oracle,
    semantic_contracts,
    harness,
)
from types import SimpleNamespace
from tests.experiments.test_gm_eval_adjudication import (
    expectation,
    response,
    saved_run,
)
from tests.experiments.test_gm_eval_no_repeated_clarification import (
    request as repetition_request,
    response as repetition_response,
)
from tests.experiments.test_gm_eval_scoring import REGISTRY, case, control, score


def test_default_reference_versions_the_authorized_audit_changes():
    assert catalog.REFERENCE_VERSION == "1.17"
    assert set(catalog.REFERENCE_VERSIONS) == {
        "1.10",
        "1.11",
        "1.12",
        "1.13",
        "1.14",
        "1.15",
        "1.16",
        "1.17",
    }


@pytest.mark.parametrize(
    "code", ["provider_failed", "deadline_exceeded", "synthesis_failed"]
)
def test_gm_uses_matching_runtime_origin_for_model_declared_codes(code):
    from experiments.gm_eval import harness
    from experiments.gm_eval.trace import TraceRecorder

    trace = TraceRecorder(turn=1)
    trace.audit(
        {
            "event_type": "planned_terminal",
            "terminal_reason": code,
            "reason_origin": "model_declared_block",
        }
    )
    turn = {
        "turn": 1,
        "terminal": {"type": "error", "code": code},
        "persistence_verified": True,
    }
    assert harness._failure_flags([turn], trace, []) == ["model_task_failure"]


def test_known_validation_cycle_is_model_owned_and_clarification_is_not_failure():
    from general_manager.chat.evals.outcomes import terminal_failure_categories

    assert terminal_failure_categories(
        {"type": "error", "code": "provider_failed"},
        terminal_origin="scheduler_validation_cycle",
    ) == {"provider_failed": "model_task_failure"}
    assert (
        terminal_failure_categories(
            {
                "type": "done",
                "orchestration": {
                    "unresolved": [
                        {"task_id": "question", "reason": "clarification_required"}
                    ]
                },
            }
        )
        == {}
    )


def test_runtime_failure_origin_is_bound_for_new_replay_without_changing_saved_flags():
    run = saved_run()
    turn = run["turns"][0]
    turn["events"][-1] = {"type": "error", "code": "provider_failed"}
    turn["terminal"] = turn["events"][-1]
    run["trace"] = {
        "events": [
            {
                "turn": 1,
                "category": "audit",
                "event_type": "planned_terminal",
                "terminal_reason": "provider_failed",
                "reason_origin": "model_declared_block",
            }
        ]
    }
    request = adjudication.build_adjudication_request(expectation(), run, 0)
    assert request["candidate_failure_flags"][0]["category"] == "model_task_failure"
    assert (
        request["candidate_failure_provenance"]["terminal_origin"]
        == "model_declared_block"
    )
    adjudication._validate_request(request)
    changed = deepcopy(request)
    changed["candidate_failure_provenance"]["terminal_origin"] = "provider_exception"
    with pytest.raises(adjudication.AdjudicationError):
        adjudication._validate_request(changed)
    changed["request_sha256"] = adjudication._request_digest(changed)
    with pytest.raises(adjudication.AdjudicationError):
        adjudication._validate_request(changed)
    turn["failure_flags"] = ["transport_failure"]
    request = adjudication.build_adjudication_request(expectation(), run, 0)
    assert request["candidate_failure_flags"][0]["category"] == "transport_failure"
    turn.pop("failure_flags")
    old = adjudication.build_adjudication_request(
        oracle.expected_turn(case("E004"), 0, reference_version="1.15"), run, 0
    )
    assert "candidate_failure_provenance" not in old
    assert old["candidate_failure_flags"][0]["category"] == "transport_failure"


@pytest.mark.parametrize(
    "code", ["provider_failed", "deadline_exceeded", "synthesis_failed"]
)
def test_actual_partial_model_block_matches_sanitized_audit_and_replay(
    monkeypatch, code
):
    import asyncio
    from experiments.gm_eval.trace import TraceRecorder
    from general_manager.chat import audit
    from general_manager.chat.evals import runner
    from general_manager.chat.evals.fixtures import planned_role_overrides
    from general_manager.chat.planned import scheduler

    trace = TraceRecorder(turn=1)
    original_audit = scheduler.emit_planned_audit_event
    monkeypatch.setattr(
        audit,
        "get_chat_settings",
        lambda: {"audit": {"enabled": True, "level": "verbose", "redact_fields": []}},
    )
    monkeypatch.setattr(
        scheduler,
        "emit_planned_audit_event",
        lambda event_type, payload: original_audit(
            event_type, payload, sink=trace.audit
        ),
    )
    selected = next(
        row
        for row in runner.load_dataset("planned_orchestration")
        if row.name == "partial_independent_answer"
    )
    scripts = selected.expectations["planned"]["scripts"]
    scripts["executor"] = [
        step for step in scripts["executor"] if step.get("task_id") != "task_2"
    ]
    scripts["executor"].append(
        {"task_id": "task_2", "text": {"action": "block", "reason": code}}
    )
    events = []
    original_events = runner.iter_planned_read_events

    async def tapped(prepared, *args, **kwargs):
        async for event in original_events(prepared, *args, **kwargs):
            events.append(event)
            yield event

    monkeypatch.setattr(runner, "iter_planned_read_events", tapped)
    result = asyncio.run(
        runner.run_case(
            None,
            selected,
            [],
            strategy="planned",
            role_overrides=planned_role_overrides(selected),
        )
    )
    terminal = events[-1]
    assert terminal["type"] == "done"
    assert terminal["orchestration"]["coverage"] == {"resolved": 1, "total": 2}
    assert result.status == "fail"
    assert result.failure_category == "model_task_failure"
    assert next(e for e in trace.events if e.get("terminal_reason") == code)[
        "task_id"
    ] == audit.planned_audit_lineage_id("task_2")
    turn = {"turn": 1, "terminal": terminal, "persistence_verified": True}
    assert harness._failure_flags([turn], trace, []) == ["model_task_failure"]
    run = saved_run()
    run["turns"][0]["events"][-1] = terminal
    run["turns"][0]["terminal"] = terminal
    run["trace"] = {"events": trace.events}
    request = adjudication.build_adjudication_request(expectation(), run, 0)
    assert request["candidate_failure_provenance"]["reason_origins"] == {
        "task_2": "model_declared_block"
    }
    assert request["candidate_failure_flags"][0]["category"] == "model_task_failure"
    adjudication._validate_request(request)


@pytest.mark.parametrize("task_id", ["", "x" * 129])
def test_unhashable_unresolved_task_cannot_match_an_audit_without_identity(task_id):
    from general_manager.chat.evals.outcomes import runtime_failure_provenance

    terminal = {
        "type": "done",
        "orchestration": {
            "unresolved": [{"task_id": task_id, "reason": "provider_failed"}]
        },
    }
    event = {
        "turn": 1,
        "category": "audit",
        "event_type": "planned_task_progress",
        "terminal_reason": "provider_failed",
        "reason_origin": "model_declared_block",
    }
    assert runtime_failure_provenance(terminal, [event], turn=1)["reason_origins"] == {}


@pytest.mark.parametrize("wrapper", ["experiment", "observed", "recording"])
def test_forwarding_provider_wrappers_close_inner_stream_before_return(
    tmp_path, monkeypatch, wrapper
):
    import asyncio
    from experiments.gm_eval.profiles import (
        ExperimentProvider,
        bind_provider_factory,
        siwc_factory,
    )
    from experiments.gm_eval.trace import TraceRecorder
    from experiments.siwc_eval.provider import Provider
    from experiments.siwc_eval.answer_scoring import _RecordingProvider
    from general_manager.chat.planned.provider_calls import (
        complete_provider_round,
        InvalidProviderRoundError,
    )
    from general_manager.chat.providers.base import TextChunkEvent, ToolCallEvent

    state = {"closed": False}

    async def complete(self, messages, tools):
        try:
            yield TextChunkEvent("ambiguous")
            yield ToolCallEvent("tool", "query", {})
        finally:
            state["closed"] = True

    monkeypatch.setattr(Provider, "complete", complete)
    trace = TraceRecorder()
    inner = Provider("offline", object(), max_requests=1)

    def factory(_config):
        return inner

    if wrapper == "experiment":
        wrapped = ExperimentProvider({"model": "offline", "role": "executor"})
    elif wrapper == "observed":
        wrapped = siwc_factory(object())({"model": "offline"})
    else:
        wrapped = _RecordingProvider(
            "offline",
            object(),
            max_requests=1,
            artifact={},
            path=tmp_path / "record.json",
        )

    async def check():
        with bind_provider_factory(factory, trace):
            with pytest.raises(InvalidProviderRoundError):
                await complete_provider_round(wrapped, [], [], 1)
            assert state["closed"]

    asyncio.run(check())


@pytest.mark.parametrize("identifier", ["E074", "E084"])
def test_unrequested_order_does_not_change_complete_membership(identifier):
    contract = oracle.expected_turn(case(identifier), 0)
    observed, judgment = control(contract)
    assert score(contract, observed, judgment)["passed"]
    if identifier == "E084":
        observed["facts"]["constraints"]["all_materials"].reverse()
    else:
        observed["facts"]["result_ids"].reverse()
        observed["facts"].pop("ranked_ids", None)
        observed["fact_support"].pop("ranked_ids", None)
    assert score(contract, observed, judgment)["passed"]


@pytest.mark.parametrize("damage", ["missing", "extra", "duplicate", "extra_filter"])
@pytest.mark.parametrize("identifier", ["E074", "E084"])
def test_unordered_semantics_still_require_exact_complete_facts(identifier, damage):
    contract = oracle.expected_turn(case(identifier), 0)
    observed, judgment = control(contract)
    values = (
        observed["facts"]["constraints"]["all_materials"]
        if identifier == "E084"
        else observed["facts"]["result_ids"]
    )
    if damage == "missing":
        values.pop()
    elif damage == "extra":
        values.append("M99" if identifier == "E084" else "P99")
    elif damage == "duplicate":
        values.append(values[0])
    else:
        observed["facts"]["constraints"]["invented_filter"] = "active"
    assert not score(contract, observed, judgment)["passed"]


@pytest.mark.parametrize("version", ["1.10", "1.11", "1.12", "1.13", "1.14", "1.15"])
def test_archived_unordered_contracts_keep_their_original_comparison(version):
    for identifier in ("E074", "E084"):
        contract = oracle.expected_turn(case(identifier), 0, reference_version=version)
        observed, judgment = control(contract)
        if identifier == "E084":
            observed["facts"]["constraints"]["all_materials"].reverse()
        else:
            observed["facts"]["ranked_ids"].reverse()
        assert not score(contract, observed, judgment)["passed"]


def test_explicitly_requested_order_remains_strict():
    contract = oracle.expected_turn(case("E083"), 0)
    observed, judgment = control(contract)
    assert score(contract, observed, judgment)["passed"]
    observed["facts"]["ranked_ids"].reverse()
    assert not score(contract, observed, judgment)["passed"]


def test_e074_does_not_require_an_unrequested_ranking_claim():
    contract = oracle.expected_turn(case("E074"), 0)
    assert "ranked_ids" not in contract["facts"]
    assert "ranked_ids" not in adjudication.fact_schemas(contract)
    assert contract["dimensions"]["C"] == {"applicable": False, "checks": []}
    observed, judgment = control(contract)
    observed["facts"]["result_ids"].reverse()
    assert score(contract, observed, judgment)["passed"]


def _with_tool_history():
    request = repetition_request("Which year should I use?")
    history = request["packet"]["visible_history"]
    history.append({"role": "tool", "content": '{"selected_year":2027}'})
    context = {
        "current_question": request["packet"]["current_question"],
        "visible_history": history,
    }
    request["packet"]["context_sha256"] = semantic_contracts.context_digest(context)
    request["observation"]["trace"]["conversation_context"] = context
    request["request_sha256"] = adjudication._request_digest(request)
    return request


def test_native_tool_history_is_context_not_a_reason_to_discard_judgment():
    request = _with_tool_history()
    result = adjudication.parse_adjudication_response(
        request, json.dumps(repetition_response(request)), judge_id="offline-control"
    )
    assert result["status"] == "completed"


def test_tool_history_cannot_impersonate_a_user_choice():
    request = _with_tool_history()
    value = repetition_response(
        request,
        passed=False,
        repetitions=[
            {
                "answer_quote": "Which year should I use?",
                "resolved_by": {
                    "source": "visible_history",
                    "index": len(request["packet"]["visible_history"]) - 1,
                    "quote": "2027",
                },
            }
        ],
    )
    result = adjudication.parse_adjudication_response(
        request, json.dumps(value), judge_id="offline-control"
    )
    assert result["status"] == "judge_failure"


@pytest.mark.parametrize(
    "key,value",
    [
        ("case_id", "E014"),
        ("case_id", None),
        ("turn_index", 1),
        ("turn_index", False),
        ("turn_index", "0"),
        ("turn_index", -1),
    ],
)
def test_replay_rejects_outer_scope_relabeling(key, value):
    request = adjudication.build_adjudication_request(expectation(), saved_run(), 0)
    raw = response(request)
    assert (
        adjudication.parse_adjudication_response(
            request, raw, judge_id="offline-control"
        )["status"]
        == "completed"
    )
    request[key] = value
    result = adjudication.parse_adjudication_response(
        request, raw, judge_id="offline-control"
    )
    assert result["status"] == "judge_failure"
    assert result["failure_flags"][-1]["reason"] == "inconsistent_adjudication_scope"
    assert not adjudication.score_adjudicated(
        expectation("E014"), result, registry=REGISTRY
    )["passed"]


def _history_control():
    contract = expectation("E008", 1)
    schema = {
        "manager": "ShipmentPlan",
        "contract_version": 2,
        "fields": ["quantity", "customer"],
        "type": "ShipmentPlanType",
        "relations": [{"name": "customer", "path": ["customer"], "target": "Customer"}],
        "types": {
            "ShipmentPlanType": {
                "kind": "object",
                "fields": {"customer": {"type": "CustomerType"}},
            },
            "CustomerType": {"kind": "reference", "manager": "Customer"},
        },
    }
    args = {
        "manager": "ShipmentPlan",
        "fields": ["id", "year", "quantity", {"customer": ["id", "code", "name"]}],
    }
    output = {
        "data": [
            {
                "id": y,
                "year": y,
                "quantity": str(150 + (y - 2027) * 10),
                "customer": {"id": 1, "code": "C01", "name": "XYZ Industrie"},
            }
            for y in range(2027, 2032)
        ],
        "total_count": 5,
        "has_more": False,
        "complete": True,
    }
    events = []
    durable = []
    history = []
    for name, arguments, result in [
        ("get_manager_schema", {"manager": "ShipmentPlan"}, schema),
        ("query", args, output),
    ]:
        events.extend(
            [
                {
                    "type": "tool_call",
                    "task_id": "read",
                    "id": name,
                    "name": name,
                    "args": arguments,
                },
                {
                    "type": "tool_result",
                    "task_id": "read",
                    "id": name,
                    "name": name,
                    "result": result,
                },
            ]
        )
        durable.append(
            {
                "role": "tool",
                "tool_name": name,
                "tool_args": arguments,
                "tool_result": result,
            }
        )
        history.append(
            {
                "role": "assistant",
                "content": f"Historical tool data ({name}): " + json.dumps(result),
            }
        )
    facts = deepcopy(contract["facts"])
    facts["customer_ids"] = [1]
    facts["context"]["customer_ids"] = [1]
    answer = json.dumps(facts)
    run = {
        "case_id": "E008",
        "schema_index": {"ShipmentPlan": schema, "Customer": {"relations": []}},
        "turns": [
            {
                "turn": 1,
                "user": "Development?",
                "answer": "Which period?",
                "events": [
                    *events,
                    {"type": "text_chunk", "content": "Which period?"},
                    {"type": "done"},
                ],
                "durable_messages": durable,
                "persistence_verified": True,
            },
            {
                "turn": 2,
                "user": "2027 to 2031 plan.",
                "answer": answer,
                "events": [{"type": "text_chunk", "content": answer}, {"type": "done"}],
                "history": history,
                "history_source": "production_prepare_conversation_messages",
                "persistence_verified": True,
                "durable_messages": [],
            },
        ],
    }
    return contract, run, facts


def _score_history(contract, run, facts):
    request = adjudication.build_adjudication_request(contract, run, 1)
    packet = request["packet"]
    refs = [row["id"] for row in packet["evidence"]]
    checks = {
        c["id"]: {
            "passed": True,
            "reason": "Injected offline semantic control.",
            "evidence_ids": ["answer", *refs],
        }
        for c in packet["semantic_checks"]
    }
    checks["no_repeated_clarification"].update(
        context_sha256=packet["context_sha256"], repetitions=[]
    )
    value = {
        "schema_version": request["schema_version"],
        "status": "completed",
        "request_sha256": request["request_sha256"],
        "answer_sha256": packet["answer_sha256"],
        "facts": facts,
        "fact_support": {
            name: {
                "status": "present",
                "answer_quotes": [run["turns"][1]["answer"]],
                "evidence_ids": refs,
            }
            for name in facts
        },
        "citations": [],
        "semantic_checks": checks,
    }
    parsed = adjudication.parse_adjudication_response(
        request, json.dumps(value), judge_id="offline-control"
    )
    return adjudication.score_adjudicated(contract, parsed, registry=REGISTRY)


def test_historical_typed_schema_normalizes_nested_ids_like_current_schema():
    contract, run, facts = _history_control()
    current = deepcopy(run)
    current["turns"][1]["history"] = []
    current["turns"][1]["events"] = (
        run["turns"][0]["events"][:-2] + current["turns"][1]["events"]
    )
    assert _score_history(contract, current, facts)["passed"]
    assert _score_history(contract, run, facts)["passed"]


@pytest.mark.parametrize(
    "damage",
    ["invisible", "not_durable", "not_verified", "changed_args", "schema_after_query"],
)
def test_historical_schema_requires_visible_durable_prior_proof(damage):
    contract, run, facts = _history_control()
    if damage == "invisible":
        run["turns"][1]["history"].pop(0)
    elif damage == "not_durable":
        run["turns"][0]["durable_messages"].pop(0)
    elif damage == "not_verified":
        run["turns"][0]["persistence_verified"] = False
    elif damage == "changed_args":
        run["turns"][0]["durable_messages"][0] = deepcopy(
            run["turns"][0]["durable_messages"][0]
        )
        run["turns"][0]["durable_messages"][0]["tool_args"]["manager"] = "Other"
    else:
        events = run["turns"][0]["events"]
        events[:4] = events[2:4] + events[:2]
    result = _score_history(contract, run, facts)
    assert not result["passed"]
    assert not result["scored"]


def test_historical_schema_in_packet_must_match_its_separate_origin_proof():
    contract, run, _ = _history_control()
    request = adjudication.build_adjudication_request(contract, run, 1)
    adjudication.judge_messages(request)
    calls = deepcopy(request["packet"]["tool_calls"])
    schema = next(call for call in calls if call["name"] == "get_manager_schema")
    schema["output"]["types"]["CustomerType"]["manager"] = "Project"
    request["packet"]["tool_calls"] = calls
    request["observation"]["trace"]["tool_calls"] = deepcopy(calls)
    request["request_sha256"] = adjudication._request_digest(request)
    with pytest.raises(adjudication.AdjudicationError, match="historical_schema"):
        adjudication.judge_messages(request)


def test_new_reference_inherits_all_other_gold_and_extraction_shapes():
    for row in catalog.load_catalog():
        for index in range(len(row["turns"])):
            old = oracle.expected_turn(row, index, reference_version="1.15")
            new = oracle.expected_turn(row, index, reference_version="1.16")
            if row["id"] == "E074":
                old["facts"].pop("ranked_ids")
                old["entity_identity_reference"]["fields"].pop("ranked_ids")
                old["dimensions"]["C"] = {"applicable": False, "checks": []}
            assert new["facts"] == old["facts"]
            assert adjudication.fact_schemas(new) == adjudication.fact_schemas(old)
            for dimension, value in new["dimensions"].items():
                before = deepcopy(old["dimensions"][dimension])
                after = deepcopy(value)
                for check in after["checks"]:
                    if check["comparison"] == "constraints":
                        check["comparison"] = "exact"
                    if row["id"] == "E074" and check["field"] == "ranked_ids":
                        assert check["comparison"] == "set"
                        check["comparison"] = "exact"
                assert after == before


@pytest.mark.parametrize(
    "code,category",
    [
        ("deadline_exceeded", "budget_exhausted"),
        ("provider_failed", "transport_failure"),
    ],
)
def test_new_terminal_classification_matches_harness_and_replay(code, category):
    run = saved_run()
    turn = run["turns"][0]
    turn["events"][-1] = {"type": "error", "code": code}
    turn["terminal"] = turn["events"][-1]
    request = adjudication.build_adjudication_request(expectation(), run, 0)
    assert {flag["category"] for flag in request["candidate_failure_flags"]} == {
        category
    }
    assert harness._failure_flags([turn], SimpleNamespace(calls=[]), []) == [category]


def test_archived_unclassified_deadline_keeps_its_legacy_fallback():
    run = saved_run()
    turn = run["turns"][0]
    turn["events"][-1] = {"type": "error", "code": "deadline_exceeded"}
    contract = oracle.expected_turn(case("E004"), 0, reference_version="1.15")
    request = adjudication.build_adjudication_request(contract, run, 0)
    assert {flag["category"] for flag in request["candidate_failure_flags"]} == {
        "transport_failure"
    }


@pytest.mark.parametrize("original,replacement", [(False, 0), (True, 1), (2, 2.0)])
def test_historical_schema_proof_requires_json_exact_defaults(original, replacement):
    contract, run, _ = _history_control()
    schema = run["schema_index"]["ShipmentPlan"]
    schema["metadata"] = {"default": original}
    run["turns"][1]["history"][0]["content"] = (
        "Historical tool data (get_manager_schema): " + json.dumps(schema)
    )
    request = adjudication.build_adjudication_request(contract, run, 1)
    adjudication.judge_messages(request)
    calls = deepcopy(request["packet"]["tool_calls"])
    next(call for call in calls if call["name"] == "get_manager_schema")["output"][
        "metadata"
    ]["default"] = replacement
    request["packet"]["tool_calls"] = calls
    request["observation"]["trace"]["tool_calls"] = deepcopy(calls)
    request["request_sha256"] = adjudication._request_digest(request)
    with pytest.raises(adjudication.AdjudicationError, match="historical_schema"):
        adjudication.judge_messages(request)
