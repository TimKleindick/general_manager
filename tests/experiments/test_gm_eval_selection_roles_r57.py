"""Offline request/parser/scorer controls; no live or historical rescore."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.gm_eval import adjudication, catalog, oracle
from tests.experiments.test_gm_eval_adjudication import saved_run
from tests.experiments.test_gm_eval_business_semantics_r56 import digest
from tests.experiments.test_gm_eval_extraction_roles import contract
from tests.experiments.test_gm_eval_scoring import case


def test_all_149_reference_112_contracts_and_shapes_remain_exact():
    rows = json.loads(
        Path(__file__)
        .with_name("data")
        .joinpath("reference_1_12_hashes.json")
        .read_text()
    )
    assert len(rows) == 149
    for row in rows:
        expected = oracle.expected_turn(
            case(row["case_id"]), row["turn_index"], reference_version="1.12"
        )
        assert digest(expected) == row["contract_sha256"]
        assert digest(adjudication.fact_schemas(expected)) == row["fact_schema_sha256"]


def test_new_reference_inherits_all_112_facts_and_obligations():
    assert catalog.REFERENCE_VERSION == "1.16"
    assert catalog.REFERENCE_VERSIONS == (
        "1.10",
        "1.11",
        "1.12",
        "1.13",
        "1.14",
        "1.15",
        "1.16",
    )
    for row in catalog.load_catalog():
        for index in range(len(row["turns"])):
            old = oracle.expected_turn(row, index, reference_version="1.12")
            new = oracle.expected_turn(row, index, reference_version="1.13")
            for key in (
                "facts",
                "dimensions",
                "source_requirements",
                "semantic_checks",
                "citation_policy",
            ):
                assert new[key] == old[key], (row["id"], index, key)


def role_contract():
    value = contract()
    value["schema_version"] = "1.13"
    value["facts"]["constraints"]["excluded_materials"] = ["PRIVATE_EXCLUDED"]
    value["entity_identity_reference"]["paths"].append(
        {
            "path": ["constraints", "excluded_materials"],
            "manager": "Element",
            "shape": "list",
        }
    )
    value["entity_identity_reference"]["fields"]["material_ids"][
        "selection_target_paths"
    ] = [["constraints", "all_materials"]]
    return value


def test_explicit_positive_path_is_generic_value_free_and_keeps_exclusions_typed():
    value = role_contract()
    shapes = adjudication.fact_schemas(value)
    text = shapes["material_ids"]["description"]
    assert "constraints.all_materials" in text
    assert "constraints.excluded_materials" not in text
    assert "positive inclusion" in text
    assert "Preserve" in text
    assert "Element" in text
    assert "PRIVATE_" not in json.dumps(shapes)
    assert "excluded_materials" in shapes["constraints"]["properties"]
    changed = deepcopy(value)
    changed["facts"]["material_ids"] = ["NEW_GOLD", "ANOTHER_GOLD"]
    changed["facts"]["constraints"] = {
        "all_materials": ["NEW_GOLD"],
        "excluded_materials": ["DIFFERENT_GOLD"],
    }
    assert adjudication.fact_schemas(changed) == shapes


def test_unbound_fields_keep_prior_guidance_and_generic_bound_fields_use_the_role():
    value = role_contract()
    descriptor = value["entity_identity_reference"]["fields"].pop("material_ids")
    value["entity_identity_reference"]["fields"]["component_ids"] = descriptor
    value["facts"]["component_ids"] = value["facts"].pop("material_ids")
    text = adjudication.fact_schemas(value)["component_ids"]["description"]
    assert "constraints.all_materials" in text
    assert "constraints.excluded_materials" not in text
    descriptor.pop("selection_target_paths")
    assert (
        "constraints.excluded_materials"
        in adjudication.fact_schemas(value)["component_ids"]["description"]
    )


@pytest.mark.parametrize(
    "damage", ["empty", "missing", "wrong_manager", "wrong_shape", "string"]
)
def test_invalid_explicit_role_binding_is_rejected_without_fallback(damage):
    value = role_contract()
    field = value["entity_identity_reference"]["fields"]["material_ids"]
    if damage == "empty":
        field["selection_target_paths"] = []
    elif damage == "missing":
        field["selection_target_paths"] = [["constraints", "nonexistent"]]
    elif damage == "string":
        field["selection_target_paths"] = "constraints.all_materials"
    else:
        value["entity_identity_reference"]["paths"][0][
            "manager" if damage == "wrong_manager" else "shape"
        ] = "unrelated"
    with pytest.raises(ValueError, match="selection target"):
        adjudication.fact_schemas(value)


def _roundtrip(facts, *, semantic=True):
    expected = oracle.expected_turn(case("E072"), 0, reference_version="1.13")
    data = json.loads(
        Path(__file__)
        .with_name("data")
        .joinpath("e072_role_protocol_r57.json")
        .read_text()
    )
    run = saved_run()
    run["case_id"] = "E072"
    turn = run["turns"][0]
    turn.update(
        user=data["user"],
        answer=data["answer"],
        events=[
            *data["query_events"],
            {"type": "text_chunk", "content": data["answer"]},
            {"type": "done"},
        ],
    )
    turn["tool_calls"] = [e for e in data["query_events"] if e["type"] == "tool_call"]
    turn["tool_results"] = [
        e for e in data["query_events"] if e["type"] == "tool_result"
    ]
    turn["durable_messages"] = []
    run["schema_index"] = {
        "Project": {"relations": [{"name": "materials", "target": "Material"}]},
        "Material": {"relations": []},
    }
    run["trace"]["provider_calls"][0]["messages"][0]["content"] = (
        "REFERENCE_DATA="
        + json.dumps(
            {
                "task": {"task_id": turn["tool_calls"][0]["task_id"]},
                "manager_candidates": [{"manager": "Project"}, {"manager": "Material"}],
                "task_evidence": [],
            }
        )
    )
    request = adjudication.build_adjudication_request(expected, run, 0)
    ids = [e["id"] for e in request["packet"]["evidence"]]
    raw = {
        "schema_version": request["schema_version"],
        "status": "completed",
        "request_sha256": request["request_sha256"],
        "answer_sha256": request["packet"]["answer_sha256"],
        "facts": facts,
        "fact_support": {
            k: {
                "status": "present",
                "answer_quotes": [data["answer"]],
                "evidence_ids": ids,
            }
            for k in facts
        },
        "citations": [],
        "semantic_checks": {
            c["id"]: {
                "passed": semantic,
                "reason": "Offline injected protocol control, no model-quality claim.",
                "evidence_ids": ["answer", *ids],
            }
            for c in request["packet"]["semantic_checks"]
        },
    }
    parsed = adjudication.parse_adjudication_response(
        request, json.dumps(raw), judge_id="offline-r57-control"
    )
    return expected, request, parsed


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "extra_positive",
        "wrong_positive",
        "missing_positive",
        "missing_exclusion",
        "wrong_exclusion",
        "extra_exclusion",
        "reversed",
        "extra_result",
        "missing_result",
        "extra_scope",
        "unsupported",
    ],
)
def test_e072_real_queries_request_parse_score_keeps_wrong_claims_scored(damage):
    facts = deepcopy(
        oracle.expected_turn(case("E072"), 0, reference_version="1.12")["facts"]
    )
    # Original visible query IDs: Brass=3, Copper=1, P11=9. The parser must
    # preserve bad claims rather than subtract exclusions or fill Gold values.
    facts.update(
        material_ids=[3],
        result_ids=[9],
        constraints={"all_materials": [3], "excluded_materials": [1]},
    )
    if damage == "extra_positive":
        facts["material_ids"] = [3, 1]
    elif damage == "wrong_positive":
        facts["material_ids"] = [1]
    elif damage == "missing_positive":
        facts["material_ids"] = []
    elif damage == "missing_exclusion":
        facts["constraints"].pop("excluded_materials")
    elif damage == "wrong_exclusion":
        facts["constraints"]["excluded_materials"] = [3]
    elif damage == "extra_exclusion":
        facts["constraints"]["excluded_materials"] = [1, 3]
    elif damage == "reversed":
        facts["constraints"] = {"all_materials": [1], "excluded_materials": [3]}
    elif damage == "extra_result":
        facts["result_ids"].append(1)
    elif damage == "missing_result":
        facts["result_ids"] = []
    elif damage == "extra_scope":
        facts["constraints"]["status"] = "ongoing"
    expected, request, parsed = _roundtrip(facts, semantic=damage != "unsupported")
    assert request["reference_version"] == "1.13"
    assert parsed["status"] == "completed"
    assert parsed["observation"]["facts"] == facts
    result = adjudication.score_adjudicated(
        expected, parsed, registry=expected["manager_interfaces"]
    )
    assert result["scored"]
    assert result["passed"] is (damage is None)
    assert result["evaluation_revision"] == "measurement-contracts-7"
    if damage is None:
        for marker in (None, "1.12"):
            broken = deepcopy(parsed)
            if marker is None:
                broken["observation"]["extraction"].pop("reference_version")
            else:
                broken["observation"]["extraction"]["reference_version"] = marker
            assert not adjudication.score_adjudicated(
                expected, broken, registry=expected["manager_interfaces"]
            )["passed"]
