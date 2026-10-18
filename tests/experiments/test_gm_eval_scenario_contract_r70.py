"""Closed scenario representations share values, never missing or extra claims."""

from copy import deepcopy

import pytest

from experiments.gm_eval import adjudication, oracle, scoring
from tests.experiments.test_gm_eval_extraction_revision_r55 import _roundtrip
from tests.experiments.test_gm_eval_scoring import case, control, score


def _contract(identifier="E093", turn=0, version="1.17"):
    return oracle.expected_turn(case(identifier), turn, reference_version=version)


def _control(contract):
    observed, judgment = control(contract)
    observed["extraction"]["reference_version"] = contract["schema_version"]
    return observed, judgment


def _named_bounds(forecast):
    forecast["scenario"] = {
        year: {"low": low, "high": high}
        for year, (low, high) in forecast["scenario"].items()
    }


@pytest.mark.parametrize("identifier,turn", [("E093", 0), ("E094", 0), ("E097", 1)])
def test_closed_named_forecast_bounds_match_the_same_ordered_pair(identifier, turn):
    contract = _contract(identifier, turn)
    observed, judgment = _control(contract)
    _named_bounds(observed["facts"]["forecast"])
    before = deepcopy(observed)
    result = score(contract, observed, judgment)
    assert result["dimensions"]["C"]["status"] == "pass"
    forecast_check = next(
        check
        for check in result["dimensions"]["C"]["checks"]
        if check["field"] == "forecast"
    )
    assert forecast_check["observed"] == before["facts"]["forecast"]
    assert observed == before


@pytest.mark.parametrize(
    "version", ["1.10", "1.11", "1.12", "1.13", "1.14", "1.15", "1.16"]
)
def test_historical_references_keep_named_scenario_objects_failing(version):
    contract = oracle.expected_turn(case("E093"), 0, reference_version=version)
    observed, judgment = _control(contract)
    _named_bounds(observed["facts"]["forecast"])
    assert score(contract, observed, judgment)["dimensions"]["C"]["status"] == "fail"


@pytest.mark.parametrize(
    "version", ["1.10", "1.11", "1.12", "1.13", "1.14", "1.15", "1.16", "1.17"]
)
def test_ordered_scenario_pairs_keep_their_existing_semantics(version):
    contract = _contract(version=version)
    observed, judgment = _control(contract)
    assert score(contract, observed, judgment)["dimensions"]["C"]["status"] == "pass"


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_low",
        "wrong_high",
        "missing_low",
        "missing_high",
        "extra_bound",
        "reversed_object",
        "reversed_list",
        "short_list",
        "long_list",
        "missing_year",
        "extra_year",
        "null_year",
        "boolean_bound",
        "nonfinite_bound",
        "renamed_bound",
        "contradictory_extra_claim",
    ],
)
def test_scenario_format_compatibility_keeps_incorrect_or_incomplete_claims_failing(
    damage,
):
    contract = _contract()
    observed, judgment = _control(contract)
    forecast = observed["facts"]["forecast"]
    _named_bounds(forecast)
    scenario = forecast["scenario"]
    year = next(iter(scenario))
    bounds = scenario[year]
    low, high = bounds["low"], bounds["high"]
    if damage == "wrong_low":
        bounds["low"] = "99999"
    elif damage == "wrong_high":
        bounds["high"] = "99999"
    elif damage == "missing_low":
        bounds.pop("low")
    elif damage == "missing_high":
        bounds.pop("high")
    elif damage == "extra_bound":
        bounds["central"] = "180"
    elif damage == "reversed_object":
        bounds.update(low=high, high=low)
    elif damage == "reversed_list":
        scenario[year] = [high, low]
    elif damage == "short_list":
        scenario[year] = [low]
    elif damage == "long_list":
        scenario[year] = [low, high, high]
    elif damage == "missing_year":
        scenario.pop(year)
    elif damage == "extra_year":
        scenario["2199"] = {"low": low, "high": high}
    elif damage == "null_year":
        scenario[year] = None
    elif damage == "boolean_bound":
        bounds["low"] = True
    elif damage == "nonfinite_bound":
        bounds["low"] = "NaN"
    elif damage == "renamed_bound":
        bounds["LOW"] = bounds.pop("low")
    else:
        bounds["contradictory_scenario"] = {"low": 999, "high": 1000}
    assert score(contract, observed, judgment)["dimensions"]["C"]["status"] == "fail"


@pytest.mark.parametrize(
    "damage", ["slope", "missing_status", "extra_forecast", "values"]
)
def test_named_scenarios_do_not_mask_other_forecast_errors(damage):
    contract = _contract()
    observed, judgment = _control(contract)
    forecast = observed["facts"]["forecast"]
    _named_bounds(forecast)
    if damage == "slope":
        forecast["slope"] = "-999"
    elif damage == "missing_status":
        forecast.pop("status")
    elif damage == "extra_forecast":
        forecast["alternative"] = {"2031": 999}
    else:
        forecast["values"]["2027"] = {"low": "144", "high": "216"}
    assert score(contract, observed, judgment)["dimensions"]["C"]["status"] == "fail"


def test_equivalent_numeric_bounds_keep_the_existing_numeric_comparison():
    contract = _contract()
    observed, judgment = _control(contract)
    _named_bounds(observed["facts"]["forecast"])
    for bounds in observed["facts"]["forecast"]["scenario"].values():
        bounds.update({key: float(value) for key, value in bounds.items()})
    assert score(contract, observed, judgment)["dimensions"]["C"]["status"] == "pass"


@pytest.mark.parametrize("invent_scenario", [False, True])
def test_unavailable_forecast_null_is_preserved_without_inventing_a_scenario(
    invent_scenario,
):
    contract = _contract("E095")
    observed, judgment = _control(contract)
    if invent_scenario:
        observed["facts"]["forecast"]["scenario"] = {"2031": {"low": 1, "high": 2}}
    status = "fail" if invent_scenario else "pass"
    assert score(contract, observed, judgment)["dimensions"]["C"]["status"] == status


def test_general_comparator_retains_strict_dictionary_list_distinction():
    assert not scoring._matches([1, 2], {"low": 1, "high": 2})
    assert not scoring._matches(
        {"scenario": {"2041": [1, 2]}}, {"scenario": {"2041": {"low": 1, "high": 2}}}
    )


@pytest.mark.parametrize("version", ["1.16", "1.17"])
@pytest.mark.parametrize("damage", [None, "wrong", "reversed", "extra"])
def test_blind_extraction_preserves_named_bounds_and_contradictions(version, damage):
    contract = _contract(version=version)
    facts = deepcopy(contract["facts"])
    _named_bounds(facts["forecast"])
    bounds = next(iter(facts["forecast"]["scenario"].values()))
    if damage == "wrong":
        bounds["low"] = 999
    elif damage == "reversed":
        bounds["low"], bounds["high"] = bounds["high"], bounds["low"]
    elif damage == "extra":
        bounds["contradictory"] = [999, 1000]
    _, parsed = _roundtrip(contract, facts)
    assert parsed["status"] == "completed"
    assert parsed["observation"]["facts"]["forecast"] == facts["forecast"]
    # Preserve historical comparison and test the real prospective request path.
    result = adjudication.score_adjudicated(
        contract, parsed, registry=contract["manager_interfaces"]
    )
    expected = "pass" if version == "1.17" and damage is None else "fail"
    assert result["dimensions"]["C"]["status"] == expected
    assert result["primary_failure"] != "judge_failure"
