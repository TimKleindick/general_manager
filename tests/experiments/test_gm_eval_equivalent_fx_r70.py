"""Offline controls for declared input-currency conversion equivalence."""

from copy import deepcopy
from hashlib import sha256

import pytest

from experiments.gm_eval import oracle
from experiments.gm_eval.seeds import build_seed
from tests.experiments.test_gm_eval_equivalent_sources_r70 import equivalent_control
from tests.experiments.test_gm_eval_scoring import bind_control_trace, case, score


def fx_seed():
    data = build_seed("RANK", "missing_revenue")
    fx = build_seed("RANK", "fx")
    data["project"][2] = deepcopy(fx["project"][2])
    data["exchange_rate"] = deepcopy(fx["exchange_rate"])
    return data


def fx_control(*, restrictions=None):
    data = fx_seed()
    expected, observation, judgment = equivalent_control(
        seed_data=data, restrictions=restrictions
    )
    answer = (
        "Cobalt's known 2027 net revenue plan is 400 * 100 USD * 0.8 EUR/USD "
        "= 32000 EUR, using the frozen synthetic USD/EUR reference dated "
        "2026-10-03. This is the highest known plan; Borealis has unknown "
        "quantity and revenue, so no unconditional winner is established."
    )
    observation["answer"] = answer
    digest = sha256(answer.encode()).hexdigest()
    observation["extraction"]["answer_sha256"] = judgment["answer_sha256"] = digest
    for support in observation["fact_support"].values():
        support["answer_quotes"] = [answer]
    call = observation["trace"]["tool_calls"][0]
    call["arguments"]["fields"].extend(["fxRate", "fxAsOf", "fxSource"])
    for row, project in zip(call["output"]["data"], data["project"], strict=True):
        row.update(
            fxRate=project["fx_rate"],
            fxAsOf=project["fx_as_of"],
            fxSource=project["fx_source"],
        )
    bind_control_trace(observation, judgment)
    return expected, observation, judgment


def test_independent_declared_fx_inputs_are_an_equivalent_metric_route():
    expected, observation, judgment = fx_control()
    assert score(expected, observation, judgment)["passed"]
    route = expected["source_requirements"][0]["equivalent_routes"][0]
    dollar = next(row for row in route["rows"] if row["code"] == "P03")
    assert dollar["currency"] == "USD"
    assert dollar["fx_rate"] == "0.8"
    assert dollar["fx_as_of"] == "2026-10-03"
    assert dollar["fx_source"] == "frozen synthetic USD/EUR reference"
    # Native EUR inputs need no conversion fields to establish this same metric.
    euro = next(row for row in route["rows"] if row["code"] == "P01")
    assert "fx_rate" not in euro


@pytest.mark.parametrize(
    "damage",
    [
        "missing_rate",
        "wrong_rate",
        "null_rate",
        "missing_date",
        "wrong_date",
        "missing_source",
        "wrong_source",
        "wrong_currency",
        "unselected_rate",
        "same_amount_different_inputs",
        "partial_population",
        "unknown_as_zero",
        "wrong_metric",
        "wrong_amount",
        "unreviewed_use",
    ],
)
def test_fx_equivalence_preserves_inputs_provenance_population_and_answer(damage):
    expected, observation, judgment = fx_control()
    query = observation["trace"]["tool_calls"][0]
    row = next(row for row in query["output"]["data"] if row["code"] == "P03")
    if damage == "missing_rate":
        row.pop("fxRate")
    elif damage == "wrong_rate":
        row["fxRate"] = "0.9"
    elif damage == "null_rate":
        row["fxRate"] = None
    elif damage == "missing_date":
        row.pop("fxAsOf")
    elif damage == "wrong_date":
        row["fxAsOf"] = "2026-10-04"
    elif damage == "missing_source":
        row.pop("fxSource")
    elif damage == "wrong_source":
        row["fxSource"] = "unsupported source with the same rate"
    elif damage == "wrong_currency":
        row["currency"] = "GBP"
    elif damage == "unselected_rate":
        query["arguments"]["fields"].remove("fxRate")
    elif damage == "same_amount_different_inputs":
        row.update(currency="EUR", netPrice="80", fxRate="1")
    elif damage == "partial_population":
        query["output"]["data"].pop()
        query["output"]["total_count"] -= 1
    elif damage == "unknown_as_zero":
        next(row for row in query["output"]["data"] if row["code"] == "P02")[
            "planUnits"
        ] = "0"
    elif damage == "wrong_metric":
        observation["facts"]["metric"] = "gross_issued_invoice_revenue"
    elif damage == "wrong_amount":
        observation["facts"]["values"] = {"P03": "40000.00"}
    elif damage == "unreviewed_use":
        judgment["checks"]["answer_supported"]["evidence_ids"] = ["answer"]
    bind_control_trace(observation, judgment)
    assert not score(expected, observation, judgment)["passed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("fx_rate", None),
        ("fx_rate", "0.9"),
        ("fx_as_of", "2026-10-04"),
        ("fx_source", "wrong declared source"),
    ],
)
def test_project_fx_metadata_must_match_the_independent_reference(field, value):
    data = fx_seed()
    data["project"][2][field] = value
    assert oracle._planned_revenue_inputs(data) == []


@pytest.mark.parametrize(
    "field,value",
    [("base_currency", "GBP"), ("quote_currency", "USD"), ("as_of", "2026-10-04")],
)
def test_missing_matching_base_eur_quote_or_date_is_not_assumed(field, value):
    data = fx_seed()
    data["exchange_rate"][0][field] = value
    with pytest.raises(ValueError, match="exchange rate"):
        oracle.expected_turn(case("E081"), 0, seed_data=data, reference_version="1.17")


@pytest.mark.parametrize(
    "field,value", [("rate", "0.9"), ("source", "different independent source")]
)
def test_a_matching_project_number_does_not_override_independent_rate_provenance(
    field, value
):
    data = fx_seed()
    data["exchange_rate"][0][field] = value
    assert oracle._planned_revenue_inputs(data) == []


def test_fx_route_does_not_override_an_explicit_canonical_source():
    expected, observation, judgment = fx_control(
        restrictions={"planned_net_revenue": ["ProjectCommercial"]}
    )
    assert not score(expected, observation, judgment)["passed"]


def test_fx_route_does_not_replace_requested_physical_provenance():
    row = deepcopy(case("E081"))
    row["contract"] = "provenance"
    expected = oracle.expected_turn(
        row, 0, seed_data=fx_seed(), reference_version="1.17"
    )
    assert not any(
        requirement.get("equivalent_routes")
        for requirement in expected["source_requirements"]
    )
    assert expected["facts"]["provenance"]["planned_revenue"] == "Calculation"


def test_unknown_foreign_plan_stays_unknown_without_an_invented_conversion():
    data = fx_seed()
    data["project"][1].update(currency="USD", fx_rate=None, fx_as_of=None, fx_source="")
    expected, observation, judgment = equivalent_control(seed_data=data)
    # Use the separately annotated mixed-currency answer and extraction header.
    _, fx_observation, _ = fx_control()
    observation["answer"] = fx_observation["answer"]
    digest = sha256(observation["answer"].encode()).hexdigest()
    observation["extraction"]["answer_sha256"] = judgment["answer_sha256"] = digest
    query = observation["trace"]["tool_calls"][0]
    query["arguments"]["fields"].extend(["fxRate", "fxAsOf", "fxSource"])
    for row, project in zip(query["output"]["data"], data["project"], strict=True):
        row.update(
            fxRate=project["fx_rate"],
            fxAsOf=project["fx_as_of"],
            fxSource=project["fx_source"],
        )
    for support in observation["fact_support"].values():
        support["answer_quotes"] = [observation["answer"]]
    bind_control_trace(observation, judgment)
    assert score(expected, observation, judgment)["passed"]
    assert expected["facts"]["missing_ids"] == ["P02"]
    route = expected["source_requirements"][0]["equivalent_routes"][0]
    unknown = next(row for row in route["rows"] if row["code"] == "P02")
    assert unknown["plan_units"] is None
    assert "fx_rate" not in unknown


def test_separate_complete_currency_queries_require_fx_only_for_converted_rows():
    expected, observation, judgment = fx_control()
    trace = observation["trace"]
    original = trace["tool_calls"][0]
    trace["tool_calls"], trace["evidence"] = [], []
    for currency in ("EUR", "USD"):
        query = deepcopy(original)
        query["id"] = f"query-{currency}"
        query["arguments"]["filters"] = {"currency_Exact": currency}
        rows = [row for row in query["output"]["data"] if row["currency"] == currency]
        if currency == "EUR":
            fields = [
                field
                for field in query["arguments"]["fields"]
                if field not in {"fxRate", "fxAsOf", "fxSource"}
            ]
            query["arguments"]["fields"] = query["manager_fields"]["Project"] = fields
            rows = [
                {field: value for field, value in row.items() if field in fields}
                for row in rows
            ]
        query["output"].update(data=rows, total_count=len(rows))
        trace["tool_calls"].append(query)
        trace["evidence"].append(
            {
                "id": f"evidence-{currency}",
                "manager": "Project",
                "origin": "tool",
                "call_id": query["id"],
            }
        )
    refs = [record["id"] for record in trace["evidence"]]
    observation["extraction"]["evidence_ids"] = observation["citations"] = refs
    for support in observation["fact_support"].values():
        support["evidence_ids"] = refs
    for check in judgment["checks"].values():
        check["evidence_ids"] = ["answer", *refs]
    bind_control_trace(observation, judgment)
    assert score(expected, observation, judgment)["passed"]


@pytest.mark.parametrize("mutation", ["missing", "ambiguous"])
def test_independent_exchange_reference_cannot_be_missing_or_ambiguous(mutation):
    data = fx_seed()
    if mutation == "missing":
        data["exchange_rate"] = []
    else:
        data["exchange_rate"].append(deepcopy(data["exchange_rate"][0]))
    with pytest.raises(ValueError, match="exchange rate"):
        oracle.expected_turn(case("E081"), 0, seed_data=data, reference_version="1.17")
