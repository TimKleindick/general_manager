"""Synthetic offline linkage controls; no saved answers or model evaluations."""

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
)
from tests.experiments.test_gm_eval_scoring import REGISTRY


def material_packet(defect: str | None = None) -> dict[str, Any]:
    """Build fresh tool rows with distinct direct, nested and bridge witnesses."""
    answer = (
        "Borealis (P02) is the XYZ project using Steel (M02); Aurora (P01) uses Copper."
    )
    material = {"id": 2, "code": "M02", "name": "Steel"}
    copper = {"id": 1, "code": "M01", "name": "Copper"}
    project_rows = [
        {
            "id": 1,
            "code": "P01",
            "name": "Aurora",
            "customerId": 1,
            "materialsList": {
                "items": [copper],
                "pageInfo": {"totalCount": 1, "currentPage": 1, "totalPages": 1},
            },
        },
        {
            "id": 2,
            "code": "P02",
            "name": "Borealis",
            "customerId": 1,
            "materialsList": {
                "items": [material],
                "pageInfo": {"totalCount": 1, "currentPage": 1, "totalPages": 1},
            },
        },
    ]
    if defect == "wrong_material":
        material.update(id=1, code="M01", name="Copper")
    elif defect == "false_membership":
        project_rows.pop()
    elif defect == "wrong_owner":
        project_rows[1]["customerId"] = 2
    elif defect == "partial_materials":
        project_rows[1]["materialsList"]["pageInfo"].update(totalCount=2, totalPages=2)
    elif defect == "missing_material":
        project_rows[1]["materialsList"]["items"] = []
        material = None

    queries = [
        ("Customer", ["id", "code", "name"], [{"id": 1, "code": "C01", "name": "XYZ"}]),
        ("Material", ["id", "code", "name"], [material] if material else []),
        (
            "Project",
            [
                "id",
                "code",
                "name",
                "customerId",
                {
                    "materialsList": [
                        {"items": ["id", "code", "name"]},
                        {"pageInfo": ["totalCount", "currentPage", "totalPages"]},
                    ]
                },
            ],
            project_rows,
        ),
        ("Material", ["id", "code", "name"], [copper]),
        (
            "ProjectPart",
            ["id", "code", "projectId", "partId"],
            [{"id": 2, "code": "PP02", "projectId": 2, "partId": 2}],
        ),
        (
            "Part",
            ["id", "code", "materialId"],
            [{"id": 2, "code": "T02", "materialId": 2}],
        ),
    ]
    events = []
    for index, (manager, fields, rows) in enumerate(queries, 1):
        args = {"manager": manager, "fields": fields}
        output = {
            "data": rows,
            "total_count": len(rows),
            "has_more": False,
            "complete": True,
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
                    "result": output,
                },
            ]
        )
    events.extend([{"type": "text_chunk", "content": answer}, {"type": "done"}])
    managers = ["Customer", "Material", "Project", "ProjectPart", "Part"]
    run = {
        "case_id": "E034",
        "model_performance_measured": False,
        "turns": [
            {
                "turn": 1,
                "user": "List XYZ's projects.",
                "answer": "",
                "events": [{"type": "done"}],
                "history": [],
            },
            {
                "turn": 2,
                "user": "Of those, which use steel?",
                "answer": answer,
                "events": events,
                "tool_calls": [e for e in events if e["type"] == "tool_call"],
                "tool_results": [e for e in events if e["type"] == "tool_result"],
                "history": [{"role": "user", "content": "List XYZ's projects."}],
                "history_source": "production_prepare_conversation_messages",
                "durable_messages": [],
                "persistence_verified": True,
            },
        ],
        "trace": {
            "provider_calls": [
                {
                    "turn": 2,
                    "messages": [
                        {
                            "role": "user",
                            "content": "REFERENCE_DATA="
                            + json.dumps(
                                {
                                    "task": {"task_id": "read"},
                                    "manager_candidates": [
                                        {"manager": m} for m in managers
                                    ],
                                    "task_evidence": [],
                                }
                            ),
                        }
                    ],
                }
            ]
        },
        "schema_index": {m: {"relations": []} for m in managers},
    }
    run["schema_index"]["Project"]["relations"] = [
        {
            "name": "materialsList",
            "path": ["materialsList", "items"],
            "target": "Material",
        }
    ]
    return adjudication.build_adjudication_request(expectation("E034", 1), run, 1)


def material_response(
    packet: dict[str, Any],
    fact_ids: list[str],
    semantic_ids: list[str],
    *,
    supported: bool = True,
) -> dict[str, Any]:
    answer = packet["packet"]["answer"]
    facts = deepcopy(expectation("E034", 1)["facts"])
    checks = {
        item["id"]: {
            "passed": True,
            "reason": "Explicit synthetic independent review fixture.",
            "evidence_ids": ["answer"],
        }
        for item in packet["packet"]["semantic_checks"]
    }
    checks["answer_supported"].update(passed=supported, evidence_ids=semantic_ids)
    checks["no_repeated_clarification"].update(
        context_sha256=packet["packet"]["context_sha256"], repetitions=[]
    )
    return {
        "schema_version": packet["schema_version"],
        "status": "completed",
        "request_sha256": packet["request_sha256"],
        "answer_sha256": packet["packet"]["answer_sha256"],
        "facts": facts,
        "fact_support": {
            field: {
                "status": "present",
                "answer_quotes": [answer],
                "evidence_ids": fact_ids,
            }
            for field in facts
        },
        "citations": [],
        "semantic_checks": checks,
    }


def parsed_and_scored(
    packet: dict[str, Any], value: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed = adjudication.parse_adjudication_response(
        packet, json.dumps(value), judge_id="synthetic-offline-review"
    )
    scored = adjudication.score_adjudicated(
        expectation("E034", 1), parsed, registry=REGISTRY
    )
    return parsed, scored


def grounding(scored: dict[str, Any]) -> dict[str, Any]:
    return next(
        c
        for c in scored["dimensions"]["A"]["checks"]
        if c["field"] == "evidence_grounding"
    )


def test_new_blind_contract_delivers_explicit_exact_linkage_and_independent_review() -> (
    None
):
    packet = request()
    provider = OfflineJudge(
        [TextChunkEvent(response(packet)), DoneEvent(TokenUsage(1, 2))]
    )
    result = asyncio.run(
        adjudication.adjudicate_turn(packet, judge_id="offline", judge=provider)
    )
    assert result["status"] == "completed"
    contract = packet["packet"]["grounding_contract"]
    assert contract["version"] == "3"
    assert contract["evidence_binding"] == {
        "operator": "exact_id_intersection",
        "fact_support_scope": "data_fields",
        "semantic_check": "answer_supported",
        "reference_scope": "existing_eligible_evidence_ids",
    }
    instruction = provider.seen[0].content
    assert "exact-ID intersection" in instruction
    assert "independently verify" in instruction
    assert "manager qualifier" in instruction
    assert "Additional witnesses" in instruction
    assert "never copy or supplement IDs automatically" in instruction
    delivered = json.loads(provider.seen[1].content)
    assert delivered["grounding_contract"] == contract
    assert result["model_performance_measured"] is False


@pytest.mark.parametrize(
    "shared",
    [
        ["turn-2/call-2/Material", "turn-2/call-3/Project"],
        ["turn-2/call-3/Material", "turn-2/call-3/Project"],
        ["turn-2/call-3/Project", "turn-2/call-5/ProjectPart", "turn-2/call-6/Part"],
    ],
    ids=["direct", "nested", "bridge"],
)
def test_multiple_legitimate_shared_source_paths_remain_admissible(
    shared: list[str],
) -> None:
    packet = material_packet()
    fact_ids = ["turn-2/call-1/Customer", *shared]
    value = material_response(packet, fact_ids, [*shared, "turn-2/call-4/Material"])
    parsed, scored = parsed_and_scored(packet, value)
    assert parsed["status"] == "completed"
    assert grounding(scored)["status"] == "pass"
    assert scored["passed"] is True
    assert grounding(scored)["evidence_ids"] == sorted(shared)
    assert parsed["observation"]["fact_support"] == value["fact_support"]
    assert (
        parsed["semantic_judgment"]["checks"]["answer_supported"]["evidence_ids"]
        == value["semantic_checks"]["answer_supported"]["evidence_ids"]
    )


@pytest.mark.parametrize(
    "fact_material,semantic_material",
    [
        ("turn-2/call-2/Material", "turn-2/call-3/Material"),
        ("turn-2/call-3/Material", "turn-2/call-2/Material"),
        ("turn-2/call-4/Material", "turn-2/call-3/Material"),
    ],
    ids=["distinct_valid_ids", "reverse_valid_ids", "different_material_row"],
)
def test_distinct_ids_never_gain_automatic_equivalence_or_union(
    fact_material: str, semantic_material: str
) -> None:
    packet = material_packet()
    project = "turn-2/call-3/Project"
    value = material_response(
        packet, [fact_material, project], [semantic_material, project]
    )
    parsed, scored = parsed_and_scored(packet, value)
    assert parsed["status"] == "completed"
    assert grounding(scored)["status"] == "fail"
    assert grounding(scored)["evidence_ids"] == [project]
    assert parsed["observation"]["fact_support"] == value["fact_support"]
    assert parsed["semantic_judgment"]["checks"]["answer_supported"][
        "evidence_ids"
    ] == [semantic_material, project]


@pytest.mark.parametrize(
    "bad_id", ["invented", "turn-2/call-3/Part", "turn-99/call-2/Material"]
)
@pytest.mark.parametrize("section", ["fact_support", "answer_supported"])
def test_invalid_or_wrong_manager_qualified_ids_remain_judge_failures(
    bad_id: str, section: str
) -> None:
    packet = material_packet()
    common = ["turn-2/call-2/Material", "turn-2/call-3/Project"]
    value = material_response(packet, common, common)
    if section == "fact_support":
        value["fact_support"]["constraints"]["evidence_ids"] = [bad_id]
    else:
        value["semantic_checks"]["answer_supported"]["evidence_ids"] = [bad_id]
    parsed, scored = parsed_and_scored(packet, value)
    assert parsed["status"] == "judge_failure"
    assert parsed["observation"]["facts"] is None
    assert scored["passed"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "wrong_material",
        "false_membership",
        "wrong_owner",
        "partial_materials",
        "missing_material",
    ],
)
def test_actual_unsupported_claims_keep_independent_false_verdict(defect: str) -> None:
    packet = material_packet(defect)
    eligible = [row["id"] for row in packet["packet"]["evidence"]]
    # This fixture explicitly represents an independent negative semantic review.
    # ID alignment does not make the payload true or override that judgment.
    value = material_response(packet, eligible, eligible, supported=False)
    if defect == "missing_material":
        value["fact_support"]["constraints"].update(
            status="unsupported", evidence_ids=[]
        )
    parsed, scored = parsed_and_scored(packet, value)
    assert parsed["status"] == "completed"
    assert parsed["observation"]["facts"]["constraints"]["all_materials"] == ["M02"]
    assert parsed["semantic_judgment"]["checks"]["answer_supported"]["passed"] is False
    assert scored["dimensions"]["A"]["status"] == "fail"
    assert scored["passed"] is False
    if defect == "missing_material":
        assert grounding(scored)["status"] == "fail"
        assert "constraints" in grounding(scored)["unsupported_fields"]


def test_shared_source_ids_never_repair_an_incorrect_result_claim() -> None:
    packet = material_packet()
    common = ["turn-2/call-2/Material", "turn-2/call-3/Project"]
    value = material_response(packet, common, common)
    value["facts"]["result_ids"] = ["P01"]
    parsed, scored = parsed_and_scored(packet, value)
    assert parsed["observation"]["facts"]["result_ids"] == ["P01"]
    assert grounding(scored)["status"] == "pass"
    assert scored["dimensions"]["R"]["status"] == "fail"
    assert scored["passed"] is False


def test_contract_is_value_free_and_legacy_version_two_request_is_untouched() -> None:
    packet = request()
    legacy = deepcopy(packet)
    legacy["packet"]["grounding_contract"] = {
        "version": "2",
        "data_fields": packet["packet"]["grounding_contract"]["data_fields"],
    }
    legacy["request_sha256"] = adjudication._request_digest(legacy)
    before = deepcopy(legacy)
    parsed = adjudication.parse_adjudication_response(
        legacy, response(legacy), judge_id="offline-legacy-control"
    )
    assert parsed["status"] == "completed"
    assert legacy == before
    changed = deepcopy(expectation())
    changed["facts"]["values"] = {"DO_NOT_DISCLOSE_GOLD": "918273.45"}
    from tests.experiments.test_gm_eval_adjudication import saved_run

    blind = adjudication.build_adjudication_request(changed, saved_run(), 0)
    assert "DO_NOT_DISCLOSE_GOLD" not in json.dumps(blind)
    assert "918273.45" not in json.dumps(blind)
