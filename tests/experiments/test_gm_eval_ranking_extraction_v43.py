"""Blind ordered extraction and coverage vocabulary, with no Gold repair."""

from copy import deepcopy
import json

import pytest

from experiments.gm_eval import adjudication, semantic_contracts
from tests.experiments.test_gm_eval_scoring import control, expected, score
from tests.experiments import test_gm_eval_adjudication as fixtures


def test_visible_table_order_is_a_requested_fact_without_ranking_prose():
    description = adjudication.fact_schemas(expected("E074"))["ranked_ids"][
        "description"
    ]
    assert "presentation order" in description
    assert "table" in description
    assert "ranking word" in description
    assert "presentation order" in adjudication.JUDGE_INSTRUCTION
    assert "tool order" in adjudication.JUDGE_INSTRUCTION


def test_ranking_coverage_has_one_shared_closed_vocabulary():
    vocabulary = getattr(semantic_contracts, "RANKING_COVERAGE", None)
    assert isinstance(vocabulary, dict)
    assert set(vocabulary) == {"known_values_only", "all_candidates", "unknown"}
    shape = adjudication.fact_schemas(expected("E081"))["ranking_coverage"]
    assert shape["type"] == ["string", "null"]
    assert set(shape["enum"]) == {*vocabulary, None}
    assert expected("E081")["facts"]["ranking_coverage"] in vocabulary
    assert "ranking_coverage" in adjudication.JUDGE_INSTRUCTION
    for label in vocabulary:
        assert label in shape["description"]
        assert label in adjudication.JUDGE_INSTRUCTION


def test_extraction_version_is_shared_with_oracle_metadata():
    assert adjudication.SCHEMA_VERSION == "1.5"
    assert getattr(semantic_contracts, "ADJUDICATION_SCHEMA_VERSION", None) == "1.5"
    assert expected("E081")["adjudication_schema_version"] == "1.5"


@pytest.mark.parametrize(
    "coverage", ["highest_known", "all", "overall", 0, False, {}, []]
)
def test_unrecognized_coverage_values_are_not_silently_normalized(coverage):
    shape = adjudication.fact_schemas(expected("E081"))["ranking_coverage"]
    assert not adjudication._matches_shape(coverage, shape)


@pytest.mark.parametrize("coverage", ["all_candidates", "unknown", None])
def test_wrong_or_absent_coverage_still_fails(coverage):
    contract = expected("E081")
    observation, judgment = control(contract)
    observation["facts"]["ranking_coverage"] = coverage
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert not result["passed"]
    assert result["dimensions"]["C"]["status"] == "fail"
    assert observation == before


@pytest.mark.parametrize("damage", ["reverse", "missing", "extra", "absent"])
def test_visible_order_is_never_filled_or_repaired_from_reference(damage):
    contract = expected("E074")
    observation, judgment = control(contract)
    original = observation["facts"]["ranked_ids"]
    wrong = {
        "reverse": list(reversed(original)),
        "missing": original[:-1],
        "extra": [*original, "P99"],
        "absent": None,
    }[damage]
    observation["facts"]["ranked_ids"] = wrong
    before = deepcopy(observation)
    assert not score(contract, observation, judgment)["passed"]
    assert observation == before


@pytest.mark.parametrize("version", ["1.3", "1.4"])
def test_legacy_response_requires_its_original_request(monkeypatch, version):
    with monkeypatch.context() as old:
        old.setattr(adjudication, "SCHEMA_VERSION", version)
        request = fixtures.request()
    raw = fixtures.response(request)
    assert (
        adjudication.parse_adjudication_response(
            request, raw, judge_id="offline-legacy"
        )["status"]
        == "completed"
    )
    new_request = fixtures.request()
    assert new_request["schema_version"] == "1.5"
    assert (
        adjudication.parse_adjudication_response(
            new_request, raw, judge_id="offline-mismatch"
        )["status"]
        == "judge_failure"
    )
    relabelled = json.loads(raw)
    relabelled["schema_version"] = "1.5"
    assert (
        adjudication.parse_adjudication_response(
            new_request, json.dumps(relabelled), judge_id="offline-relabel"
        )["status"]
        == "judge_failure"
    )


def test_closed_vocabulary_does_not_disclose_the_expected_gold_value():
    first = expected("E081")
    second = deepcopy(first)
    second["facts"]["ranking_coverage"] = "all_candidates"
    assert (
        adjudication.fact_schemas(first)["ranking_coverage"]
        == adjudication.fact_schemas(second)["ranking_coverage"]
    )
