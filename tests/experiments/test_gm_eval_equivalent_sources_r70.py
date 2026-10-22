"""Prospective offline source-route controls, never historical regrading."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from experiments.gm_eval import adjudication, catalog, oracle
from experiments.gm_eval.scoring import _sources_satisfied, make_judge_packet
from experiments.gm_eval.seeds import build_seed
from tests.experiments.test_gm_eval_scoring import (
    bind_control_trace,
    case,
    control,
    score,
)


ARCHIVE_HASHES = {
    "1.10": "b1283d9f0715bea738164774021b8a699e2bb4ac7399052e08bf968fede1732f",
    "1.11": "e5cb8a25afd1e4e475d2b34ffb1b12e30bedd1fc05bd3d8e3e7ae1cda383749f",
    "1.12": "0b9d4e33d390ca4883a2cd93a0158c7d2ab7e3a2c7a280126f96db3386b493aa",
    "1.13": "38b56a3165cce05108fb1792879cd94b99e4c98de61fae081112d519e8b761c6",
    "1.14": "6f06c99720909a4c6c447177d2e61063a4d6c68fa0d6c3464b238013cb63a25f",
    "1.15": "db342295120fce7da32669801a3ae2cf3214e7dd3bdacc105ed86d795709b464",
    "1.16": "c63a12ee67bedb381a47524201c62593088ea6d2fd862499f15272278e006a77",
}


@pytest.mark.parametrize("version", ARCHIVE_HASHES)
def test_all_149_archived_expectations_and_blind_shapes_are_unchanged(version):
    rows = []
    for row in catalog.load_catalog(reference_version=version):
        for turn in range(len(row["turns"])):
            expected = oracle.expected_turn(row, turn, reference_version=version)
            rows.append(
                {"expectation": expected, "shapes": adjudication.fact_schemas(expected)}
            )
    assert len(rows) == 149
    encoded = json.dumps(
        rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert sha256(encoded).hexdigest() == ARCHIVE_HASHES[version]


def equivalent_control(
    *, version="1.17", question=None, restrictions=None, seed_data=None
):
    row = deepcopy(case("E081"))
    row["id"] = "E001"
    if question is not None:
        row["turns"] = [question]
    if restrictions is not None:
        row["source_restrictions"] = restrictions
    data = (
        build_seed(row["snapshot"], row["fixture_variant"])
        if seed_data is None
        else seed_data
    )
    expected = oracle.expected_turn(row, 0, seed_data=data, reference_version=version)
    observation, judgment = control(expected)
    answer = (
        "Cobalt has the highest known 2027 existing net revenue plan: "
        "240 * 100 EUR = 24000 EUR. All nine plans are in EUR; "
        "Borealis has unknown quantity, so its revenue is unknown, not zero. "
        "An unconditional winner cannot be guaranteed."
    )
    fields = ["id", "code", "planYear", "planUnits", "netPrice", "currency"]
    rows = [
        {
            "id": index,
            "code": project["code"],
            "planYear": project["plan_year"],
            "planUnits": project["plan_units"],
            "netPrice": project["net_price"],
            "currency": project["currency"],
        }
        for index, project in enumerate(data["project"], 1)
    ]
    observation["answer"] = answer
    observation["extraction"].update(
        reference_version=version,
        answer_sha256=sha256(answer.encode()).hexdigest(),
        evidence_ids=["project-plan"],
    )
    observation["trace"] = {
        "observation_complete": True,
        "discovery": [{"candidates": ["Project"], "selected": ["Project"]}],
        "evidence": [
            {
                "id": "project-plan",
                "manager": "Project",
                "origin": "tool",
                "call_id": "query-plan",
            }
        ],
        "tool_calls": [
            {
                "id": "query-plan",
                "name": "query",
                "root_manager": "Project",
                "managers": ["Project"],
                "manager_fields": {"Project": fields},
                "arguments": {
                    "manager": "Project",
                    "fields": fields,
                    "filters": {"planYear_Exact": 2027},
                },
                "output": {
                    "data": rows,
                    "total_count": len(rows),
                    "has_more": False,
                    "complete": True,
                },
            }
        ],
    }
    observation["citations"] = ["project-plan"]
    for item in observation["fact_support"].values():
        item.update(answer_quotes=[answer], evidence_ids=["project-plan"])
    judgment["answer_sha256"] = sha256(answer.encode()).hexdigest()
    for item in judgment["checks"].values():
        item["evidence_ids"] = ["answer", "project-plan"]
    bind_control_trace(observation, judgment)
    return expected, observation, judgment


def test_prospective_default_allows_complete_used_equivalent_metric_inputs():
    assert catalog.REFERENCE_VERSION == "1.17"
    expected, observation, judgment = equivalent_control()
    assert score(expected, observation, judgment)["passed"]
    assert len(observation["trace"]["tool_calls"][0]["output"]["data"]) == 9
    # Trusted reference input values never enter a blind judgment packet.
    packet = make_judge_packet(expected, observation)
    assert "equivalent_routes" not in json.dumps(packet)
    assert "population" not in packet


def test_117_inherits_all_existing_facts_shapes_and_semantic_obligations():
    count = 0
    for row in catalog.load_catalog():
        for turn in range(len(row["turns"])):
            previous = oracle.expected_turn(row, turn, reference_version="1.16")
            prospective = oracle.expected_turn(row, turn, reference_version="1.17")
            for field in (
                "facts",
                "dimensions",
                "semantic_checks",
                "entity_identity_reference",
            ):
                assert prospective[field] == previous[field]
            assert adjudication.fact_schemas(prospective) == adjudication.fact_schemas(
                previous
            )
            count += 1
    assert count == 149


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_metric",
        "partial_page",
        "missing_row",
        "invented_value",
        "missing_field",
        "wrong_currency",
        "wrong_year",
        "unknown_as_zero",
        "unused",
        "unreviewed",
        "failed_query",
        "unselected_field",
        "missing_completeness",
        "duplicate_identity",
    ],
)
def test_equivalent_route_preserves_source_metric_population_and_support(damage):
    expected, observation, judgment = equivalent_control()
    call = observation["trace"]["tool_calls"][0]
    rows = call["output"]["data"]
    if damage == "wrong_metric":
        for row in rows:
            row["grossPrice"] = row.pop("netPrice")
        call["manager_fields"]["Project"].remove("netPrice")
        call["manager_fields"]["Project"].append("grossPrice")
    elif damage == "partial_page":
        call["output"].update(complete=False, has_more=True, total_count=10)
    elif damage == "missing_row":
        rows.pop(1)  # Unknown plans are part of the population, too.
        call["output"]["total_count"] = len(rows)
    elif damage == "invented_value":
        rows[0]["netPrice"] = "999"
    elif damage == "missing_field":
        rows[0].pop("planUnits")
    elif damage == "wrong_currency":
        rows[0]["currency"] = "USD"
    elif damage == "wrong_year":
        rows[0]["planYear"] = 2026
    elif damage == "unknown_as_zero":
        next(row for row in rows if row["code"] == "P02")["planUnits"] = "0"
    elif damage == "unused":
        for item in observation["fact_support"].values():
            item["evidence_ids"] = []
    elif damage == "unreviewed":
        judgment["checks"]["answer_supported"]["evidence_ids"] = ["answer"]
    elif damage == "failed_query":
        call["error"] = True
    elif damage == "unselected_field":
        call["arguments"]["fields"].remove("netPrice")
    elif damage == "missing_completeness":
        call["output"].pop("has_more")
    elif damage == "duplicate_identity":
        rows[1]["code"] = rows[0]["code"]
    bind_control_trace(observation, judgment)
    assert not score(expected, observation, judgment)["passed"]


@pytest.mark.parametrize("version", ["1.10", "1.16"])
def test_archived_source_contract_does_not_silently_regrade_alternative(version):
    expected, observation, judgment = equivalent_control(version=version)
    result = score(expected, observation, judgment)
    assert result["dimensions"]["I"]["status"] == "fail"


@pytest.mark.parametrize(
    "question",
    [
        "Use only ProjectCommercial to rank 2027 net planned revenue.",
        "Bitte verwende ausschließlich den Manager ProjectCommercial für den Planumsatz.",
    ],
)
def test_explicit_source_or_provenance_contract_is_not_replaced(question):
    expected, observation, judgment = equivalent_control(
        question=question, restrictions={"planned_net_revenue": ["ProjectCommercial"]}
    )
    assert not score(expected, observation, judgment)["passed"]


def test_undeclared_manager_with_equal_numbers_is_not_equivalent():
    expected, observation, judgment = equivalent_control()
    for requirement in expected["source_requirements"]:
        requirement.pop("equivalent_routes", None)
    assert not score(expected, observation, judgment)["passed"]


def test_source_matching_is_generic_and_does_not_recognize_product_or_case_names():
    requirement = {
        "role": "independent_metric",
        "alternatives": [["PublishedMetric"]],
        "required_fields": {},
        "equivalent_routes": [
            {
                "manager": "AuthorizedInputs",
                "identity_field": "key",
                "rows": [{"key": "r", "input": 2}],
                "required_fields": ["key", "input"],
            }
        ],
    }
    assert _sources_satisfied(requirement, {"AuthorizedInputs"})
    assert not _sources_satisfied(requirement, {"UnrelatedInputs"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("metric", "gross_invoice_revenue"),
        ("unit", "USD"),
        ("years", [2026]),
        ("values", {"P03": "99999.00"}),
        ("ranking_coverage", "all_values_known"),
    ],
)
def test_equal_route_does_not_waive_answer_metric_units_values_or_unknowns(
    field, value
):
    expected, observation, judgment = equivalent_control()
    observation["facts"][field] = value
    assert not score(expected, observation, judgment)["passed"]


def test_independent_rejection_of_actual_use_still_fails():
    expected, observation, judgment = equivalent_control()
    judgment["checks"]["answer_supported"]["passed"] = False
    assert not score(expected, observation, judgment)["passed"]


def test_undeclared_currency_conversion_does_not_get_an_equivalent_route():
    row = deepcopy(case("E081"))
    data = build_seed(row["snapshot"], row["fixture_variant"])
    data["project"][0]["currency"] = "USD"
    data["exchange_rate"].append(
        {
            "base_currency": "USD",
            "quote_currency": "EUR",
            "as_of": "2026-10-03",
            "rate": "1",
            "source": "declared offline control",
        }
    )
    expected = oracle.expected_turn(row, 0, seed_data=data, reference_version="1.17")
    assert not any(
        "equivalent_routes" in requirement
        for requirement in expected["source_requirements"]
    )


def test_disjoint_complete_input_pages_can_cover_the_same_population():
    expected, observation, judgment = equivalent_control()
    trace = observation["trace"]
    original = trace["tool_calls"][0]
    rows = original["output"]["data"]
    trace["tool_calls"] = []
    trace["evidence"] = []
    for index, offset in enumerate(range(0, len(rows), 5)):
        call = deepcopy(original)
        call["id"] = f"query-page-{index}"
        call["arguments"].update(offset=offset, limit=5)
        call["output"] = {
            "data": rows[offset : offset + 5],
            "total_count": len(rows),
            "has_more": offset + 5 < len(rows),
            "complete": False,
        }
        trace["tool_calls"].append(call)
        trace["evidence"].append(
            {
                "id": f"page-{index}",
                "manager": "Project",
                "origin": "tool",
                "call_id": call["id"],
            }
        )
    ids = [record["id"] for record in trace["evidence"]]
    observation["extraction"]["evidence_ids"] = ids
    observation["citations"] = ids
    for support in observation["fact_support"].values():
        support["evidence_ids"] = ids
    for check in judgment["checks"].values():
        check["evidence_ids"] = ["answer", *ids]
    bind_control_trace(observation, judgment)
    assert score(expected, observation, judgment)["passed"]


def test_blind_request_parse_score_roundtrip_uses_117_and_equivalent_source():
    from tests.experiments.test_gm_eval_adjudication import saved_run

    expected, observation, _ = equivalent_control()
    call = observation["trace"]["tool_calls"][0]
    run = saved_run()
    run["case_id"] = expected["case_id"]
    run["schema_index"] = {"Project": {"relations": []}}
    run["trace"] = {"provider_calls": []}
    turn = run["turns"][0]
    turn.update(
        user="Which project has the highest known planned net revenue in 2027?",
        answer=observation["answer"],
    )
    turn["events"][0]["args"] = call["arguments"]
    turn["events"][1]["result"] = call["output"]
    turn["durable_messages"] = []
    turn["events"][2]["content"] = turn["answer"]
    turn["events"][:0] = [
        {
            "type": "tool_call",
            "task_id": "read",
            "id": "discovery",
            "name": "search_managers",
            "args": {"query": "planned net revenue inputs"},
        },
        {
            "type": "tool_result",
            "task_id": "read",
            "id": "discovery",
            "name": "search_managers",
            "result": [{"manager": "Project"}],
        },
    ]
    turn["tool_calls"] = [
        event for event in turn["events"] if event["type"] == "tool_call"
    ]
    turn["tool_results"] = [
        event for event in turn["events"] if event["type"] == "tool_result"
    ]
    request = adjudication.build_adjudication_request(expected, run, 0)
    assert request["reference_version"] == "1.17"
    ids = [record["id"] for record in request["observation"]["trace"]["evidence"]]
    assert ids and all(
        record["manager"] == "Project" for record in request["packet"]["evidence"]
    )
    packet = request["packet"]
    result = {
        "schema_version": request["schema_version"],
        **(
            {"additional_quantities": {"reviewed_entire_answer": True, "claims": []}}
            if request["schema_version"] == "1.6"
            else {}
        ),
        "status": "completed",
        "request_sha256": request["request_sha256"],
        "answer_sha256": packet["answer_sha256"],
        "facts": observation["facts"],
        "fact_support": {
            field: {
                "status": "present",
                "answer_quotes": [turn["answer"]],
                "evidence_ids": ids,
            }
            for field in observation["facts"]
        },
        "citations": [],
        "semantic_checks": {
            check["id"]: {
                "passed": True,
                "reason": "Offline annotated input arithmetic and qualified answer usage.",
                "evidence_ids": ["answer", *ids],
            }
            for check in packet["semantic_checks"]
        },
    }
    parsed = adjudication.parse_adjudication_response(
        request, json.dumps(result), judge_id="offline-control"
    )
    result = score(expected, parsed["observation"], parsed["semantic_judgment"])
    assert result["passed"], result


def test_route_population_uses_mutated_seed_inputs_and_unrelated_identity():
    data = build_seed("RANK", "missing_revenue")
    data["project"][3].update(
        code="Different-identity", name="Different label", net_price="50"
    )
    expected, observation, judgment = equivalent_control(seed_data=data)
    assert score(expected, observation, judgment)["passed"]
    observation["trace"]["tool_calls"][0]["output"]["data"][3]["netPrice"] = "100"
    bind_control_trace(observation, judgment)
    assert not score(expected, observation, judgment)["passed"]


def test_single_project_request_requires_only_its_complete_eligible_subset():
    row = deepcopy(case("E081"))
    row.update(
        contract="revenue",
        fixture_variant="base",
        turns=["What is Aurora's existing 2027 net revenue plan?"],
    )
    expected = oracle.expected_turn(row, 0, reference_version="1.17")
    route = expected["source_requirements"][0]["equivalent_routes"][0]
    assert [project["code"] for project in route["rows"]] == ["P01"]
    _, observation, judgment = equivalent_control(seed_data=build_seed("RANK", "base"))
    observation["facts"] = deepcopy(expected["facts"])
    observation["fact_support"] = {
        field: {
            "status": "present",
            "answer_quotes": [observation["answer"]],
            "evidence_ids": ["project-plan"],
        }
        for field in expected["facts"]
    }
    call = observation["trace"]["tool_calls"][0]
    call["output"]["data"] = [call["output"]["data"][0]]
    call["output"]["total_count"] = 1
    call["arguments"]["filters"] = {"code": "P01"}
    # This is a separately annotated control, not a reused judgment of the
    # previous ranking answer.
    observation["answer"] = (
        "Aurora existing net revenue plan for 2027 is 120 * 100 EUR = 12000 EUR."
    )
    digest = sha256(observation["answer"].encode()).hexdigest()
    observation["extraction"]["answer_sha256"] = judgment["answer_sha256"] = digest
    for support in observation["fact_support"].values():
        support["answer_quotes"] = [observation["answer"]]
    judgment["checks"] = {
        check["id"]: {
            "passed": True,
            "reason": "Independently annotated single-project control.",
            "evidence_ids": ["answer", "project-plan"],
        }
        for check in expected["semantic_checks"]
    }
    bind_control_trace(observation, judgment)
    assert score(expected, observation, judgment)["passed"]


def test_visible_citations_can_name_the_used_equivalent_route():
    expected, observation, judgment = equivalent_control(
        question="Compare 2027 net planned revenue and cite sources."
    )
    assert expected["citation_policy"] == "visible_required"
    assert score(expected, observation, judgment)["passed"]
    observation["citations"] = []
    assert not score(expected, observation, judgment)["passed"]


def test_explicit_physical_provenance_stays_on_its_original_interfaces():
    row = deepcopy(case("E081"))
    row["contract"] = "provenance"
    expected = oracle.expected_turn(row, 0, reference_version="1.17")
    assert not any(
        requirement.get("equivalent_routes")
        for requirement in expected["source_requirements"]
    )
    assert expected["facts"]["provenance"]["planned_revenue"] == "Calculation"


def test_equivalent_route_support_is_independent_of_product_manager_names():
    from experiments.gm_eval.scoring import score_turn
    from tests.experiments.test_gm_eval_scoring import REGISTRY

    expected, observation, judgment = equivalent_control()
    replacement = "AuthorizedMetricInputs"
    expected["source_requirements"][0]["equivalent_routes"][0]["manager"] = replacement
    expected["manager_interfaces"][replacement] = "Database"
    for discovery in observation["trace"]["discovery"]:
        discovery.update(candidates=[replacement], selected=[replacement])
    observation["trace"]["evidence"][0]["manager"] = replacement
    query = observation["trace"]["tool_calls"][0]
    query.update(root_manager=replacement, managers=[replacement])
    query["manager_fields"][replacement] = query["manager_fields"].pop("Project")
    query["arguments"]["manager"] = replacement
    bind_control_trace(observation, judgment)
    result = score_turn(
        expected,
        observation,
        registry={**REGISTRY, replacement: "Database"},
        semantic_judgment=judgment,
    )
    assert result["passed"], result


@pytest.mark.parametrize("restriction", [[], ["Project", "Project"], [None], "Project"])
def test_malformed_trusted_source_restrictions_fail_closed(restriction):
    row = deepcopy(case("E081"))
    row["source_restrictions"] = {"planned_net_revenue": restriction}
    with pytest.raises(ValueError, match="Source restrictions"):
        oracle.expected_turn(row, 0, reference_version="1.17")
