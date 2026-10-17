"""Offline extraction-contract controls, never live judge-accuracy measurements."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from experiments.gm_eval import adjudication, catalog
from experiments.gm_eval.oracle import expected_turn
from tests.experiments.test_gm_eval_adjudication import saved_run
from tests.experiments.test_gm_eval_extraction_revision_r55 import _roundtrip


POLICY = "recommendation-claims/1"
FIELDS = {"gaps", "customer_evidence", "recommendation_criterion"}


def _contract(identifier="E096", turn=0, version="1.15"):
    case = next(c for c in catalog.load_catalog() if c["id"] == identifier)
    return expected_turn(case, turn, reference_version=version)


def _facts(contract):
    facts = deepcopy(contract["facts"])
    # Independently annotated complete comparison, including the excluded customer.
    facts["gaps"] = {"C01": 70, "C02": -100, "C03": 20}
    facts["customer_evidence"] = {
        "C01": {
            "actuals": {"2023": 100, "2024": 120, "2025": 140},
            "forecast_2031": 260,
            "plan_2031": 190,
            "gap": 70,
        },
        "C02": {
            "actuals": {"2023": 300, "2024": 280, "2025": 260},
            "forecast_2031": 140,
            "plan_2031": 240,
            "gap": -100,
        },
        "C03": {
            "actuals": {"2023": 50, "2024": 55, "2025": 60},
            "forecast_2031": 90,
            "plan_2031": 70,
            "gap": 20,
        },
    }
    return facts


def test_new_requests_bind_only_three_corrected_field_descriptions():
    contract = _contract()
    request, _ = _roundtrip(contract, _facts(contract))
    assert request["packet"].get("extraction_policy") == POLICY
    current = request["packet"]["fact_fields"]
    legacy = adjudication.fact_schemas(contract)
    assert {name for name in current if current[name] != legacy[name]} == FIELDS
    for name in FIELDS:
        assert {k: v for k, v in current[name].items() if k != "description"} == {
            k: v for k, v in legacy[name].items() if k != "description"
        }
    for name in ("gaps", "customer_evidence"):
        text = current[name]["description"]
        assert "excluded" in text and "negative" in text and "prose" in text
        assert "never" in text.lower()
        assert "Exclude entities mentioned only" not in text
    text = current["customer_evidence"]["description"]
    for component in ("actuals", "forecast_2031", "plan_2031", "gap"):
        assert component in text
    assert "answer_supported" in text and "status" in text
    criterion = current["recommendation_criterion"]
    assert "positive_2031_forecast_minus_existing_plan" in criterion["description"]
    assert "only" in criterion["description"] and "wrong" in criterion["description"]
    assert "enum" not in criterion


@pytest.mark.parametrize("version", ["1.10", "1.11", "1.12", "1.13", "1.14", "1.15"])
def test_legacy_shapes_and_all_business_contracts_remain_exact(version):
    hashes = {
        "1.10": "b1283d9f0715bea738164774021b8a699e2bb4ac7399052e08bf968fede1732f",
        "1.11": "e5cb8a25afd1e4e475d2b34ffb1b12e30bedd1fc05bd3d8e3e7ae1cda383749f",
        "1.12": "0b9d4e33d390ca4883a2cd93a0158c7d2ab7e3a2c7a280126f96db3386b493aa",
        "1.13": "38b56a3165cce05108fb1792879cd94b99e4c98de61fae081112d519e8b761c6",
        "1.14": "6f06c99720909a4c6c447177d2e61063a4d6c68fa0d6c3464b238013cb63a25f",
        "1.15": "db342295120fce7da32669801a3ae2cf3214e7dd3bdacc105ed86d795709b464",
    }
    rows = []
    for case in catalog.load_catalog(reference_version=version):
        for turn in range(len(case["turns"])):
            contract = expected_turn(case, turn, reference_version=version)
            rows.append(
                {"expectation": contract, "shapes": adjudication.fact_schemas(contract)}
            )
    assert len(rows) == 149
    encoded = json.dumps(
        rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert sha256(encoded.encode()).hexdigest() == hashes[version]


def test_archived_request_keeps_shapes_system_text_and_response_binding():
    contract = _contract()
    current, _ = _roundtrip(contract, _facts(contract))
    legacy = deepcopy(current)
    legacy["packet"].pop("extraction_policy", None)
    shapes = adjudication.fact_schemas(contract, extraction_policy="legacy")
    legacy["packet"]["fact_fields"] = shapes
    packet = legacy["packet"]
    packet["response_schema"] = adjudication._response_schema(
        shapes,
        packet["semantic_checks"],
        [r["id"] for r in packet["evidence"]],
        packet["grounding_contract"]["data_fields"],
    )
    legacy["request_sha256"] = adjudication._request_digest(legacy)
    assert current["request_sha256"] != legacy["request_sha256"]
    assert sha256(
        adjudication.judge_messages(legacy)[0].content.encode()
    ).hexdigest() == (
        "0c9dddc88e277187f7a2a5efd1ccd2eb417c2625160c7142ebfb138d618c1508"
    )
    assert (
        adjudication.judge_messages(current)[0]
        == adjudication.judge_messages(legacy)[0]
    )
    raw = {
        "schema_version": legacy["schema_version"],
        "request_sha256": legacy["request_sha256"],
        "answer_sha256": packet["answer_sha256"],
        "status": "ungradable",
        "reason": "Archived control, no external request.",
    }
    result = adjudication.parse_adjudication_response(
        current, json.dumps(raw), judge_id="offline-binding-control"
    )
    assert result["status"] != "completed"
    assert "stale_adjudication_response" in str(result["failure_flags"])


def test_unknown_and_out_of_scope_policies_are_rejected():
    contract = _contract()
    with pytest.raises(ValueError, match="policy"):
        adjudication.fact_schemas(contract, extraction_policy="unknown")
    with pytest.raises(ValueError, match="policy"):
        adjudication.fact_schemas(_contract("E004"), extraction_policy=POLICY)
    current, _ = _roundtrip(contract, _facts(contract))
    current["packet"]["extraction_policy"] = "unknown"
    current["request_sha256"] = adjudication._request_digest(current)
    with pytest.raises(ValueError, match="policy"):
        adjudication.judge_messages(current)


@pytest.mark.parametrize("policy", [True, [], {}, 1.0])
def test_mistyped_policy_is_a_stable_validation_error(policy):
    with pytest.raises(ValueError, match="policy"):
        adjudication.fact_schemas(_contract(), extraction_policy=policy)


def test_python_request_entry_point_can_explicitly_rebuild_legacy_policy():
    contract = _contract()
    run = saved_run()
    run["case_id"] = contract["case_id"]
    request = adjudication.build_adjudication_request(
        contract, run, 0, extraction_policy="legacy"
    )
    assert "extraction_policy" not in request["packet"]
    assert request["packet"]["fact_fields"] == adjudication.fact_schemas(contract)


@pytest.mark.parametrize("damage", ["reference", "description"])
def test_policy_marker_cannot_override_an_archived_or_inconsistent_contract(damage):
    contract = _contract()
    request, _ = _roundtrip(contract, _facts(contract))
    if damage == "reference":
        request["reference_version"] = "1.14"
    else:
        request["packet"]["fact_fields"]["gaps"]["description"] = "legacy"
    request["request_sha256"] = adjudication._request_digest(request)
    with pytest.raises(ValueError, match="inconsistent_extraction_policy"):
        adjudication.judge_messages(request)


def test_annotations_remain_subject_to_independent_answer_support():
    contract = _contract()
    _, parsed = _roundtrip(
        contract,
        _facts(contract),
        semantic_overrides={"answer_supported": False},
    )
    result = adjudication.score_adjudicated(
        contract, parsed, registry=contract["manager_interfaces"]
    )
    assert result["dimensions"]["C"]["status"] == "pass"
    assert result["dimensions"]["A"]["status"] == "fail"
    assert not result["passed"]


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "missing",
        "wrong_gap",
        "wrong_history",
        "extra",
        "annotations",
        "wrong_year",
        "reversed",
        "free_text",
    ],
)
def test_complete_comparisons_pass_but_missing_wrong_or_extra_claims_still_fail(damage):
    contract = _contract()
    facts = _facts(contract)
    if damage == "missing":
        facts["gaps"].pop("C02")
        facts["customer_evidence"].pop("C02")
    elif damage == "wrong_gap":
        facts["gaps"]["C02"] = 100
    elif damage == "wrong_history":
        facts["customer_evidence"]["C02"]["actuals"]["2024"] = 0
    elif damage == "extra":
        facts["gaps"]["C99"] = 100
    elif damage == "annotations":
        facts["customer_evidence"]["C01"]["active"] = True
    elif damage == "wrong_year":
        facts["recommendation_criterion"] = "positive_2029_forecast_minus_existing_plan"
    elif damage == "reversed":
        facts["recommendation_criterion"] = "positive_2031_existing_plan_minus_forecast"
    elif damage == "free_text":
        facts["recommendation_criterion"] = (
            "positive 2031 forecast-minus-plan gaps, descending"
        )
    request, parsed = _roundtrip(contract, facts)
    assert request["packet"].get("extraction_policy") == POLICY
    assert parsed["status"] == "completed", "Incorrect claims must remain representable"
    result = adjudication.score_adjudicated(
        contract, parsed, registry=contract["manager_interfaces"]
    )
    assert result["dimensions"]["C"]["status"] == ("pass" if damage is None else "fail")
    # This shared protocol fixture does not model the required plan quantity query;
    # its I/A checks are independent. Only the complete C contract is controlled here.


def test_blind_policy_never_reveals_gold_ids_values_or_recommendation_membership():
    contract = _contract()
    changed = deepcopy(contract)
    changed["facts"]["gaps"] = {"SECRET_GOLD": "918273.45"}
    changed["facts"]["customer_evidence"] = {
        "SECRET_GOLD": next(iter(contract["facts"]["customer_evidence"].values()))
    }
    shapes = adjudication.fact_schemas(contract, extraction_policy=POLICY)
    assert shapes == adjudication.fact_schemas(changed, extraction_policy=POLICY)
    text = json.dumps(shapes)
    assert "SECRET_GOLD" not in text and "918273.45" not in text


def test_nonrecommendation_requests_keep_their_original_policy_scope():
    contract = _contract("E079")
    request, _ = _roundtrip(contract, contract["facts"])
    assert "extraction_policy" not in request["packet"]
    assert request["packet"]["fact_fields"] == adjudication.fact_schemas(contract)
