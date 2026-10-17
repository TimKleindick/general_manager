"""Authorized reference semantics, never a live evaluation or historical rescore."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from experiments.gm_eval import adjudication, catalog, oracle
from experiments.gm_eval.seeds import build_seed
from tests.experiments.test_gm_eval_extraction_revision_r55 import _roundtrip
from tests.experiments.test_gm_eval_scoring import case, control, score


def expected(identifier, *, version="1.12", seed=None):
    return oracle.expected_turn(
        case(identifier), 0, reference_version=version, seed_data=seed
    )


def digest(value):
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def test_all_149_reference_111_contracts_and_blind_shapes_remain_exact():
    rows = json.loads(
        Path(__file__)
        .with_name("data")
        .joinpath("reference_1_11_hashes.json")
        .read_text()
    )
    assert len(rows) == 149
    for row in rows:
        contract = oracle.expected_turn(
            case(row["case_id"]), row["turn_index"], reference_version="1.11"
        )
        assert digest(contract) == row["contract_sha256"]
        assert digest(adjudication.fact_schemas(contract)) == row["fact_schema_sha256"]


def test_reference_112_keeps_frozen_catalog_and_explicit_older_selectors():
    assert catalog.REFERENCE_VERSION == "1.15"
    assert catalog.REFERENCE_VERSIONS == (
        "1.10",
        "1.11",
        "1.12",
        "1.13",
        "1.14",
        "1.15",
    )
    assert catalog.catalog_sha256() == catalog.REVIEWED_SHA256
    assert {
        row["case_id"]
        for row in catalog.reference_corrections()
        if row["version"] == "1.12"
    } == {"E072", "E116"}
    for version in ("1.10", "1.11"):
        assert not any(
            row["version"] == "1.12"
            for row in catalog.reference_corrections(reference_version=version)
        )


def test_e072_excludes_mixed_projects_but_allows_other_unexcluded_materials():
    seed = build_seed("RANK", "base")
    brass = next(row for row in seed["project"] if row["code"] == "P11")
    pure = {**deepcopy(brass), "code": "P12", "name": "PureBrass"}
    other = {
        **deepcopy(brass),
        "code": "P13",
        "name": "BrassAndSteel",
        "material_codes": ["M03", "M02"],
    }
    seed["project"].extend([pure, other])
    brass["material_codes"] = ["M03", "M01"]
    current = expected("E072", seed=seed)
    assert current["facts"]["result_ids"] == ["P12", "P13"]
    assert current["facts"]["constraints"] == {
        "all_materials": ["M03"],
        "excluded_materials": ["M01"],
    }
    assert current["facts"]["material_ids"] == ["M03"]
    assert expected("E072", version="1.11", seed=seed)["facts"]["result_ids"] == [
        "P11",
        "P12",
        "P13",
    ]
    assert ["constraints", "excluded_materials"] in [
        row["path"] for row in current["entity_identity_reference"]["paths"]
    ]


@pytest.mark.parametrize(
    "damage",
    [None, "extra_project", "missing_exclusion", "wrong_exclusion", "wrong_inclusion"],
)
def test_e072_positive_and_negative_claims_are_strict(damage):
    contract = expected("E072")
    observation, judgment = control(contract)
    observation["extraction"]["reference_version"] = "1.12"
    facts = observation["facts"]
    if damage == "extra_project":
        facts["result_ids"].append("P01")
    elif damage == "missing_exclusion":
        facts["constraints"].pop("excluded_materials")
    elif damage == "wrong_exclusion":
        facts["constraints"]["excluded_materials"] = ["M02"]
    elif damage == "wrong_inclusion":
        facts["constraints"]["all_materials"] = ["M01"]
    result = score(contract, observation, judgment)
    assert result["passed"] is (damage is None)
    if damage:
        assert result["dimensions"]["R"]["status"] == "fail"


def test_e116_requires_the_sum_and_scope_without_wbs_discovery_or_details():
    contract = expected("E116")
    assert contract["facts"] == {
        "response_mode": "answer",
        "source_kinds": ["actual"],
        "metric": "actual_net_project_cost",
        "unit": "EUR",
        "years": [2025],
        "period": {"start": "2025-01-01", "end_exclusive": "2026-01-01"},
        "total": "4700.00",
        "constraints": {"kind": "actual"},
    }
    assert contract["source_requirements"] == [
        {
            "role": "actual_cost_ledger",
            "alternatives": [["ProjectCost"]],
            "required_fields": {},
        }
    ]
    observation, judgment = control(contract)
    observation["extraction"]["reference_version"] = "1.12"
    assert score(contract, observation, judgment)["passed"]
    assert {row["manager"] for row in observation["trace"]["evidence"]} == {
        "ProjectCost"
    }


def test_e116_total_does_not_depend_on_wbs_assignments_or_label_availability():
    seed = build_seed("RANK", "project_costs")
    seed["wbs_element"] = []
    for row in seed["project_cost"]:
        row.pop("wbs_code", None)
    assert expected("E116", seed=seed)["facts"]["total"] == "4700.00"


def test_e116_ledger_filter_excludes_plans_and_both_date_boundaries():
    seed = build_seed("RANK", "project_costs")
    for row in seed["project_cost"]:
        if (
            row["kind"] != "actual"
            or not "2025-01-01" <= row["cost_date"] < "2026-01-01"
        ):
            row["net_amount"] = "918273.45"
    assert expected("E116", seed=seed)["facts"]["total"] == "4700.00"
    eligible = next(
        row
        for row in seed["project_cost"]
        if row["kind"] == "actual" and "2025-01-01" <= row["cost_date"] < "2026-01-01"
    )
    eligible["net_amount"] = "0.01"
    assert expected("E116", seed=seed)["facts"]["total"] != "4700.00"


def test_e116_missing_ledger_evidence_cannot_pass():
    contract = expected("E116")
    observation, judgment = control(contract)
    observation["trace"]["evidence"] = []
    assert not score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize(
    "detail,valid",
    [
        ("", True),
        (
            " Vollständige WBS-Aufteilung: Engineering 3500 EUR, Projektmanagement 1200 EUR.",
            True,
        ),
        (
            " Davon beispielsweise Engineering 3500 EUR; dies ist nur ein Teilbetrag.",
            True,
        ),
        (" Engineering beträgt 3600 EUR.", False),
        (
            " Vollständige WBS-Aufteilung: Engineering 3500 EUR, Projektmanagement 1300 EUR.",
            False,
        ),
    ],
)
def test_plain_sum_answer_and_optional_details_roundtrip_with_strict_judgment(
    detail, valid
):
    contract = expected("E116")
    answer = (
        "Die tatsächlichen Nettoprojektkosten für 2025 betragen insgesamt 4700 EUR."
        + detail
    )
    request, parsed = _roundtrip(
        contract,
        deepcopy(contract["facts"]),
        answer=answer,
        semantic_overrides={"additional_breakdowns_supported": valid},
    )
    assert request["reference_version"] == "1.12" and parsed["status"] == "completed"
    result = adjudication.score_adjudicated(
        contract, parsed, registry=contract["manager_interfaces"]
    )
    assert result["dimensions"]["C"]["status"] == "pass"
    assert result["passed"] is valid
    if not valid:
        assert result["dimensions"]["A"]["status"] == "fail"


@pytest.mark.parametrize(
    "field,value",
    [
        ("total", "4701.00"),
        ("total", None),
        ("unit", "USD"),
        ("metric", "planned_project_cost"),
        ("years", [2026]),
        ("period", {"start": "2025-01-01", "end_exclusive": "2026-01-02"}),
        ("constraints", {"kind": "plan"}),
        ("source_kinds", ["existing_plan"]),
    ],
)
def test_e116_strict_arithmetic_units_period_kind_and_missing_total(field, value):
    contract = expected("E116")
    observation, judgment = control(contract)
    observation["extraction"]["reference_version"] = "1.12"
    observation["facts"][field] = value
    assert not score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize("judgment_state", ["correct", "wrong", "missing"])
def test_optional_breakdown_does_not_waive_its_independent_grounding(judgment_state):
    contract = expected("E116")
    check = next(
        row
        for row in contract["semantic_checks"]
        if row["id"] == "additional_breakdowns_supported"
    )
    assert "partial" in check["instruction"] and "complete" in check["instruction"]
    observation, judgment = control(contract)
    observation["extraction"]["reference_version"] = "1.12"
    if judgment_state == "missing":
        judgment["checks"].pop(check["id"])
    elif judgment_state == "wrong":
        judgment["checks"][check["id"]]["passed"] = False
    result = score(contract, observation, judgment)
    assert result["passed"] is (judgment_state == "correct")
    if judgment_state == "wrong":
        assert result["dimensions"]["A"]["status"] == "fail"
    if judgment_state == "missing":
        assert result["primary_failure"] == "judge_failure"


@pytest.mark.parametrize("identifier", ["E117", "E118"])
def test_explicit_wbs_requests_remain_strict(identifier):
    contract = expected(identifier)
    assert contract["facts"]["values"] == {"W01": "3500.00", "W02": "1200.00"}
    observation, judgment = control(contract)
    observation["extraction"]["reference_version"] = "1.12"
    for name in ("values", "wbs_names", "result_ids"):
        observation["facts"].pop(name)
    assert score(contract, observation, judgment)["dimensions"]["C"]["status"] == "fail"


def test_e116_blind_total_extraction_is_value_free_and_never_fills_an_omission():
    contract = expected("E116")
    shapes = adjudication.fact_schemas(contract)
    assert "values" not in shapes and "wbs_names" not in shapes
    assert "Never calculate" in shapes["total"]["description"]
    altered = deepcopy(contract)
    altered["facts"]["total"] = "918273.45"
    assert adjudication.fact_schemas(altered) == shapes
    assert "918273.45" not in json.dumps(shapes)
    facts = deepcopy(contract["facts"])
    facts["total"] = None
    request, parsed = _roundtrip(contract, facts)
    assert request["reference_version"] == "1.12"
    assert parsed["status"] == "completed"
    assert (
        adjudication.score_adjudicated(
            contract, parsed, registry=contract["manager_interfaces"]
        )["dimensions"]["C"]["status"]
        == "fail"
    )


@pytest.mark.parametrize("version", [None, "1.10", "1.11", True])
def test_reference_112_requires_matching_extraction_version(version):
    contract = expected("E116")
    observation, judgment = control(contract)
    if version is None:
        observation["extraction"].pop("reference_version", None)
    else:
        observation["extraction"]["reference_version"] = version
    assert score(contract, observation, judgment)["primary_failure"] == "judge_failure"


def test_e086_approval_and_all_unrelated_contracts_are_preserved():
    for original in catalog.load_catalog(reference_version="1.11"):
        if original["id"] in {"E072", "E116"}:
            continue
        for turn in range(len(original["turns"])):
            old = oracle.expected_turn(original, turn, reference_version="1.11")
            current = oracle.expected_turn(original, turn, reference_version="1.12")
            current["schema_version"] = "1.11"
            for alternative in current.get("response_alternatives", {}).values():
                alternative["schema_version"] = "1.11"
            assert current == old
    assert expected("E086")["facts"]["constraints"]["supplier_approved"] is True
