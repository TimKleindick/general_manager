"""Prospective offline Judge contracts; no historical scores or live efficacy."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from typing import Any

import pytest

from experiments.gm_eval import adjudication
from general_manager.chat.providers.base import DoneEvent, TextChunkEvent, TokenUsage
from tests.experiments.test_gm_eval_adjudication import (
    OfflineJudge,
    expectation,
    request,
    response,
    saved_run,
)
from tests.experiments.test_gm_eval_grounding_linkage import (
    grounding,
    material_packet,
    material_response,
    parsed_and_scored,
)
from tests.experiments.test_gm_eval_scoring import REGISTRY


def _packet(
    contract: dict[str, Any],
    answer: str,
    queries: list[tuple[str, list[dict[str, Any]], dict[str, Any]]],
) -> dict[str, Any]:
    """Build a fresh saved-trace control through the real request builder."""
    run = saved_run()
    run["case_id"] = contract["case_id"]
    events: list[dict[str, Any]] = []
    for index, (manager, rows, filters) in enumerate(queries, 1):
        args = {
            "manager": manager,
            "fields": sorted({field for row in rows for field in row}),
            "filters": filters,
        }
        events.extend(
            [
                {
                    "type": "tool_call",
                    "task_id": "read",
                    "id": f"query-{index}",
                    "name": "query",
                    "args": args,
                },
                {
                    "type": "tool_result",
                    "task_id": "read",
                    "id": f"query-{index}",
                    "name": "query",
                    "result": {
                        "data": rows,
                        "total_count": len(rows),
                        "has_more": False,
                        "complete": True,
                    },
                },
            ]
        )
    events.extend([{"type": "text_chunk", "content": answer}, {"type": "done"}])
    turn = run["turns"][0]
    turn.update(
        turn=contract["turn_index"] + 1,
        answer=answer,
        events=events,
        tool_calls=[event for event in events if event["type"] == "tool_call"],
        tool_results=[event for event in events if event["type"] == "tool_result"],
        durable_messages=[],
    )
    run["turns"] = [
        {
            "turn": index + 1,
            "user": "Synthetic earlier question.",
            "answer": "",
            "events": [{"type": "done"}],
        }
        for index in range(contract["turn_index"])
    ] + [turn]
    managers = sorted({manager for manager, _, _ in queries})
    run["schema_index"] = {manager: {"relations": []} for manager in managers}
    run["trace"]["provider_calls"][0].update(turn=turn["turn"])
    run["trace"]["provider_calls"][0]["messages"][0]["content"] = (
        "REFERENCE_DATA="
        + json.dumps(
            {
                "task": {"task_id": "read"},
                "manager_candidates": [{"manager": manager} for manager in managers],
                "task_evidence": [],
            }
        )
    )
    return adjudication.build_adjudication_request(
        contract, run, contract["turn_index"]
    )


def _claims(
    packet: dict[str, Any], facts: dict[str, Any], *, supported: bool = True
) -> dict[str, Any]:
    """An explicitly injected extraction, not an offline replacement Judge."""
    evidence = [item["id"] for item in packet["packet"]["evidence"]]
    checks = {
        check["id"]: {
            "passed": supported if check["id"] == "answer_supported" else True,
            "reason": "Explicit synthetic independent review control.",
            "evidence_ids": ["answer", *evidence],
        }
        for check in packet["packet"]["semantic_checks"]
    }
    if "no_repeated_clarification" in checks:
        checks["no_repeated_clarification"].update(
            context_sha256=packet["packet"]["context_sha256"], repetitions=[]
        )
    return {
        "schema_version": packet["schema_version"],
        **(
            {"additional_quantities": {"reviewed_entire_answer": True, "claims": []}}
            if packet["schema_version"] == "1.6"
            else {}
        ),
        "status": "completed",
        "request_sha256": packet["request_sha256"],
        "answer_sha256": packet["packet"]["answer_sha256"],
        "facts": facts,
        "fact_support": {
            field: {
                "status": "absent" if value is None else "present",
                "answer_quotes": [] if value is None else [packet["packet"]["answer"]],
                "evidence_ids": [] if value is None else evidence,
            }
            for field, value in facts.items()
        },
        "citations": [],
        "semantic_checks": checks,
    }


def _parse_score(
    contract: dict[str, Any], packet: dict[str, Any], value: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed = adjudication.parse_adjudication_response(
        packet, json.dumps(value), judge_id="synthetic-r26-control"
    )
    return parsed, adjudication.score_adjudicated(contract, parsed, registry=REGISTRY)


def _cost_packet(contract: dict[str, Any], answer: str) -> dict[str, Any]:
    return _packet(
        contract,
        answer,
        [
            (
                "ProjectCost",
                [
                    {
                        "id": index,
                        "kind": "actual",
                        "year": 2025,
                        "projectId": project,
                        "wbsElementId": wbs,
                        "netCost": cost,
                    }
                    for index, (project, wbs, cost) in enumerate(
                        [
                            (1, 1, "1200"),
                            (1, 1, "800"),
                            (1, 2, "500"),
                            (2, 1, "1500"),
                            (2, 2, "700"),
                        ],
                        1,
                    )
                ],
                {"kind": "actual", "year": 2025},
            ),
            (
                "Project",
                [
                    {"id": 1, "code": "P01", "name": "Aurora"},
                    {"id": 2, "code": "P02", "name": "Borealis"},
                ],
                {},
            ),
            (
                "WbsElement",
                [
                    {"id": 1, "code": "W01", "name": "Engineering"},
                    {"id": 2, "code": "W02", "name": "ProjectManagement"},
                ],
                {},
            ),
        ],
    )


@pytest.mark.parametrize(
    "identifier,answer",
    [
        (
            "E117",
            "Coûts réels nets de projet en EUR pour 2025 (1 janvier au 31 décembre): "
            "W01 Engineering 3500; W02 ProjectManagement 1200; total 4700. Plans exclus.",
        ),
        (
            "E118",
            "Actual net project costs in EUR for calendar 2025: W01 Engineering 3500; "
            "W02 ProjectManagement 1200; total 4700. Plan entries excluded.",
        ),
        (
            "E120",
            "Aurora P01: coûts réels nets de projet de 2500 EUR pour 2025, "
            "du 1 janvier au 31 décembre; écritures prévisionnelles exclues.",
        ),
    ],
)
def test_actual_cost_meaning_is_delivered_to_injected_judge_without_gold_values(
    identifier: str, answer: str
) -> None:
    contract = expectation(identifier)
    packet = _cost_packet(contract, answer)
    claims = _claims(packet, deepcopy(contract["facts"]))
    provider = OfflineJudge(
        [TextChunkEvent(json.dumps(claims)), DoneEvent(TokenUsage(1, 2))]
    )
    parsed = asyncio.run(
        adjudication.adjudicate_turn(packet, judge_id="synthetic-r26", judge=provider)
    )
    assert parsed["status"] == "completed"
    instruction = provider.seen[0].content
    assert "actual_net_project_cost" in instruction
    assert "incurred" in instruction and "planned or forecast" in instruction
    assert "3500" not in instruction and "2500" not in instruction
    scored = adjudication.score_adjudicated(contract, parsed, registry=REGISTRY)
    assert scored["dimensions"]["C"]["status"] == "pass"
    assert parsed["model_performance_measured"] is False
    changed = deepcopy(contract)
    changed["facts"]["metric"] = "PRIVATE_EXPECTED_METRIC"
    assert adjudication.fact_schemas(changed) == adjudication.fact_schemas(contract)


@pytest.mark.parametrize(
    "field,wrong,dimension",
    [
        ("metric", "planned_net_project_cost", "C"),
        ("unit", "USD", "C"),
        ("years", [2027], "C"),
        ("period", {"start": "2024-01-01", "end_exclusive": "2025-01-01"}, "C"),
        ("values", {"P01": "2501"}, "C"),
        ("total", "2501", "C"),
        ("source_kinds", ["existing_plan"], "I"),
        ("constraints", {"kind": "plan", "project": "P01"}, "R"),
    ],
)
def test_cost_contract_never_repairs_wrong_assertions_from_correct_tools(
    field: str, wrong: Any, dimension: str
) -> None:
    contract = expectation("E120")
    facts = deepcopy(contract["facts"])
    facts[field] = wrong
    packet = _cost_packet(contract, "Aurora P01 cost assertions: " + json.dumps(facts))
    parsed, scored = _parse_score(contract, packet, _claims(packet, facts))
    assert parsed["status"] == "completed"
    assert parsed["observation"]["facts"][field] == wrong
    assert scored["dimensions"][dimension]["status"] == "fail"
    assert scored["passed"] is False


def _context_contract() -> dict[str, Any]:
    contract = expectation()
    contract["facts"]["context"] = {
        "metric": "gross_shipped_quantity",
        "unit": "pieces",
        "years": [2025],
    }
    contract["dimensions"]["Q"] = {
        "applicable": True,
        "checks": [
            {
                "field": "context",
                "expected": contract["facts"]["context"],
                "comparison": "exact",
            }
        ],
    }
    return contract


def test_asserted_context_has_explicit_component_meaning_in_real_request() -> None:
    contract = _context_contract()
    packet = adjudication.build_adjudication_request(contract, saved_run(), 0)
    description = packet["packet"]["fact_fields"]["context"]["description"]
    assert "metric, unit and years" in description
    assert "same asserted component" in description
    assert "null" in description and "contradict" in description
    parsed, scored = _parse_score(
        contract, packet, _claims(packet, deepcopy(contract["facts"]))
    )
    assert parsed["status"] == "completed"
    assert scored["dimensions"]["Q"]["status"] == "pass"


@pytest.mark.parametrize(
    "context",
    [
        None,
        {"metric": "gross_shipped_quantity", "unit": None, "years": [2025]},
        {"metric": "gross_shipped_quantity", "unit": "kg", "years": [2025]},
        {"metric": "planned_net_revenue", "unit": "EUR", "years": [2027]},
    ],
)
def test_absent_or_conflicting_context_is_not_filled_from_other_fields(
    context: Any,
) -> None:
    contract = _context_contract()
    packet = adjudication.build_adjudication_request(contract, saved_run(), 0)
    facts = deepcopy(contract["facts"])
    facts["context"] = context
    parsed, scored = _parse_score(contract, packet, _claims(packet, facts))
    assert parsed["status"] == "completed"
    assert parsed["observation"]["facts"]["context"] == context
    assert scored["dimensions"]["Q"]["status"] == "fail"


def test_typed_identity_and_multi_hop_witness_rules_reach_actual_judge_messages() -> (
    None
):
    packet = material_packet()
    instruction = adjudication.judge_messages(packet)[0].content
    assert "JSON type" in instruction
    assert "never cast" in instruction
    assert "multi-hop" in instruction
    assert "fact_support" in instruction and "answer_supported" in instruction
    shared = [
        "turn-2/call-3/Project",
        "turn-2/call-5/ProjectPart",
        "turn-2/call-6/Part",
    ]
    value = material_response(packet, shared, shared)
    parsed, scored = parsed_and_scored(packet, value)
    assert parsed["status"] == "completed"
    assert grounding(scored)["status"] == "pass"


@pytest.mark.parametrize(
    "fact_ids,semantic_ids",
    [
        (
            ["turn-2/call-3/Project"],
            ["turn-2/call-5/ProjectPart", "turn-2/call-6/Part"],
        ),
        (["turn-2/call-2/Material"], ["turn-2/call-3/Material"]),
    ],
)
def test_distinct_or_missing_bridge_ids_remain_grounding_failures(
    fact_ids: list[str], semantic_ids: list[str]
) -> None:
    packet = material_packet()
    parsed, scored = parsed_and_scored(
        packet, material_response(packet, fact_ids, semantic_ids)
    )
    assert parsed["status"] == "completed"
    assert grounding(scored)["status"] == "fail"
    assert scored["passed"] is False


def test_wrong_manager_qualified_reference_is_not_repaired() -> None:
    packet = material_packet()
    value = material_response(packet, ["turn-2/call-3/Part"], ["turn-2/call-3/Part"])
    parsed, scored = parsed_and_scored(packet, value)
    assert parsed["status"] == "judge_failure"
    assert scored["passed"] is False


@pytest.mark.parametrize("identifier", [1, "1"])
def test_wrong_entity_id_remains_a_result_failure_with_shared_evidence(
    identifier: Any,
) -> None:
    packet = material_packet()
    shared = ["turn-2/call-2/Material", "turn-2/call-3/Project"]
    value = material_response(packet, shared, shared)
    value["facts"]["result_ids"] = [identifier]
    parsed, scored = parsed_and_scored(packet, value)
    assert parsed["status"] == "completed"
    assert parsed["observation"]["facts"]["result_ids"] == [identifier]
    assert type(parsed["observation"]["facts"]["result_ids"][0]) is type(identifier)
    assert scored["dimensions"]["R"]["status"] == "fail"


def test_wrong_ranking_order_is_preserved_and_fails_the_unchanged_scorer() -> None:
    contract = expectation("E066", 1)
    facts = deepcopy(contract["facts"])
    facts["ranked_ids"] = list(reversed(facts["ranked_ids"]))
    packet = _packet(
        contract,
        "Planned net revenue in EUR for 2027, presented in this order: "
        + json.dumps(facts),
        [("ProjectCommercial", [{"code": "P02", "plannedNetRevenue": "30000"}], {})],
    )
    parsed, scored = _parse_score(contract, packet, _claims(packet, facts))
    assert parsed["status"] == "completed"
    assert parsed["observation"]["facts"]["ranked_ids"] == facts["ranked_ids"]
    assert scored["dimensions"]["C"]["status"] == "fail"
    assert scored["passed"] is False


def test_multi_value_constraints_are_representable_and_still_incorrect() -> None:
    contract = expectation("E101", 1)
    families = ["Steel", "StainlessSteel", "LegacySteel"]
    facts = {
        "response_mode": "answer",
        "result_ids": [],
        "constraints": {"family": families, "is_active": True},
        "context": {"constraints": {"family": families, "is_active": True}},
    }
    packet = _packet(
        contract,
        "No non-deleted materials in families Steel, StainlessSteel and LegacySteel; "
        "the complete selection has zero rows and includes business-inactive records.",
        [("Material", [], {"family_In": families, "is_active": True})],
    )
    value = _claims(packet, facts, supported=False)
    provider = OfflineJudge(
        [TextChunkEvent(json.dumps(value)), DoneEvent(TokenUsage(1, 2))]
    )
    parsed = asyncio.run(
        adjudication.adjudicate_turn(packet, judge_id="synthetic-r26", judge=provider)
    )
    assert parsed["status"] == "completed"
    assert parsed["observation"]["facts"] == facts
    assert parsed["observation"]["fact_support"] == value["fact_support"]
    scored = adjudication.score_adjudicated(contract, parsed, registry=REGISTRY)
    assert scored["dimensions"]["R"]["status"] == "fail"
    assert scored["dimensions"]["Q"]["status"] == "fail"
    assert scored["dimensions"]["A"]["status"] == "fail"
    assert scored["passed"] is False
    assert "steel" not in json.dumps(packet["packet"]["fact_fields"])


@pytest.mark.parametrize(
    "bad",
    [
        [True],
        [["Steel"]],
        [{"family": "Steel"}],
        [None],
        [float("inf")],
        [float("nan")],
    ],
)
def test_constraint_list_elements_must_match_the_scalar_claim_type(bad: Any) -> None:
    shapes = adjudication.fact_schemas(expectation("E101", 1))
    assert not adjudication._matches_shape({"family": bad}, shapes["constraints"])


def test_constraint_list_shape_does_not_widen_numeric_result_fields() -> None:
    shapes = adjudication.fact_schemas(expectation("E120"))
    assert not adjudication._matches_shape(["2500"], shapes["total"])
    assert not adjudication._matches_shape({"P01": ["2500"]}, shapes["values"])
    assert adjudication._matches_shape({"kind": "actual"}, shapes["constraints"])


def test_missing_numeric_claim_stays_absent_despite_correct_tool_value() -> None:
    packet = request()
    value = json.loads(response(packet))
    value["facts"]["values"] = None
    value["fact_support"]["values"] = {
        "status": "absent",
        "answer_quotes": [],
        "evidence_ids": [],
    }
    parsed, scored = _parse_score(expectation(), packet, value)
    assert parsed["status"] == "completed"
    assert parsed["observation"]["facts"]["values"] is None
    assert scored["dimensions"]["C"]["status"] == "fail"
