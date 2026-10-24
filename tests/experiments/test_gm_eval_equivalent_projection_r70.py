"""Extra real query evidence must not poison a separately used metric route."""

from copy import deepcopy

import pytest

from tests.experiments.test_gm_eval_equivalent_sources_r70 import equivalent_control
from tests.experiments.test_gm_eval_equivalent_fx_r70 import fx_control
from tests.experiments.test_gm_eval_scoring import bind_control_trace, score


def add_query(observation, *, fields, rows, position=0, identifier="extra-query"):
    trace = observation["trace"]
    query = {
        "id": identifier,
        "name": "query",
        "root_manager": "Project",
        "managers": ["Project"],
        "manager_fields": {"Project": list(rows[0])},
        "arguments": {"manager": "Project", "fields": fields},
        "output": {
            "data": rows,
            "total_count": len(rows),
            "has_more": False,
            "complete": True,
        },
    }
    trace["tool_calls"].insert(position, query)
    trace["evidence"].insert(
        position,
        {
            "id": identifier,
            "manager": "Project",
            "origin": "tool",
            "call_id": identifier,
        },
    )
    return query


@pytest.mark.parametrize("position", [0, 1])
@pytest.mark.parametrize("kind", ["identity", "other_metric", "structured_identity"])
def test_extra_ineligible_query_does_not_poison_a_separately_used_metric(
    position, kind
):
    expected, observation, judgment = equivalent_control()
    rows = [{"id": 91, "code": "unrelated-project", "name": "Separate identity result"}]
    fields = ["id", "code", "name"]
    if kind == "other_metric":
        rows[0]["issuedGrossAmount"] = "24000"
        fields.append("issuedGrossAmount")
    elif kind == "structured_identity":
        fields = [{"field": field} for field in fields]
    add_query(observation, fields=fields, rows=rows, position=position)
    bind_control_trace(observation, judgment)
    result = score(expected, observation, judgment)
    assert result["dimensions"]["I"]["status"] == "pass", result
    assert result["dimensions"]["A"]["status"] == "pass", result
    assert result["passed"], result


def test_identity_support_can_coexist_with_full_metric_support():
    expected, observation, judgment = equivalent_control()
    add_query(
        observation,
        fields=["id", "code", "name"],
        rows=[{"id": 3, "code": "P03", "name": "Cobalt"}],
    )
    observation["extraction"]["evidence_ids"].append("extra-query")
    observation["fact_support"]["result_ids"]["evidence_ids"].append("extra-query")
    judgment["checks"]["answer_supported"]["evidence_ids"].append("extra-query")
    bind_control_trace(observation, judgment)
    result = score(expected, observation, judgment)
    assert result["dimensions"]["I"]["status"] == "pass", result
    assert result["dimensions"]["A"]["status"] == "pass", result


@pytest.mark.parametrize(
    "damage",
    [
        "missing_returned_input",
        "wrong_metric_input",
        "wrong_null",
        "extra_population",
        "wrong_fx",
    ],
)
def test_structurally_qualifying_false_metric_evidence_is_not_ignored(damage):
    expected, observation, judgment = (
        fx_control() if damage == "wrong_fx" else equivalent_control()
    )
    actual = observation["trace"]["tool_calls"][0]
    duplicate = add_query(
        observation,
        fields=deepcopy(actual["arguments"]["fields"]),
        rows=deepcopy(actual["output"]["data"]),
    )
    if damage == "missing_returned_input":
        duplicate["output"]["data"][0].pop("netPrice")
    elif damage == "wrong_metric_input":
        duplicate["output"]["data"][0]["netPrice"] = "999"
    elif damage == "wrong_null":
        next(row for row in duplicate["output"]["data"] if row["code"] == "P02")[
            "planUnits"
        ] = "0"
    elif damage == "extra_population":
        duplicate["output"]["data"][0]["code"] = "unrelated-project"
    elif damage == "wrong_fx":
        next(row for row in duplicate["output"]["data"] if row["code"] == "P03")[
            "fxRate"
        ] = "0.9"
    bind_control_trace(observation, judgment)
    result = score(expected, observation, judgment)
    assert result["dimensions"]["I"]["status"] == "fail", result
    # The false extra result was not used by the independently reviewed answer.
    assert result["dimensions"]["A"]["status"] == "pass", result
    assert not result["passed"]


@pytest.mark.parametrize(
    "damage",
    [
        "identity_only",
        "missing_selected_metric_input",
        "wrong_used_inputs",
        "wrong_used_fx",
    ],
)
def test_unused_correct_metric_query_cannot_rescue_wrong_or_nonmetric_used_evidence(
    damage,
):
    expected, observation, judgment = (
        fx_control() if damage == "wrong_used_fx" else equivalent_control()
    )
    actual = observation["trace"]["tool_calls"][0]
    if damage == "identity_only":
        fields = ["id", "code", "name"]
        rows = [{"id": 3, "code": "P03", "name": "Cobalt"}]
    else:
        fields = deepcopy(actual["arguments"]["fields"])
        rows = deepcopy(actual["output"]["data"])
        if damage == "missing_selected_metric_input":
            fields.remove("netPrice")
            for row in rows:
                row.pop("netPrice")
        elif damage == "wrong_used_fx":
            next(row for row in rows if row["code"] == "P03")["fxRate"] = "0.9"
        else:
            rows[0]["netPrice"] = "999"
    add_query(observation, fields=fields, rows=rows)
    observation["extraction"]["evidence_ids"] = ["extra-query"]
    for support in observation["fact_support"].values():
        support["evidence_ids"] = ["extra-query"]
    judgment["checks"]["answer_supported"]["evidence_ids"] = ["answer", "extra-query"]
    bind_control_trace(observation, judgment)
    result = score(expected, observation, judgment)
    assert result["dimensions"]["A"]["status"] == "fail", result
    if damage in {"identity_only", "missing_selected_metric_input"}:
        assert result["dimensions"]["I"]["status"] == "pass", result
    assert not result["passed"]


@pytest.mark.parametrize("form", ["structured", "shorthand"])
def test_valid_metric_projection_stays_eligible_in_supported_field_forms(form):
    expected, observation, judgment = equivalent_control()
    query = observation["trace"]["tool_calls"][0]
    query["arguments"]["fields"] = [
        {"field": name} if form == "structured" else {name: None}
        for name in query["arguments"]["fields"]
    ]
    add_query(
        observation,
        fields=["id", "code", "name"],
        rows=[{"id": 3, "code": "P03", "name": "Cobalt"}],
    )
    bind_control_trace(observation, judgment)
    assert score(expected, observation, judgment)["passed"]


@pytest.mark.parametrize("form", ["nested_relation", "nested_arguments"])
def test_nested_metric_names_do_not_make_a_root_identity_query_eligible(form):
    expected, observation, judgment = equivalent_control()
    actual = observation["trace"]["tool_calls"][0]
    fields = ["id", "code"]
    if form == "nested_relation":
        fields.append({"otherMetric": deepcopy(actual["arguments"]["fields"])})
        rows = [
            {
                "id": 3,
                "code": "P03",
                "otherMetric": deepcopy(actual["output"]["data"][2]),
            }
        ]
    else:
        fields.append(
            {
                "field": "name",
                "arguments": {
                    field: "unused" for field in actual["arguments"]["fields"]
                },
            }
        )
        rows = [{"id": 3, "code": "P03", "name": "Cobalt"}]
    add_query(observation, fields=fields, rows=rows)
    bind_control_trace(observation, judgment)
    result = score(expected, observation, judgment)
    assert result["dimensions"]["I"]["status"] == "pass", result
    assert result["passed"], result
