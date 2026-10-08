"""Offline contracts, independent arithmetic, and deliberately wrong answers."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import json
from typing import Any

import pytest


def api(name: str) -> Any:
    module_name = f"experiments.gm_eval.{name}"
    assert importlib.util.find_spec(module_name) is not None, (
        f"The {name} contract API has not been implemented"
    )
    return importlib.import_module(module_name)


def case(identifier: str) -> dict[str, Any]:
    return next(row for row in api("catalog").load_catalog() if row["id"] == identifier)


def expected(identifier: str, turn: int = 0) -> dict[str, Any]:
    return api("oracle").expected_turn(case(identifier), turn)


REGISTRY = {
    "Customer": "Database",
    "Project": "Database",
    "Shipment": "Database",
    "Material": "ReadOnly",
    "ProjectCommercial": "Calculation",
    "ShipmentPlan": "ReadOnly",
    "CustomerOutlook": "Calculation",
    "Supplier": "Database",
    "Part": "Database",
    "ProjectPart": "Database",
    "ProjectDependency": "Database",
    "ShipmentReturn": "Database",
    "WbsElement": "ReadOnly",
    "ProjectCost": "Database",
}


def control(expectation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """A scoring unit-test control, never a model or harness-quality result."""
    managers = sorted(
        {
            manager
            for requirement in expectation["source_requirements"]
            for manager in requirement["alternatives"][0]
        }
    )
    fields = {
        name: sorted(
            {
                field
                for requirement in expectation["source_requirements"]
                for field in requirement.get("required_fields", {}).get(name, [])
            }
        )
        for name in managers
    }
    evidence = [
        {
            "id": f"ev-{name}",
            "manager": name,
            "origin": "tool",
            "call_id": f"call-{name}",
        }
        for name in managers
    ]
    answer = "Reviewed synthetic scoring control; wording is deliberately irrelevant."
    digest = sha256(answer.encode()).hexdigest()
    references = [row["id"] for row in evidence]
    observation = {
        "answer": answer,
        "facts": deepcopy(expectation["facts"]),
        "extraction": {
            "status": "validated",
            "adjudication_schema_version": expectation.get(
                "adjudication_schema_version"
            ),
            "origin": "external",
            "validator": "test-review",
            "answer_sha256": digest,
            "evidence_ids": references,
        },
        "trace": {
            "discovery": [{"candidates": managers, "selected": managers}]
            if managers
            else [],
            "evidence": evidence,
            "tool_calls": [
                {
                    "id": f"call-{name}",
                    "managers": [name],
                    "manager_fields": {name: fields[name]},
                    "output": {
                        "test_control": True,
                        **{field: "scoring-control" for field in fields[name]},
                    },
                }
                for name in managers
            ],
        },
        "citations": references,
        "fact_support": {
            field: {
                "status": "present",
                "answer_quotes": [answer],
                "evidence_ids": list(references),
            }
            for field in expectation["facts"]
        },
    }
    judgment = {
        "status": "completed",
        "origin": "external",
        "judge_id": "offline-test-review",
        "answer_sha256": digest,
        "checks": {
            check["id"]: {
                "passed": True,
                "reason": "Reviewed controlled facts and evidence.",
                "evidence_ids": ["answer", *references],
            }
            for check in expectation["semantic_checks"]
        },
    }
    if "no_repeated_clarification" in judgment["checks"]:
        context = {
            "current_question": "Use the already specified metric and horizon.",
            "visible_history": [],
        }
        observation["trace"]["conversation_context"] = context
        judgment["checks"]["no_repeated_clarification"].update(
            context_sha256=api("semantic_contracts").context_digest(context),
            repetitions=[],
        )
    if expectation.get("selection_scope_contract"):
        rows = [
            {
                "id": 2,
                "code": "M02",
                "family": "steel",
                "isActive": True,
                "active": True,
            },
            {
                "id": 4,
                "code": "M04",
                "family": "steel",
                "isActive": True,
                "active": True,
            },
            {
                "id": 5,
                "code": "M05",
                "family": "steel",
                "isActive": True,
                "active": False,
            },
        ]
        columns = list(rows[0])
        observation["trace"]["tool_calls"] = [
            {
                "id": "call-Material",
                "name": "query",
                "root_manager": "Material",
                "arguments": {"manager": "Material", "fields": columns},
                "managers": ["Material"],
                "manager_fields": {"Material": columns},
                "output": {
                    "data": rows,
                    "total_count": 3,
                    "has_more": False,
                    "complete": True,
                },
            }
        ]
    if expectation.get("citation_policy") == "internal_grounding":
        trace_digest = api("scoring").evidence_trace_digest(observation["trace"])
        observation["extraction"]["evidence_trace_sha256"] = trace_digest
        judgment["evidence_trace_sha256"] = trace_digest
    return observation, judgment


def bind_control_trace(observation: dict[str, Any], judgment: dict[str, Any]) -> None:
    """Seal a newly constructed scoring control, not a reused live judgment."""
    digest = api("scoring").evidence_trace_digest(observation["trace"])
    observation["extraction"]["evidence_trace_sha256"] = digest
    judgment["evidence_trace_sha256"] = digest


def score(
    expectation: dict[str, Any],
    observation: dict[str, Any],
    judgment: dict[str, Any] | None,
) -> dict[str, Any]:
    return api("scoring").score_turn(
        expectation, observation, registry=REGISTRY, semantic_judgment=judgment
    )


def test_catalog_is_frozen_complete_and_balanced() -> None:
    catalog = api("catalog")
    cases = catalog.load_catalog()
    summary = catalog.validate_catalog(cases)
    assert summary["total"] == 121
    assert summary["turns"] == 149
    assert summary["by_language"] == {"DE": 41, "FR": 40, "EN": 40}
    assert summary["core_intents"] == 15
    assert summary["core_instances"] == 75
    assert summary["targeted"] == 36
    assert (
        summary["original_catalog_sha256"]
        == "972636e1e2a7ebebdab2fc03d7d17fac73cd60c6c6b8d6c5857a8dc4452b4f6f"
    )
    assert summary["catalog_sha256"] == catalog.catalog_sha256()
    assert summary["reference_corrections"][0]["case_id"] == "E082"
    changed = deepcopy(cases)
    changed[22]["turns"] = ["Changed core intent"]
    with pytest.raises(ValueError):
        catalog.validate_catalog(changed)


def test_every_catalog_turn_has_explicit_contract() -> None:
    oracle = api("oracle")
    contracts = [
        oracle.expected_turn(row, turn)
        for row in api("catalog").load_catalog()
        for turn in range(len(row["turns"]))
    ]
    assert len(contracts) == 149
    assert all(set(row["dimensions"]) == set("DIRCQA") for row in contracts)
    assert all(row["semantic_checks"] for row in contracts)
    assert all(
        row["contract_coverage"] in {"deterministic_and_semantic", "semantic_only"}
        for row in contracts
    )
    assert json.loads(json.dumps(contracts)) == contracts
    with pytest.raises(IndexError):
        oracle.expected_turn(case("E001"), 1)
    with pytest.raises(IndexError):
        oracle.expected_turn(case("E001"), -1)


def test_reference_revenue_rank_and_date_edges() -> None:
    assert expected("E005")["facts"]["values"] == {"P01": "12000.00"}
    assert expected("E006")["facts"]["result_ids"] == ["P02"]
    assert expected("E009")["facts"]["ranked_ids"] == [
        "P03",
        "P04",
        "P01",
        "P05",
        "P06",
    ]
    assert expected("E077")["facts"]["values"] == {"P01": "120"}
    assert expected("E078", 1)["facts"]["values"] == {"P01": "240"}
    assert expected("E079")["facts"]["values"] == {
        "gross": "120",
        "returns": "10",
        "net": "110",
    }


def test_oracle_arithmetic_is_from_seed_inputs_and_rounds_only_final_money() -> None:
    oracle = api("oracle")
    seed = api("seeds").build_seed()
    project = next(row for row in seed["project"] if row["code"] == "P01")
    project.update(plan_units="3", net_price="0.335")
    result = oracle.expected_turn(case("E005"), 0, seed)
    assert result["facts"]["values"] == {"P01": "1.01"}
    project["plan_units"] = None
    assert oracle.expected_turn(case("E005"), 0, seed)["facts"]["values"] == {
        "P01": None
    }


def test_currency_is_explicit_and_unknown_rates_are_not_invented() -> None:
    value = expected("E080")
    assert value["facts"]["result_ids"] == ["P03"]
    assert value["facts"]["values"] == {"P03": "32000.00"}
    assert value["facts"]["fx"]["rate"] == "0.8"
    seed = api("seeds").build_seed(variant="fx")
    seed["exchange_rate"] = []
    with pytest.raises(ValueError, match="rate"):
        api("oracle").expected_turn(case("E080"), 0, seed)


def test_missing_zero_and_reference_discrepancy_are_distinct() -> None:
    assert expected("E081")["facts"]["missing_ids"] == ["P02"]
    assert expected("E081")["facts"]["result_ids"] == ["P03"]
    contract = expected("E082")
    assert contract["facts"]["zero_ids"] == ["P08", "P11"]
    assert contract["facts"]["missing_ids"] == ["P02"]
    assert contract["reference_issues"] == []
    assert contract["reference_corrections"][0]["id"] == "E082-zero-revenue-P11"
    observation, judgment = control(contract)
    result = score(contract, observation, judgment)
    assert result["primary_failure"] == "passed"
    assert result["reference_corrections"][0]["id"] == "E082-zero-revenue-P11"


def test_targeted_relations_are_complete_and_exclude_counterexamples() -> None:
    targets = {
        "E071": ["P09"],
        "E072": ["P11"],
        "E073": ["P01", "P02"],
        "E074": ["P01", "P03", "P04", "P05", "P06", "P07"],
        "E083": ["P01", "P03", "P04", "P05", "P06", "P07"],
        "E084": ["P01"],
        "E085": ["P12"],
        "E086": ["P01"],
        "E087": ["P03", "P04", "P05", "P06", "P07"],
        "E088": ["P01"],
        "E089": ["P02", "P03", "P04"],
        "E090": [],
    }
    for identifier, ids in targets.items():
        assert (
            expected(identifier, int(identifier == "E071"))["facts"]["result_ids"]
            == ids
        )


def test_ols_scenario_missing_history_and_recommendation() -> None:
    forecast = expected("E093")["facts"]["forecast"]
    assert list(forecast["values"].values()) == [
        "180.00",
        "200.00",
        "220.00",
        "240.00",
        "260.00",
    ]
    assert forecast["slope"] == "20"
    assert forecast["scenario"]["2031"] == ["208.00", "312.00"]
    assert expected("E094")["facts"]["forecast"]["slope"] == "-20"
    missing = expected("E095")["facts"]["forecast"]
    assert missing["status"] == "insufficient_history"
    assert missing["values"] is None
    assert missing["missing_years"] == [2024]
    recommendation = expected("E096")["facts"]
    assert recommendation["ranked_ids"] == ["C01", "C03"]
    assert recommendation["gaps"] == {"C01": "70.00", "C02": "-100.00", "C03": "20.00"}
    assert expected("E100", 1)["facts"]["plan"] == expected("E091")["facts"]["plan"]


def test_ols_clamps_negative_values_but_never_imputes_missing_years() -> None:
    oracle = api("oracle")
    series = {2023: "20", 2024: "10", 2025: "0"}
    assert oracle.linear_forecast(series)["values"] == {
        str(year): "0.00" for year in range(2027, 2032)
    }
    assert oracle.linear_forecast({2023: "20", 2025: "0"})["values"] is None
    assert (
        oracle.linear_forecast({2023: "0", 2024: "0", 2025: "0"})["status"]
        == "available"
    )


def test_clarification_and_carryover_are_turn_specific() -> None:
    for identifier in ("E007", "E008", "E018", "E019", "E071", "E078", "E099", "E100"):
        first = expected(identifier)
        assert first["facts"]["response_mode"] == "clarification"
        assert first["facts"]["result_assertion"] == "none"
        assert any(
            c["id"] == "no_repeated_clarification"
            for c in expected(identifier, 1)["semantic_checks"]
        )
    assert expected("E099", 1)["facts"]["result_ids"] == ["P02"]
    assert expected("E099", 2)["facts"]["result_ids"] == ["P03"]
    assert expected("E097", 0)["contract_coverage"] == "semantic_only"
    assert expected("E097", 1)["facts"]["metric"] == "shipped_quantity"


@pytest.mark.parametrize(
    ("identifier", "field", "wrong", "dimension"),
    [
        ("E002", "customer_ids", ["C02"], "R"),
        ("E003", "result_ids", ["M03"], "R"),
        ("E088", "constraints", {"bill_to_customer": "C01", "approved": True}, "R"),
        ("E004", "years", [2026], "C"),
        ("E004", "unit", "kg", "C"),
        ("E090", "result_ids", ["P99"], "R"),
        ("E083", "result_ids", ["P01", "P03"], "R"),
        ("E091", "source_kinds", ["new_forecast"], "I"),
        ("E009", "ranked_ids", ["P03", "P04", "P01", "P05", "P07"], "C"),
    ],
)
def test_wrong_answers_fail_the_responsible_dimension(
    identifier: str, field: str, wrong: Any, dimension: str
) -> None:
    contract = expected(identifier)
    observation, judgment = control(contract)
    observation["facts"][field] = wrong
    result = score(contract, observation, judgment)
    assert result["dimensions"][dimension]["status"] == "fail"
    assert result["primary_failure"] == "model_task_failure"


def test_all_scoring_controls_require_an_external_judge_and_na_stays_na() -> None:
    contract = expected("E003")
    observation, judgment = control(contract)
    result = score(contract, observation, judgment)
    assert result["primary_failure"] == "passed"
    assert result["dimensions"]["C"]["status"] == "N/A"
    assert result["dimensions"]["Q"]["status"] == "N/A"
    no_judge = score(contract, observation, None)
    assert no_judge["primary_failure"] == "judge_failure"
    assert no_judge["dimensions"]["A"]["status"] == "unscored"
    observation["extraction"]["origin"] = "model"
    assert score(contract, observation, judgment)["primary_failure"] == "judge_failure"


def test_registry_and_citation_evidence_cannot_be_self_certified() -> None:
    contract = expected("E098")
    observation, judgment = control(contract)
    bad_registry = {**REGISTRY, "ProjectCommercial": "Database"}
    result = api("scoring").score_turn(
        contract, observation, registry=bad_registry, semantic_judgment=judgment
    )
    assert result["dimensions"]["I"]["status"] == "fail"
    observation["citations"] = ["invented-reference"]
    assert score(contract, observation, judgment)["dimensions"]["A"]["status"] == "fail"
    observation["trace"]["tool_calls"] = []
    assert (
        score(contract, observation, judgment)["primary_failure"] == "harness_failure"
    )


def test_validated_history_and_alternative_calculation_path_are_accepted() -> None:
    contract = expected("E093")
    alternate = deepcopy(contract)
    alternate["source_requirements"][0]["alternatives"].reverse()
    observation, judgment = control(alternate)
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["primary_failure"] == "passed"
    contract = expected("E097", 1)
    observation, judgment = control(contract)
    observation["trace"]["history_evidence"] = deepcopy(
        observation["trace"]["evidence"]
    )
    for entry in observation["trace"]["evidence"]:
        entry.update(origin="history", source_turn=0, source_evidence_id=entry["id"])
    observation["trace"]["discovery"] = []
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["primary_failure"] == "passed"
    assert result["dimensions"]["D"]["status"] == "N/A"


def test_failure_hierarchy_preserves_all_secondary_causes() -> None:
    contract = expected("E004")
    observation, judgment = control(contract)
    flags = [
        {"category": category, "reason": f"Observed {category}", "phase": "test"}
        for category in ("budget_exhausted", "transport_failure", "fixture_invalid")
    ]
    result = api("scoring").score_turn(
        contract,
        observation,
        registry=REGISTRY,
        semantic_judgment=judgment,
        failure_flags=flags,
    )
    assert result["primary_failure"] == "fixture_invalid"
    assert {"budget_exhausted", "transport_failure"} <= set(result["secondary_flags"])


def test_judge_packet_is_blind_and_incomplete_or_stale_judgment_is_unscored() -> None:
    contract = expected("E100", 1)
    observation, judgment = control(contract)
    observation.update(
        model="private-model", previous_score=1, provider_profile={"name": "strong"}
    )
    packet = api("scoring").make_judge_packet(contract, observation)
    assert "private-model" not in json.dumps(packet)
    assert "previous_score" not in packet
    judgment["checks"].pop(next(iter(judgment["checks"])))
    assert score(contract, observation, judgment)["primary_failure"] == "judge_failure"
    observation, judgment = control(contract)
    judgment["answer_sha256"] = "stale"
    assert score(contract, observation, judgment)["primary_failure"] == "judge_failure"


def test_all_turn_contract_checks_agree_with_final_facts_and_controls_pass() -> None:
    for row in api("catalog").load_catalog():
        for turn in range(len(row["turns"])):
            contract = api("oracle").expected_turn(row, turn)
            for dimension in contract["dimensions"].values():
                fields = [check["field"] for check in dimension["checks"]]
                assert len(fields) == len(set(fields)), (row["id"], turn, fields)
                for check in dimension["checks"]:
                    if check["comparison"] == "required_topic_groups":
                        assert row["contract"] == "clarify_actuals" and turn == 0
                        assert check["field"] == "clarification_topics"
                        assert check["expected"] == [
                            ["criterion", "metric"],
                            ["horizon"],
                        ]
                        assert contract["facts"][check["field"]] == [
                            "criterion",
                            "horizon",
                        ]
                    else:
                        assert check["expected"] == contract["facts"][check["field"]]
            observation, judgment = control(contract)
            assert (
                score(contract, observation, judgment)["primary_failure"] == "passed"
            ), (row["id"], turn)


def test_currency_source_is_a_real_project_manager_field() -> None:
    contract = expected("E080")
    fx_role = next(
        row
        for row in contract["source_requirements"]
        if row["role"] == "fixed_exchange_rate"
    )
    assert fx_role["alternatives"] == [["Project"]]
    assert contract["facts"]["fx"]["source"] == "frozen synthetic USD/EUR reference"


def test_only_authorized_e082_gold_changed_and_original_is_reconstructible() -> None:
    catalog = api("catalog")
    cases = catalog.load_catalog()
    correction = catalog.reference_corrections()[0]
    row = next(row for row in cases if row["id"] == "E082")
    assert row["expected"] == correction["after"]
    row["expected"] = correction["before"]
    original_bytes = (
        json.dumps(cases[:100], ensure_ascii=False, indent=2) + "\n"
    ).encode()
    assert sha256(original_bytes).hexdigest() == catalog.ORIGINAL_REVIEWED_SHA256


def test_unapproved_targeted_gold_changes_are_rejected() -> None:
    catalog = api("catalog")
    cases = catalog.load_catalog()
    cases[82]["expected"] = "Only the first page is enough."
    with pytest.raises(ValueError):
        catalog.validate_catalog(cases)


def test_complete_no_tool_trace_is_model_failure_not_judge_failure() -> None:
    contract = expected("E005")
    observation, judgment = control(contract)
    observation["trace"] = {
        "observation_complete": True,
        "tool_calls": [],
        "evidence": [],
        "discovery": [],
    }
    observation["citations"] = []
    observation["extraction"]["evidence_ids"] = []
    for result in judgment["checks"].values():
        result["evidence_ids"] = ["answer"]
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["primary_failure"] == "model_task_failure"
    assert result["dimensions"]["C"]["status"] == "pass"
    assert result["dimensions"]["I"]["status"] == "fail"
    assert result["dimensions"]["D"]["status"] == "fail"


def test_missing_extraction_and_incomplete_trace_never_imply_no_tool_failure() -> None:
    contract = expected("E005")
    result = score(
        contract, {"answer": "Offline infrastructure probe", "facts": None}, None
    )
    assert result["primary_failure"] == "judge_failure"
    assert result["dimensions"]["C"]["status"] == "unscored"
    assert result["dimensions"]["I"]["status"] == "unscored"


def test_calculation_existing_plan_is_alternative_without_relabeled_forecast() -> None:
    contract = expected("E091")
    observation, judgment = control(contract)
    observation["trace"] = {
        "observation_complete": True,
        "discovery": [
            {"candidates": ["CustomerOutlook"], "selected": ["CustomerOutlook"]}
        ],
        "tool_calls": [
            {
                "id": "calc",
                "managers": ["CustomerOutlook"],
                "manager_fields": {"CustomerOutlook": ["existing_plan"]},
                "output": {"existing_plan": 190},
            }
        ],
        "evidence": [
            {
                "id": "calc-plan",
                "manager": "CustomerOutlook",
                "origin": "tool",
                "call_id": "calc",
            }
        ],
    }
    observation["citations"] = ["calc-plan"]
    observation["extraction"]["evidence_ids"] = ["calc-plan"]
    for item in observation["fact_support"].values():
        item["evidence_ids"] = ["calc-plan"]
    for result in judgment["checks"].values():
        result["evidence_ids"] = ["answer", "calc-plan"]
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["primary_failure"] == "passed"
    observation["trace"]["tool_calls"][0]["output"] = {"forecast": 190}
    observation["trace"]["tool_calls"][0]["manager_fields"] = {
        "CustomerOutlook": ["forecast"]
    }
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["dimensions"]["I"]["status"] == "fail"
    assert result["dimensions"]["A"]["status"] == "fail"


def test_nested_selected_relation_is_a_valid_source_path() -> None:
    contract = expected("E098")
    observation, judgment = control(contract)
    observation["trace"]["discovery"] = [
        {
            "candidates": ["Project", "Shipment", "ProjectCommercial"],
            "selected": ["Project", "Shipment", "ProjectCommercial"],
        }
    ]
    material_call = next(
        call
        for call in observation["trace"]["tool_calls"]
        if call["id"] == "call-Material"
    )
    material_call["root_manager"] = "Project"
    material_call["managers"] = ["Project", "Material"]
    material_call["output"] = {"project": {"materials": [{"code": "M01"}]}}
    assert score(contract, observation, judgment)["primary_failure"] == "passed"


def test_authorized_simple_questions_are_paired_and_avoid_needless_clarification() -> (
    None
):
    for identifier, expected_ids in (
        ("E106", ["C01", "C02"]),
        ("E111", ["P01", "P03", "P05"]),
    ):
        contract = expected(identifier)
        assert contract["facts"]["result_ids"] == expected_ids
        assert any(
            check["id"] == "unnecessary_clarification" and check["dimension"] == "Q"
            for check in contract["semantic_checks"]
        )
    names = expected("E111")["facts"]["project_names"]
    assert names == {"P01": "Aurora", "P03": "Cobalt", "P05": "Echo"}
    for identifier in range(106, 122):
        contract = expected(f"E{identifier:03}")
        observation, judgment = control(contract)
        judgment["checks"]["unnecessary_clarification"]["passed"] = False
        assert (
            score(contract, observation, judgment)["dimensions"]["Q"]["status"]
            == "fail"
        )


def test_new_cost_density_and_prior_month_oracles_exclude_adjacent_periods() -> None:
    for identifier in ("E116", "E117", "E118"):
        facts = expected(identifier)["facts"]
        assert facts["values"] == {"W01": "3500.00", "W02": "1200.00"}
        assert facts["total"] == "4700.00"
        assert facts["years"] == [2025]
    density = expected("E119")["facts"]
    assert density["result_ids"] == ["M01"]
    assert density["designation"] == "Copper"
    assert density["values"] == {"M01": "8.96"}
    assert density["unit"] == "g/cm3"
    assert expected("E120")["facts"]["values"] == {"P01": "2500.00"}
    recent = expected("E121")["facts"]
    assert recent["values"] == {"P01": "30"}
    assert recent["period"] == {"start": "2026-09-01", "end_exclusive": "2026-10-01"}


def test_expansion_metadata_retains_original_catalog_identity() -> None:
    summary = api("catalog").validate_catalog(api("catalog").load_catalog())
    assert summary["original_case_count"] == 100
    assert summary["reference_version"] == "1.10"
    assert summary["reference_expansions"][0]["case_ids"] == [
        f"E{number:03}" for number in range(101, 122)
    ]
    assert summary["by_scale"] == {5: 20, 10: 23, 50: 24, 100: 26, 250: 28}


@pytest.mark.parametrize(
    ("identifier", "manager", "graphql_fields", "incorrect_field"),
    [
        (
            "E091",
            "CustomerOutlook",
            {"existingPlan": 190},
            "forecast",
        ),
        (
            "E080",
            "Project",
            {
                "fxRate": "0.8",
                "fxAsOf": "2026-10-03",
                "fxSource": "frozen synthetic USD/EUR reference",
            },
            "fxSpotRate",
        ),
    ],
)
def test_graphql_camelcase_evidence_preserves_required_field_meaning(
    identifier: str,
    manager: str,
    graphql_fields: dict[str, Any],
    incorrect_field: str,
) -> None:
    contract = expected(identifier)
    if identifier == "E091":
        contract["source_requirements"][0]["alternatives"].reverse()
    observation, judgment = control(contract)
    call = next(
        call
        for call in observation["trace"]["tool_calls"]
        if call["id"] == f"call-{manager}"
    )
    call["manager_fields"] = {manager: list(graphql_fields)}
    call["output"] = {"data": {manager: {"edges": [{"node": graphql_fields}]}}}
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["primary_failure"] == "passed"

    # A different business property must still fail even with the right value.
    renamed = dict(graphql_fields)
    original_field = next(iter(renamed))
    renamed[incorrect_field] = renamed.pop(original_field)
    call["manager_fields"] = {manager: list(renamed)}
    call["output"] = {"data": {manager: {"edges": [{"node": renamed}]}}}
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["dimensions"]["I"]["status"] == "fail"
    assert result["dimensions"]["A"]["status"] == "fail"


STEEL_QUESTION = "Welche Stahlwerkstoffe haben wir aktuell in der Datenbank?"
STEEL_SCOPE_REPLY = "Mit aktuell meine ich nicht gelöschte Datensätze; auch inaktive Werkstoffe gehören dazu."


def test_k13_retains_natural_question_and_adds_shared_scope_followup() -> None:
    catalog = api("catalog")
    assert catalog.REFERENCE_VERSION == "1.10"
    cases = [row for row in catalog.load_catalog() if row["core_id"] == "K13"]
    assert len(cases) == 5
    assert {row["managers"] for row in cases} == {5, 10, 50, 100, 250}
    for row in cases:
        assert row["turns"] == [STEEL_QUESTION, STEEL_SCOPE_REPLY]
        assert row["contract"] == "steel_inventory_scope"
    corrections = [
        row for row in catalog.reference_corrections() if row["version"] == "1.3"
    ]
    assert {row["case_id"] for row in corrections} == {
        f"E{number:03}" for number in range(101, 106)
    }
    restored = catalog.load_catalog()
    for correction in corrections:
        row = next(row for row in restored if row["id"] == correction["case_id"])
        row.update(correction["before"])
    prior = (json.dumps(restored, ensure_ascii=False, indent=2) + "\n").encode()
    assert (
        sha256(prior).hexdigest()
        == "d5f3a7d663790ec504845385429473a5bb58c030d930385954c6057cfc00ddd5"
    )


def test_k13_accepts_grounded_current_inventory_including_business_inactive() -> None:
    contract = expected("E101")
    assert contract["facts"]["result_ids"] == ["M02", "M04", "M05"]
    assert contract["facts"]["constraints"] == {"family": "steel", "is_active": True}
    assert not any(
        row["id"] == "unnecessary_clarification" for row in contract["semantic_checks"]
    )
    observation, judgment = control(contract)
    assert score(contract, observation, judgment)["primary_failure"] == "passed"
    observation["facts"]["result_ids"] = ["M02", "M04"]
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "fail"
    assert result["primary_failure"] == "model_task_failure"


def test_k13_accepts_legitimate_scope_clarification_then_actual_carryover() -> None:
    first = expected("E101")
    clarification = first["response_alternatives"]["clarification"]
    observation, judgment = control(clarification)
    observation["trace"]["observation_complete"] = True
    bind_control_trace(observation, judgment)
    result = score(first, observation, judgment)
    assert result["primary_failure"] == "passed"
    assert result["dimensions"]["Q"]["status"] == "pass"
    assert result["dimensions"]["R"]["status"] == "N/A"
    second = expected("E101", 1)
    assert second["facts"]["result_ids"] == ["M02", "M04", "M05"]
    assert second["facts"]["context"]["constraints"] == {
        "family": "steel",
        "is_active": True,
    }
    observation, judgment = control(second)
    assert score(second, observation, judgment)["primary_failure"] == "passed"
    observation["answer"] = "Which horizon should I use?"
    digest = sha256(observation["answer"].encode()).hexdigest()
    observation["extraction"]["answer_sha256"] = digest
    judgment["answer_sha256"] = digest
    judgment["checks"]["no_repeated_clarification"].update(
        passed=False,
        repetitions=[
            {
                "answer_quote": observation["answer"],
                "resolved_by": {
                    "source": "current_question",
                    "index": None,
                    "quote": "already specified metric and horizon",
                },
            }
        ],
    )
    bind_control_trace(observation, judgment)
    assert score(second, observation, judgment)["dimensions"]["Q"]["status"] == "fail"


def test_k13_rejects_inappropriate_clarification_and_deletion_conflation() -> None:
    first = expected("E101")
    observation, judgment = control(first["response_alternatives"]["clarification"])
    judgment["checks"]["scope_response_suitable"]["passed"] = False
    assert score(first, observation, judgment)["dimensions"]["Q"]["status"] == "fail"
    second = expected("E101", 1)
    observation, judgment = control(second)
    observation["facts"]["constraints"] = {"family": "steel", "active": True}
    judgment["checks"]["model_specific_deletion_semantics"]["passed"] = False
    result = score(second, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "fail"
    assert result["dimensions"]["A"]["status"] == "fail"


def test_k13_missing_followup_is_not_a_completed_case() -> None:
    first = expected("E101")
    observation, judgment = control(first["response_alternatives"]["clarification"])
    result = api("reporting").case_result(
        [score(first, observation, judgment)], expected_turns=len(case("E101")["turns"])
    )
    assert result["status"] != "passed"
    assert result["missing_turns"] == [1]


def test_k13_script_queries_current_steel_without_business_active_filter() -> None:
    scripted = api("scripted")
    import asyncio
    from general_manager.chat.providers.base import Message, ToolCallEvent

    script = scripted.make_script(case("E101"), None)
    provider = script({"role": "executor"})
    message = Message("user", 'REFERENCE_DATA={"task_evidence": []}')

    async def events() -> list[Any]:
        return [event async for event in provider.complete([message], [])]

    calls = [
        event for event in asyncio.run(events()) if isinstance(event, ToolCallEvent)
    ]
    assert calls[0].name == "query"
    assert calls[0].args["manager"] == "Material"
    assert calls[0].args["filters"] == {"family": "steel"}


@pytest.mark.parametrize("invalid_extraction", [None, {"origin": "model"}])
def test_k13_unvalidated_mode_cannot_waive_result_completeness(
    invalid_extraction: Any,
) -> None:
    first = expected("E101")
    observation, judgment = control(first["response_alternatives"]["clarification"])
    if invalid_extraction is None:
        observation.pop("extraction")
    else:
        observation["extraction"].update(invalid_extraction)
    result = score(first, observation, judgment)
    assert result["primary_failure"] == "judge_failure"
    assert result["dimensions"]["R"]["status"] == "unscored"
    assert result["passed"] is False


def test_k13_followup_cannot_select_optional_first_turn_branch() -> None:
    followup = expected("E101", 1)
    assert "response_alternatives" not in followup
    observation, judgment = control(followup)
    observation["facts"]["response_mode"] = "clarification"
    result = score(followup, observation, judgment)
    assert result["primary_failure"] == "model_task_failure"
    assert result["dimensions"]["A"]["status"] == "fail"


def identity_control(
    expectation: dict[str, Any],
    manager: str,
    rows: list[dict[str, Any]],
    identifiers: list[Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Actual-row-shaped evidence; never an inferred model answer or live result."""
    observation, judgment = control(expectation)
    fields = sorted({name for row in rows for name in row})
    observation["facts"]["result_ids"] = identifiers
    observation["trace"]["observation_complete"] = True
    observation["trace"]["tool_calls"] = [
        {
            "id": f"call-{manager}",
            "name": "query",
            "root_manager": manager,
            "arguments": {"manager": manager, "fields": fields},
            "managers": [manager],
            "manager_fields": {manager: fields},
            "error": False,
            "output": {"data": rows, "total_count": len(rows), "has_more": False},
        }
    ]
    trace_digest = api("scoring").evidence_trace_digest(observation["trace"])
    observation["extraction"]["evidence_trace_sha256"] = trace_digest
    judgment["evidence_trace_sha256"] = trace_digest
    return observation, judgment


def test_database_ids_resolve_from_returned_names_without_changing_gold_or_raw_facts() -> (
    None
):
    contract = expected("E106")
    observation, judgment = identity_control(
        contract,
        "Customer",
        [{"id": 1, "name": "XYZ Industrie"}, {"id": 2, "name": "Boreal SA"}],
        [1, 2],
    )
    original = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "pass"
    assert observation == original
    assert contract["facts"]["result_ids"] == ["C01", "C02"]
    assert result["identity_normalization"]["raw_facts"]["result_ids"] == [1, 2]
    assert result["identity_normalization"]["normalized_facts"]["result_ids"] == [
        "C01",
        "C02",
    ]
    mappings = result["identity_normalization"]["mappings"]
    assert {row["canonical_code"] for row in mappings} == {"C01", "C02"}
    assert all(
        row["manager"] == "Customer" and row["evidence_ids"] == ["ev-Customer"]
        for row in mappings
    )


def test_database_id_normalization_uses_values_not_row_order_and_accepts_returned_codes() -> (
    None
):
    contract = expected("E106")
    observation, judgment = identity_control(
        contract,
        "Customer",
        [{"id": 91, "name": "Boreal SA"}, {"id": 407, "code": "C01"}],
        ["407", 91],
    )
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "pass"
    assert result["identity_normalization"]["normalized_facts"]["result_ids"] == [
        "C01",
        "C02",
    ]


@pytest.mark.parametrize(
    "identifiers", [[1], [1, 3], [1, 2, 3], [1, 1], [1, "C01"], [True, 2], [None, 2]]
)
def test_id_equivalence_keeps_missing_wrong_extra_duplicate_and_non_ids_wrong(
    identifiers: list[Any],
) -> None:
    contract = expected("E106")
    observation, judgment = identity_control(
        contract,
        "Customer",
        [{"id": 1, "name": "XYZ Industrie"}, {"id": 2, "name": "Boreal SA"}],
        identifiers,
    )
    observation["trace"]["tool_calls"][0]["output"]["data"].append(
        {"id": 3, "name": "Nova"}
    )
    observation["trace"]["tool_calls"][0]["output"]["total_count"] = 3
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["dimensions"]["R"]["status"] == "fail"


def test_id_equivalence_never_maps_omitted_page_rows_or_unlinked_rows() -> None:
    contract = expected("E106")
    observation, judgment = identity_control(
        contract, "Customer", [{"id": 1, "name": "XYZ Industrie"}], [1, 2]
    )
    observation["trace"]["tool_calls"][0]["output"].update(total_count=2, has_more=True)
    assert (
        score(contract, observation, judgment)["dimensions"]["R"]["status"]
        == "unscored"
    )
    observation["trace"]["tool_calls"][0]["output"]["data"].append(
        {"id": 2, "name": "Boreal SA"}
    )
    observation["trace"]["evidence"] = []
    observation["extraction"]["evidence_ids"] = []
    assert (
        score(contract, observation, judgment)["dimensions"]["R"]["status"]
        == "unscored"
    )


@pytest.mark.parametrize(
    "rows",
    [
        [
            {"id": 1, "code": "C02", "name": "XYZ Industrie"},
            {"id": 2, "name": "Boreal SA"},
        ],
        [
            {"id": 1, "code": "UNKNOWN", "name": "XYZ Industrie"},
            {"id": 2, "name": "Boreal SA"},
        ],
        [
            {"id": 1, "name": "XYZ Industrie"},
            {"id": 1, "name": "Boreal SA"},
            {"id": 2, "name": "Boreal SA"},
        ],
        [
            {"id": 1, "name": "XYZ Industrie"},
            {"id": 7, "name": "XYZ Industrie"},
            {"id": 2, "name": "Boreal SA"},
        ],
        [{"id": True, "name": "XYZ Industrie"}, {"id": 2, "name": "Boreal SA"}],
        [{"id": 1, "name": "XYZ Industrie"}, {"id": 2, "name": "boreal sa"}],
    ],
)
def test_conflicting_or_unproven_returned_identity_cannot_gain_credit(
    rows: list[dict[str, Any]],
) -> None:
    contract = expected("E106")
    observation, judgment = identity_control(contract, "Customer", rows, [1, 2])
    assert (
        score(contract, observation, judgment)["dimensions"]["R"]["status"]
        == "unscored"
    )


def test_name_uniqueness_is_checked_against_all_source_rows_not_only_gold_or_result() -> (
    None
):
    seed = api("seeds").build_seed()
    seed["customer"].append(
        {"code": "C99", "name": "XYZ Industrie", "aliases": [], "active": False}
    )
    contract = api("oracle").expected_turn(case("E106"), 0, seed)
    assert contract["facts"]["result_ids"] == ["C01", "C02"]
    observation, judgment = identity_control(
        contract,
        "Customer",
        [{"id": 1, "name": "XYZ Industrie"}, {"id": 2, "name": "Boreal SA"}],
        [1, 2],
    )
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "unscored"
    assert any(
        row["reason"] == "ambiguous_source_name"
        for row in result["identity_normalization"]["unresolved"]
    )
    observation["trace"]["tool_calls"][0]["output"]["data"][0]["code"] = "C01"
    observation["trace"]["tool_calls"][0]["arguments"]["fields"].append("code")
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["dimensions"]["R"]["status"] == "pass"


def test_entity_ids_are_manager_scoped_and_nested_rows_are_not_root_rows() -> None:
    contract = expected("E106")
    observation, judgment = identity_control(
        contract,
        "Customer",
        [{"id": 1, "name": "XYZ Industrie"}, {"id": 2, "name": "Boreal SA"}],
        [1, 2],
    )
    call = observation["trace"]["tool_calls"][0]
    call["root_manager"] = "Project"
    call["arguments"]["manager"] = "Project"
    call["managers"].append("Project")
    call["manager_fields"]["Project"] = ["id", "name"]
    assert (
        score(contract, observation, judgment)["dimensions"]["R"]["status"]
        == "unscored"
    )
    call["root_manager"] = "Customer"
    call["arguments"]["manager"] = "Customer"
    call["output"]["data"] = [
        {
            "id": 8,
            "name": "Nova",
            "children": [
                {"id": 1, "name": "XYZ Industrie"},
                {"id": 2, "name": "Boreal SA"},
            ],
        }
    ]
    assert (
        score(contract, observation, judgment)["dimensions"]["R"]["status"]
        == "unscored"
    )


@pytest.mark.parametrize(
    ("identifier", "turn", "manager", "rows", "ids"),
    [
        (
            "E101",
            1,
            "Material",
            [
                {"id": 12, "name": "Steel"},
                {"id": 14, "name": "StainlessSteel"},
                {"id": 15, "name": "LegacySteel"},
            ],
            [12, 14, 15],
        ),
        (
            "E111",
            0,
            "Project",
            [
                {"id": 21, "name": "Aurora"},
                {"id": 23, "name": "CopperPeak"},
                {"id": 25, "name": "Delta"},
            ],
            [21, 23, 25],
        ),
    ],
)
def test_equivalence_covers_explicit_material_and_project_entity_fields(
    identifier: str, turn: int, manager: str, rows: list[dict[str, Any]], ids: list[int]
) -> None:
    contract = expected(identifier, turn)
    # Use complete source identities, without relying on assumed fixture names.
    names = {
        row["code"]: row["name"] for row in api("seeds").build_seed()[manager.lower()]
    }
    for row, code in zip(rows, contract["facts"]["result_ids"], strict=True):
        row["name"] = names[code]
    observation, judgment = identity_control(contract, manager, rows, ids)
    if "project_names" in observation["facts"]:
        observation["facts"]["project_names"] = {
            str(row["id"]): row["name"] for row in rows
        }
    assert score(contract, observation, judgment)["dimensions"]["R"]["status"] == "pass"


def test_identity_normalization_requires_validated_extraction_with_internal_grounding() -> (
    None
):
    contract = expected("E106")
    observation, judgment = identity_control(
        contract,
        "Customer",
        [{"id": 1, "name": "XYZ Industrie"}, {"id": 2, "name": "Boreal SA"}],
        [1, 2],
    )
    observation["citations"] = []
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "pass"
    assert result["dimensions"]["A"]["status"] == "pass"
    assert result["primary_failure"] == "passed"
    observation["extraction"]["origin"] = "model"
    result = score(contract, observation, judgment)
    assert result["primary_failure"] == "judge_failure"
    assert result["dimensions"]["R"]["status"] == "unscored"
    assert result["identity_normalization"]["mappings"] == []


def test_identity_reference_is_complete_typed_and_blind_to_judge_packet() -> None:
    contract = expected("E106")
    reference = contract["entity_identity_reference"]
    assert reference["fields"]["result_ids"] == {"manager": "Customer", "shape": "list"}
    assert {row["code"] for row in reference["managers"]["Customer"]} == {
        "C01",
        "C02",
        "C03",
    }
    observation, _ = identity_control(
        contract,
        "Customer",
        [{"id": 1, "name": "XYZ Industrie"}, {"id": 2, "name": "Boreal SA"}],
        [1, 2],
    )
    packet = api("scoring").make_judge_packet(contract, observation)
    assert "entity_identity_reference" not in packet
    assert '"C01"' not in json.dumps(packet)
    assert '"C03"' not in json.dumps(packet)


def test_entity_key_alias_collision_is_not_silently_deduplicated() -> None:
    contract = expected("E111")
    observation, judgment = identity_control(
        contract, "Project", [{"id": 1, "code": "P01"}], ["P01", "P03", "P05"]
    )
    observation["facts"]["project_names"] = {
        "1": "Aurora",
        "P01": "Aurora",
        "P03": "other",
    }
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "fail"
    assert any(
        row["reason"] == "duplicate_entity_keys"
        for row in result["identity_normalization"]["unresolved"]
    )


def test_identity_metadata_does_not_change_blind_fact_schema() -> None:
    contract = expected("E106")
    without_identity = deepcopy(contract)
    without_identity.pop("entity_identity_reference")
    assert api("adjudication").fact_schemas(contract) == api(
        "adjudication"
    ).fact_schemas(without_identity)
    assert '"C01"' not in json.dumps(api("adjudication").fact_schemas(contract))


def test_unknown_db_id_is_unscored_while_resolved_wrong_customer_fails() -> None:
    contract = expected("E106")
    observation, judgment = identity_control(
        contract,
        "Customer",
        [{"id": 1, "name": "XYZ Industrie"}, {"id": 2, "name": "Boreal SA"}],
        [1, 99],
    )
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "unscored"
    assert result["primary_failure"] == "judge_failure"
    assert not result["passed"]
    observation["trace"]["tool_calls"][0]["output"]["data"].append(
        {"id": 99, "name": "Nova"}
    )
    observation["trace"]["tool_calls"][0]["output"]["total_count"] = 3
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "fail"
    assert result["primary_failure"] == "model_task_failure"


def test_known_duplicate_aliases_fail_even_with_another_unresolved_map_key() -> None:
    contract = expected("E111")
    observation, judgment = identity_control(
        contract, "Project", [{"id": 1, "code": "P01"}], ["P01", "P03", "P05"]
    )
    observation["facts"]["project_names"] = {
        "1": "Aurora",
        "P01": "Aurora",
        "unknown-id": "Delta",
    }
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "fail"


@pytest.mark.parametrize(
    ("known_id", "expected_status"), [(2, "unscored"), (3, "fail")]
)
def test_unresolved_customer_id_preserves_independent_known_identity_findings(
    known_id: int, expected_status: str
) -> None:
    contract = expected("E106")
    observation, judgment = identity_control(
        contract,
        "Customer",
        [
            {"id": 1, "name": "XYZ Industrie"},
            {"id": 2, "name": "Boreal SA"},
            {"id": 3, "name": "Nova"},
        ],
        [99, known_id],
    )
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == expected_status
    assert result["primary_failure"] == "judge_failure"
    assert ("model_task_failure" in result["secondary_flags"]) is (
        expected_status == "fail"
    )
    assert any(
        row["observed"] == 99 and row["status"] == "unscored"
        for row in result["identity_normalization"]["unresolved"]
    )
    if expected_status == "fail":
        checks = result["dimensions"]["R"]["checks"]
        assert any(
            proof["observed"] == "C03"
            for check in checks
            for proof in check.get("identity_contradictions", [])
        )


@pytest.mark.parametrize(
    ("known_order", "expected_status"),
    [([4, 1, 5, 6], "unscored"), ([1, 4, 5, 6], "fail")],
)
def test_unresolved_rank_id_preserves_independent_known_order_findings(
    known_order: list[int], expected_status: str
) -> None:
    contract = expected("E009")
    observation, judgment = control(contract)
    rows = [{"id": number, "code": f"P{number:02d}"} for number in range(1, 7)]
    root, _ = identity_control(contract, "Project", rows, [])
    observation["trace"]["tool_calls"] = [
        row for row in observation["trace"]["tool_calls"] if row["id"] != "call-Project"
    ] + root["trace"]["tool_calls"]
    observation["facts"]["ranked_ids"] = [99, *known_order]
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["dimensions"]["C"]["status"] == expected_status
    assert result["primary_failure"] == "judge_failure"
    assert ("model_task_failure" in result["secondary_flags"]) is (
        expected_status == "fail"
    )


@pytest.mark.parametrize(
    ("known_key", "wrong_value", "expected_status"),
    [(21, False, "unscored"), (22, False, "fail"), (21, True, "fail")],
)
def test_unresolved_map_key_preserves_independent_known_key_and_value_findings(
    known_key: int, wrong_value: bool, expected_status: str
) -> None:
    contract = expected("E111")
    names = {row["code"]: row["name"] for row in api("seeds").build_seed()["project"]}
    observation, judgment = identity_control(
        contract,
        "Project",
        [{"id": number + 20, "code": f"P{number:02d}"} for number in range(1, 6)],
        ["P01", "P03", "P05"],
    )
    observation["facts"]["project_names"] = {
        "99": "Unresolved label",
        str(known_key): "Wrong label"
        if wrong_value
        else names[f"P{known_key - 20:02d}"],
        "23": names["P03"],
    }
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == expected_status
    assert result["primary_failure"] == "judge_failure"
    assert ("model_task_failure" in result["secondary_flags"]) is (
        expected_status == "fail"
    )


@pytest.mark.parametrize(
    "identifier", ["E101", "E106", "E111", "E116", "E119", "E120", "E121"]
)
def test_simple_answers_use_internal_grounding_without_visible_citations(
    identifier: str,
) -> None:
    contract = expected(identifier)
    observation, judgment = control(contract)
    observation["fact_support"] = {
        field: {
            "status": "present",
            "answer_quotes": [observation["answer"]],
            "evidence_ids": observation["extraction"]["evidence_ids"],
        }
        for field in observation["facts"]
    }
    observation["citations"] = []
    result = score(contract, observation, judgment)
    assert result["passed"]
    assert contract["citation_policy"] == "internal_grounding"
    assert result["visible_citations"]["required"] is False
    assert result["visible_citations"]["status"] == "absent"


@pytest.mark.parametrize(
    "damage",
    [
        "missing_support",
        "empty_refs",
        "invented_ref",
        "duplicate_ref",
        "invented_quote",
        "missing_call",
        "failed_call",
        "wrong_ids",
        "wrong_filter",
        "contradiction",
        "answer_only_judgment",
        "stale_extraction",
    ],
)
def test_internal_grounding_does_not_accept_unsupported_simple_answers(
    damage: str,
) -> None:
    contract = expected("E106")
    observation, judgment = control(contract)
    observation["fact_support"] = {
        field: {
            "status": "present",
            "answer_quotes": [observation["answer"]],
            "evidence_ids": list(observation["extraction"]["evidence_ids"]),
        }
        for field in observation["facts"]
    }
    observation["citations"] = []
    support = observation["fact_support"]["result_ids"]
    if damage == "missing_support":
        observation.pop("fact_support")
    elif damage == "empty_refs":
        support["evidence_ids"] = []
    elif damage == "invented_ref":
        support["evidence_ids"] = ["made-up"]
    elif damage == "duplicate_ref":
        support["evidence_ids"] *= 2
    elif damage == "invented_quote":
        support["answer_quotes"] = ["fabricated quotation"]
    elif damage == "missing_call":
        observation["trace"]["tool_calls"] = []
    elif damage == "failed_call":
        observation["trace"]["tool_calls"][0]["error"] = True
    elif damage == "wrong_ids":
        observation["facts"]["result_ids"] = ["C01", "C03"]
    elif damage == "wrong_filter":
        observation["facts"]["constraints"] = {"active": False}
    elif damage == "contradiction":
        judgment["checks"]["answer_supported"]["passed"] = False
    elif damage == "answer_only_judgment":
        judgment["checks"]["answer_supported"]["evidence_ids"] = ["answer"]
    elif damage == "stale_extraction":
        observation["extraction"]["answer_sha256"] = "stale"
    assert not score(contract, observation, judgment)["passed"]


def test_explicit_source_requests_and_legacy_references_keep_citation_requirement() -> (
    None
):
    explicit = next(
        row for row in api("catalog").load_catalog() if row["contract"] == "provenance"
    )
    contract = api("oracle").expected_turn(explicit, 0)
    observation, judgment = control(contract)
    observation["citations"] = []
    assert score(contract, observation, judgment)["dimensions"]["A"]["status"] == "fail"
    legacy = expected("E106")
    legacy.pop("citation_policy", None)
    legacy["schema_version"] = "1.3"
    observation, judgment = control(legacy)
    observation["citations"] = []
    assert score(legacy, observation, judgment)["dimensions"]["A"]["status"] == "fail"


@pytest.mark.parametrize(
    "damage", ["wrong_number", "missing_source", "failed_semantics"]
)
def test_internal_grounding_keeps_numeric_and_multi_source_checks(damage: str) -> None:
    contract = expected("E116")
    observation, judgment = control(contract)
    observation["citations"] = []
    if damage == "wrong_number":
        observation["facts"]["total"] = "9999.00"
    elif damage == "missing_source":
        for item in observation["fact_support"].values():
            item["evidence_ids"] = item["evidence_ids"][:1]
        judgment["checks"]["answer_supported"]["evidence_ids"] = ["answer"]
    else:
        judgment["checks"]["answer_supported"]["passed"] = False
    assert not score(contract, observation, judgment)["passed"]


def test_reference_15_preserves_gold_and_catalog_and_versions_policy() -> None:
    catalog = api("catalog")
    metadata = json.loads(
        catalog.CATALOG_PATH.with_name("catalog_corrections.json").read_text()
    )
    assert catalog.REFERENCE_VERSION == metadata["reference_version"] == "1.10"
    assert metadata["history"][-1]["reference_version"] == "1.4"
    assert metadata["evaluation_policy_changes"][-1]["case_ids"] == [
        f"E{n:03}" for n in range(1, 122)
    ]
    assert (
        catalog.catalog_sha256()
        == "d105613e4560b63e6bf69181d61ce395a28feb82f91b51b6afa6c30eed0b8c11"
    )
    assert expected("E106")["facts"]["result_ids"] == ["C01", "C02"]


@pytest.mark.parametrize(
    "identifier,turn,manager,path",
    [
        ("E008", 1, "Customer", ["context", "customer_ids"]),
        ("E020", 0, "Material", ["constraints", "all_materials"]),
    ],
)
def test_nested_list_identity_descriptors_normalize_each_element(
    identifier, turn, manager, path
):
    contract = expected(identifier, turn)
    descriptor = next(
        (
            x
            for x in contract["entity_identity_reference"].get("paths", [])
            if x["path"] == path
        ),
        None,
    )
    assert descriptor is not None and descriptor["shape"] == "list"
    contract["source_requirements"] = [
        {"role": "identity_fixture", "alternatives": [[manager]], "required_fields": {}}
    ]
    canonical = "C01" if manager == "Customer" else "M01"
    observation, judgment = identity_control(
        contract, manager, [{"id": 1, "code": canonical}], []
    )
    parent = observation["facts"]
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = [1]
    result = score(contract, observation, judgment)
    parent = result["identity_normalization"]["normalized_facts"]
    for part in path:
        parent = parent[part]
    assert parent == [canonical]
