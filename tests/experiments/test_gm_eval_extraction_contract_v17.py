"""Blind extraction contracts keep explanations and qualifiers accountable."""

from experiments.gm_eval.adjudication import (
    fact_schemas,
    JUDGE_INSTRUCTION,
    SCHEMA_VERSION,
)
from tests.experiments.test_gm_eval_scoring import expected


def test_membership_excludes_explanations_without_discarding_their_claims():
    shapes = fact_schemas(expected("E009"))
    for key in ("result_ids", "ranked_ids", "values"):
        assert "requested result" in shapes[key]["description"]
        assert "excluded" in shapes[key]["description"]
    assert "explanatory claims" in JUDGE_INSTRUCTION


def test_annual_maps_exclude_multiyear_total_without_ignoring_wrong_total():
    assert "multi-year" in fact_schemas(expected("E010"))["plan"]["description"]
    assert "incorrect total" in JUDGE_INSTRUCTION


def test_effective_constraints_can_use_explicit_user_history():
    shapes = fact_schemas(expected("E022", 1))
    assert "effective" in shapes["constraints"]["description"]
    assert "current" in shapes["context"]["description"]
    assert (
        "constraints"
        in JUDGE_INSTRUCTION.split("Only customer_ids", 1)[1].split("may resolve", 1)[0]
    )


def test_units_and_measurement_basis_have_distinct_contracts():
    shapes = fact_schemas(expected("E027"))
    assert "tax basis" in shapes["unit"]["description"]
    assert "quantity basis" in JUDGE_INSTRUCTION
    assert SCHEMA_VERSION == "1.5"


def test_missing_exclusion_is_still_required():
    assert expected("E019", 1)["facts"]["excluded_incomplete_years"] == [2026]


import json
from copy import deepcopy
import pytest
from tests.experiments.test_gm_eval_scoring import control, identity_control, score
from tests.experiments import test_gm_eval_adjudication as fixtures
from experiments.gm_eval import adjudication


@pytest.mark.parametrize("case_id", ["E009", "E010", "E017", "E022", "E027"])
def test_explanations_totals_and_qualifiers_remain_accountable(case_id):
    contract = expected(case_id)
    observation, judgment = control(contract)
    assert score(contract, observation, judgment)["passed"]
    # A hand-authored negative judge result: correct requested membership cannot
    # excuse an unsupported explanation, total, context or measurement basis.
    judgment["checks"]["answer_supported"]["passed"] = False
    judgment["checks"]["answer_supported"]["reason"] = (
        "An explanatory claim contradicts the supplied evidence."
    )
    assert not score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize("damage", ["missing_year", "extra_year", "wrong_value"])
def test_annual_map_errors_are_not_hidden_by_total_exclusion(damage):
    contract = expected("E010")
    observation, judgment = control(contract)
    plan = observation["facts"]["plan"]
    if damage == "missing_year":
        plan.pop(next(iter(plan)))
    elif damage == "extra_year":
        plan["2099"] = "123"
    else:
        plan[next(iter(plan))] = "999999"
    assert not score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize("case_id", ["E009", "E017"])
def test_extra_actual_result_member_still_fails(case_id):
    contract = expected(case_id)
    observation, judgment = control(contract)
    observation["facts"]["result_ids"].append("C99")
    assert not score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize("case_id", ["E019", "E031"])
def test_unasserted_incomplete_year_exclusion_remains_failure(case_id):
    contract = expected(case_id, 1)
    observation, judgment = control(contract)
    observation["facts"]["excluded_incomplete_years"] = None
    assert not score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_manager",
        "ambiguous",
        "missing",
        "boolean",
        "duplicate",
        "extra",
        "wrong_code",
        "wrong_shape",
    ],
)
def test_nested_identity_errors_are_never_repaired_from_gold(damage):
    contract = expected("E008", 1)
    rows = [{"id": 1, "code": "C01"}]
    ids = [1]
    manager = "Customer"
    if damage == "wrong_manager":
        manager = "Project"
    elif damage == "ambiguous":
        rows.append({"id": 1, "code": "C02"})
    elif damage == "missing":
        rows = []
    elif damage == "boolean":
        ids = [True]
    elif damage == "duplicate":
        ids = [1, 1]
    elif damage == "extra":
        ids = [1, 2]
    elif damage == "wrong_code":
        ids = ["C02"]
    else:
        ids = {"id": 1}
    contract["source_requirements"] = [
        {"role": "identity_fixture", "alternatives": [[manager]], "required_fields": {}}
    ]
    observation, judgment = identity_control(contract, manager, rows, [])
    observation["facts"]["context"]["customer_ids"] = ids
    before = deepcopy(observation)
    assert not score(contract, observation, judgment)["passed"]
    assert observation == before


def test_legacy_response_is_parseable_only_against_its_original_request(monkeypatch):
    with monkeypatch.context() as local:
        local.setattr(adjudication, "SCHEMA_VERSION", "1.3")
        legacy = fixtures.request()
    raw = fixtures.response(legacy)
    parsed = adjudication.parse_adjudication_response(
        legacy, raw, judge_id="offline-compatibility"
    )
    assert parsed["status"] == "completed"
    current = fixtures.request()
    assert current["schema_version"] == "1.5"
    assert (
        adjudication.parse_adjudication_response(
            current, raw, judge_id="offline-compatibility"
        )["status"]
        == "judge_failure"
    )
    relabeled = json.loads(raw)
    relabeled["schema_version"] = "1.5"
    assert (
        adjudication.parse_adjudication_response(
            current, json.dumps(relabeled), judge_id="offline-compatibility"
        )["status"]
        == "judge_failure"
    )


def test_new_reference_refuses_legacy_extraction_metadata():
    contract = expected("E106")
    observation, judgment = control(contract)
    assert score(contract, observation, judgment)["passed"]
    observation["extraction"]["adjudication_schema_version"] = "1.3"
    assert score(contract, observation, judgment)["primary_failure"] == "judge_failure"
