"""Versioned blind extraction preserves historical contracts and scored errors."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from experiments.gm_eval import adjudication, catalog, oracle
from experiments.gm_eval.cli import catalog_report
from tests.experiments.test_gm_eval_adjudication import saved_run
from tests.experiments.test_gm_eval_scoring import case, control, score


def _digest(value):
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _expected(identifier, version="1.11"):
    return oracle.expected_turn(case(identifier), 0, reference_version=version)


def test_all_149_historical_expectations_and_blind_shapes_remain_exact():
    baseline = json.loads(
        Path(__file__)
        .with_name("data")
        .joinpath("reference_1_10_hashes.json")
        .read_text()
    )
    cases = {row["id"]: row for row in catalog.load_catalog(reference_version="1.10")}
    for row in baseline:
        expected = oracle.expected_turn(
            cases[row["case_id"]], row["turn_index"], reference_version="1.10"
        )
        assert _digest(expected) == row["contract_sha256"]
        assert _digest(adjudication.fact_schemas(expected)) == row["fact_schema_sha256"]
    assert len(baseline) == 149


@pytest.mark.parametrize("version", [None, True, 1.11, "1.12", "1.9", ""])
def test_reference_selector_rejects_unknown_or_mistyped_versions(version):
    with pytest.raises(ValueError, match="reference version"):
        oracle.expected_turn(case("E079"), 0, reference_version=version)
    with pytest.raises(ValueError, match="reference version"):
        catalog.load_catalog(reference_version=version)


def test_catalog_and_report_preserve_the_selected_legacy_version():
    report = catalog_report(reference_version="1.10")
    assert report["catalog_validation"]["reference_version"] == "1.10"
    assert {e["schema_version"] for e in report["oracles"]} == {"1.10"}
    assert not any(c["version"] == "1.11" for c in report["reference_corrections"])
    assert catalog.REFERENCE_VERSION == "1.11"
    assert catalog.catalog_sha256() == catalog.REVIEWED_SHA256
    assert any(c["version"] == "1.11" for c in catalog.reference_corrections())


def test_return_components_are_declared_without_gold_values_or_entity_wording():
    expected = _expected("E079")
    shape = adjudication.fact_schemas(expected)["values"]
    assert set(shape["properties"]) == {"gross", "returns", "net"}
    assert "before returns" in shape["properties"]["gross"]["description"]
    assert "returned" in shape["properties"]["returns"]["description"]
    assert "after returns" in shape["properties"]["net"]["description"]
    assert "Include only entities" not in shape.get("description", "")
    assert (
        "Include only entities"
        in adjudication.fact_schemas(_expected("E077"))["values"]["description"]
    )
    changed = deepcopy(expected)
    changed["facts"]["values"] = {"DO_NOT_LEAK_GOLD_ID": "918273.45"}
    assert adjudication.fact_schemas(changed)["values"] == shape
    assert "918273.45" not in json.dumps(shape)
    assert "DO_NOT_LEAK_GOLD_ID" not in json.dumps(shape)


def test_unavailable_forecast_guidance_does_not_condition_shapes_on_gold_truth():
    expected = _expected("E095")
    shape = adjudication.fact_schemas(expected)["forecast"]
    props = shape["properties"]
    assert "no forecast values" in props["values"]["description"]
    assert "Preserve" in props["values"]["description"]
    assert "comparison" in shape["description"]
    assert "never infer" in props["scenario"]["description"]
    changed = deepcopy(expected)
    changed["facts"]["forecast"]["values"] = {"2199": "918273.45"}
    assert adjudication.fact_schemas(changed)["forecast"] == shape
    assert "2199" not in json.dumps(shape) and "918273.45" not in json.dumps(shape)
    assert adjudication._matches_shape(
        {"status": "wrong", "values": {"2031": 999}}, shape
    )


def _roundtrip(expected, facts):
    # A controlled saved tool trace and injected Judge envelope: no provider call.
    run = saved_run()
    run["case_id"] = expected["case_id"]
    turn = run["turns"][0]
    turn["answer"] = json.dumps(facts, sort_keys=True)
    for event in turn["events"]:
        if event.get("type") == "text_chunk":
            event["content"] = turn["answer"]
    turn["user"] = case(expected["case_id"])["turns"][0]
    managers = {
        m for req in expected["source_requirements"] for m in req["alternatives"][0]
    }
    for manager in sorted(managers - {"Shipment", "Project"}):
        run["schema_index"][manager] = {"relations": []}
        call = {
            "type": "tool_call",
            "task_id": "read",
            "id": manager,
            "name": "query",
            "args": {"manager": manager, "fields": ["code"]},
        }
        result = {
            "type": "tool_result",
            "task_id": "read",
            "id": manager,
            "name": "query",
            "result": {
                "data": [{"code": "SOURCE"}],
                "total_count": 1,
                "has_more": False,
            },
        }
        turn["tool_calls"].append(call)
        turn["tool_results"].append(result)
        turn["events"][-2:-2] = [call, result]
    request = adjudication.build_adjudication_request(expected, run, 0)
    ids = [e["id"] for e in request["packet"]["evidence"]]
    answer = request["packet"]["answer"]
    raw = {
        "schema_version": request["schema_version"],
        "status": "completed",
        "request_sha256": request["request_sha256"],
        "answer_sha256": request["packet"]["answer_sha256"],
        "facts": facts,
        "fact_support": {
            k: {"status": "present", "answer_quotes": [answer], "evidence_ids": ids}
            for k in facts
        },
        "citations": [],
        "semantic_checks": {
            c["id"]: {
                "passed": True,
                "reason": "Synthetic protocol control, no model quality claim.",
                "evidence_ids": ["answer", *ids],
            }
            for c in request["packet"]["semantic_checks"]
        },
    }
    parsed = adjudication.parse_adjudication_response(
        request, json.dumps(raw), judge_id="offline-r55-control"
    )
    return request, parsed


@pytest.mark.parametrize(
    "damage", [None, "wrong", "missing", "extra", "entity", "swapped"]
)
def test_component_claims_parse_and_receive_strict_scored_results(damage):
    expected = _expected("E079")
    facts = deepcopy(expected["facts"])
    if damage == "wrong":
        facts["values"]["net"] = 112
    elif damage == "missing":
        facts["values"].pop("returns")
    elif damage == "extra":
        facts["values"]["extra"] = 1
    elif damage == "entity":
        facts["values"] = {"1": 110}
    elif damage == "swapped":
        facts["values"].update(gross=10, returns=120)
    else:
        facts["values"] = {"gross": 120, "returns": 10, "net": 110}
    request, parsed = _roundtrip(expected, facts)
    assert parsed["status"] == "completed"
    assert request["reference_version"] == "1.11"
    assert parsed["observation"]["extraction"]["reference_version"] == "1.11"
    scored = adjudication.score_adjudicated(
        expected, parsed, registry=expected["manager_interfaces"]
    )
    assert scored["dimensions"]["C"]["status"] == ("pass" if damage is None else "fail")
    assert scored["primary_failure"] != "judge_failure"


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "year_nulls",
        "numeric",
        "plan",
        "missing_status",
        "missing_parameter",
        "zero_imputation",
    ],
)
def test_forecast_errors_stay_representable_and_fail_without_schema_evasion(damage):
    expected = _expected("E095")
    facts = deepcopy(expected["facts"])
    forecast = facts["forecast"]
    if damage == "year_nulls":
        forecast["values"] = {"2027": None}
    elif damage == "numeric":
        forecast["values"] = {"2027": 999}
    elif damage == "plan":
        forecast["existing_plan"] = {"2027": 60}
    elif damage == "missing_status":
        forecast.pop("status")
    elif damage == "missing_parameter":
        forecast.pop("intercept")
    elif damage == "zero_imputation":
        forecast["actuals"]["2024"] = 0
    _, parsed = _roundtrip(expected, facts)
    assert parsed["status"] == "completed"
    scored = adjudication.score_adjudicated(
        expected, parsed, registry=expected["manager_interfaces"]
    )
    assert scored["dimensions"]["C"]["status"] == ("pass" if damage is None else "fail")
    assert scored["primary_failure"] != "judge_failure"


@pytest.mark.parametrize("version", [None, "1.10", True, "unknown"])
def test_new_contract_rejects_missing_wrong_or_mistyped_extraction_version(version):
    expected = _expected("E079")
    observed, judgment = control(expected)
    if version is None:
        observed["extraction"].pop("reference_version", None)
    else:
        observed["extraction"]["reference_version"] = version
    assert score(expected, observed, judgment)["primary_failure"] == "judge_failure"


def test_explicit_null_version_is_not_markerless_legacy_compatibility():
    legacy = _expected("E079", "1.10")
    observed, judgment = control(legacy)
    observed["extraction"].pop("reference_version", None)
    assert score(legacy, observed, judgment)["passed"]
    for bad in (None, "1.11", True):
        observed["extraction"]["reference_version"] = bad
        assert score(legacy, observed, judgment)["primary_failure"] == "judge_failure"


@pytest.mark.parametrize("version", ["1.10", "1.11"])
def test_reference_marker_cannot_be_added_removed_or_changed_with_a_saved_hash(version):
    expected = _expected("E079", version)
    request, _ = _roundtrip(expected, deepcopy(expected["facts"]))
    original_hash = request["request_sha256"]
    for marker in (None, "1.10", "1.11", True):
        changed = deepcopy(request)
        if marker is None:
            changed.pop("reference_version", None)
        else:
            changed["reference_version"] = marker
        if changed == request:
            continue
        assert adjudication._request_digest(changed) != original_hash
        with pytest.raises(
            adjudication.AdjudicationError, match="stale_adjudication_request"
        ):
            adjudication._validate_request(changed)


@pytest.mark.parametrize("version", ["1.10", "1.11"])
def test_selected_reference_reaches_native_harness_metadata(version):
    from tests.experiments.test_gm_eval_harness import _child

    result = _child(
        "reference_version = "
        + repr(version)
        + "\n"
        + r"""
import asyncio, json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.catalog import load_catalog
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import make_script
runtime = bootstrap(manager_count=5)
case = next(c for c in load_catalog(reference_version=reference_version) if c['id']=='E004')
try:
    result = asyncio.run(run_case(runtime, case, make_profile('weak-only'), make_script(case, runtime), reference_version=reference_version))
    print(json.dumps(result))
finally:
    runtime.close()
"""
    )
    assert result["reference_version"] == version
    assert result["status"] == "infrastructure_check"
    assert result["model_performance_measured"] is False
