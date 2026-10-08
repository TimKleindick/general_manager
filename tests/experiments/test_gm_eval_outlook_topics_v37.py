"""Open standards and defined-analysis measurements remain distinct obligations."""

from copy import deepcopy

import pytest

from experiments.gm_eval import catalog, oracle
from tests.experiments.test_gm_eval_scoring import control, expected, score


OUTLOOK_CASES = ["E019", "E031", "E043", "E055", "E067"]


@pytest.mark.parametrize("case_id", OUTLOOK_CASES)
def test_open_outlook_requires_standard_and_horizon_at_every_scale(case_id):
    contract = expected(case_id)
    assert contract["facts"]["clarification_topics"] == ["criterion", "horizon"]
    observation, judgment = control(contract)
    observation["facts"]["clarification_topics"] = ["criterion", "horizon"]
    assert score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize("case_id", OUTLOOK_CASES)
@pytest.mark.parametrize(
    "topics",
    [
        [],
        ["criterion"],
        ["horizon"],
        ["criterion", "horizon", "horizon"],
        ["customer_identity", "horizon"],
    ],
)
def test_missing_or_wrong_open_outlook_questions_fail(case_id, topics):
    contract = expected(case_id)
    observation, judgment = control(contract)
    observation["facts"]["clarification_topics"] = topics
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    check = next(
        c
        for c in result["dimensions"]["Q"]["checks"]
        if c["field"] == "clarification_topics"
    )
    assert check["status"] == "fail" and not result["passed"]
    assert observation == before


@pytest.mark.parametrize("case_id", ["E008", "E100"])
def test_defined_development_analysis_still_requires_metric(case_id):
    contract = expected(case_id)
    assert contract["facts"]["clarification_topics"] == ["metric", "horizon"]
    observation, judgment = control(contract)
    assert score(contract, observation, judgment)["passed"]
    observation["facts"]["clarification_topics"] = ["criterion", "horizon"]
    assert not score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize("case_id", OUTLOOK_CASES)
def test_correct_topics_do_not_allow_unrelated_or_premature_answers(case_id):
    contract = expected(case_id)
    observation, judgment = control(contract)
    observation["facts"]["clarification_topics"] = ["criterion", "horizon"]
    judgment["checks"]["clarification_suitable"]["passed"] = False
    assert not score(contract, observation, judgment)["passed"]
    observation, judgment = control(contract)
    observation["facts"]["result_assertion"] = "some"
    assert not score(contract, observation, judgment)["passed"]


def test_open_outlook_correction_dispatches_by_contract_without_case_id_exception():
    case = next(c for c in catalog.load_catalog() if c["id"] == "E019")
    case["id"] = "E555"
    assert oracle.expected_turn(case, 0)["facts"]["clarification_topics"] == [
        "criterion",
        "horizon",
    ]


def test_versioned_topics_preserve_frozen_catalog_and_disclose_all_instances():
    assert (
        catalog.catalog_sha256()
        == "d105613e4560b63e6bf69181d61ce395a28feb82f91b51b6afa6c30eed0b8c11"
    )
    assert catalog.REFERENCE_VERSION == "1.10"
    corrections = [c for c in catalog.reference_corrections() if c["version"] == "1.9"]
    assert {c["case_id"] for c in corrections} == set(OUTLOOK_CASES)
    for correction in corrections:
        assert correction["before"] == ["metric", "horizon"]
        assert correction["after"] == ["criterion", "horizon"]
