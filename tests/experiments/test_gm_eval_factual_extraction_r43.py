"""Blind extraction keeps actual source metadata without supplying answer truth."""

from copy import deepcopy
import json

import pytest

from experiments.gm_eval import adjudication
from tests.experiments.test_gm_eval_adjudication import expectation, saved_run


@pytest.mark.parametrize("interface", ["Database", "ReadOnly", "Calculation"])
def test_judge_receives_captured_interfaces_only_for_selected_source_managers(
    interface,
):
    # R37 E098 named its managers, but the judge had no captured interface metadata.
    run = saved_run()
    run["registry_interfaces"] = {
        "Shipment": {"interface_type": interface + "Interface"},
        "Project": "Database",
        "HiddenUnselectedManager": "ReadOnly",
    }
    original = deepcopy(run)
    request = adjudication.build_adjudication_request(expectation(), run, 0)
    evidence = {item["manager"]: item for item in request["packet"]["evidence"]}
    assert evidence["Shipment"]["interface_type"] == interface
    assert evidence["Project"]["interface_type"] == "Database"
    assert "HiddenUnselectedManager" not in json.dumps(request)
    assert run == original


@pytest.mark.parametrize("registry", [None, {}, {"Shipment": {"unknown": "Database"}}])
def test_missing_interface_capture_is_not_filled_from_reference_or_manager_name(
    registry,
):
    run = saved_run()
    run["registry_interfaces"] = registry
    request = adjudication.build_adjudication_request(expectation(), run, 0)
    assert all("interface_type" not in item for item in request["packet"]["evidence"])


def test_provenance_schema_distinguishes_manager_attribution_from_interface_metadata():
    contract = {"facts": {"provenance": {"component": "Database"}}}
    shapes = adjudication.fact_schemas(contract)
    description = shapes["provenance"]["description"]
    assert "captured interface" in description
    assert "answer" in description and "contradict" in description
    # A wrong claim remains representable for the independent scorer to reject.
    assert adjudication._matches_shape(
        {"component": "wrong interface"}, shapes["provenance"]
    )
    changed = deepcopy(contract)
    changed["facts"]["provenance"]["component"] = "ReadOnly"
    assert adjudication.fact_schemas(changed) == shapes


def test_forecast_schema_explains_asserted_metadata_without_revealing_reference_values():
    contract = expectation("E093")
    shape = adjudication.fact_schemas(contract)["forecast"]
    fields = shape["properties"]
    assert "available" in fields["status"]["description"]
    assert "insufficient_history" in fields["status"]["description"]
    assert "sensitivity_not_confidence" in fields["scenario_kind"]["description"]
    assert "OLS" in fields["method"]["description"]
    assert "never calculate" in fields["intercept"]["description"]
    assert "explicitly complete" in fields["missing_years"]["description"]
    assert adjudication._matches_shape(
        {"status": "wrong claim", "intercept": 991}, shape
    )
    changed = deepcopy(contract)
    changed["facts"]["forecast"]["values"] = {"2199": "987654.32"}
    changed["facts"]["forecast"]["intercept"] = "999999.12"
    assert adjudication.fact_schemas(changed)["forecast"] == shape


def test_source_kind_schema_keeps_requested_measures_and_comparison_scopes_separate():
    shape = adjudication.fact_schemas(expectation("E095"))["source_kinds"]
    description = shape["description"]
    assert "requested" in description and "historical basis" in description
    assert "separate comparisons" in description
    assert "negated" in description
    assert adjudication._matches_shape(["unknown source kind"], shape)


def test_explicitly_requested_plan_revenue_requires_its_source_kind_alongside_actuals():
    # R37 E098 correctly distinguished its requested plan revenue and actual shipments.
    contract = expectation("E098")
    assert contract["facts"]["source_kinds"] == ["actual", "existing_plan"]
    assert contract["facts"]["metric"] == "gross_shipped_quantity"
    assert contract["facts"]["years"] == [2025]
    assert contract["facts"]["planned_revenue"]["year"] == 2027


@pytest.mark.parametrize(
    "damage,dimension",
    [
        ("missing_plan_kind", "I"),
        ("wrong_interface", "I"),
        ("missing_status", "C"),
        ("missing_intercept", "C"),
        ("wrong_slope", "C"),
        ("missing_assumptions", "A"),
    ],
)
def test_extraction_guidance_keeps_missing_and_contradictory_claims_failing(
    damage, dimension
):
    from tests.experiments.test_gm_eval_scoring import control, score

    contract = expectation("E098" if dimension == "I" else "E093")
    observation, judgment = control(contract)
    if damage == "missing_plan_kind":
        observation["facts"]["source_kinds"] = ["actual"]
    elif damage == "wrong_interface":
        observation["facts"]["provenance"]["planned_revenue"] = "Database"
    elif damage == "missing_status":
        observation["facts"]["forecast"].pop("status")
    elif damage == "missing_intercept":
        observation["facts"]["forecast"].pop("intercept")
    elif damage == "wrong_slope":
        observation["facts"]["forecast"]["slope"] = "999"
    else:
        judgment["checks"]["forecast_assumptions"]["passed"] = False
    result = score(contract, observation, judgment)
    assert not result["passed"]
    assert result["dimensions"][dimension]["status"] == "fail"
