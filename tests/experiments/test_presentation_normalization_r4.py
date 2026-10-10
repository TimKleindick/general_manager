"""Representation changes require complete, selected evidence and exact facts."""

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from experiments.gm_eval.scoring import score_turn
from tests.experiments.test_gm_eval_scoring import (
    bind_control_trace,
    case,
    control,
    expected,
    score,
)


def contract(facts, sources, reference=None, semantic=()):
    return {
        "case_id": "generic-representation",
        "turn_index": 0,
        "facts": deepcopy(facts),
        "dimensions": {
            dimension: {
                "applicable": dimension in {"D", "I", "R", "A"},
                "checks": [
                    {"field": field, "expected": value, "comparison": "exact"}
                    for field, value in facts.items()
                ]
                if dimension == "R"
                else [],
            }
            for dimension in "DIRCQA"
        },
        "source_requirements": [
            {"role": manager, "alternatives": [[manager]]} for manager in sources
        ],
        "manager_interfaces": {manager: "Database" for manager in sources},
        "citation_policy": "internal_grounding",
        "entity_identity_reference": reference or {},
        "semantic_checks": [
            {"id": check, "dimension": "A", "instruction": "Review controlled data"}
            for check in ["answer_supported", *semantic]
        ],
    }


def query(manager, rows, fields=None):
    selected = fields if fields is not None else list(rows[0])
    return {
        "id": f"query-{manager}",
        "name": "query",
        "root_manager": manager,
        "managers": [manager],
        "manager_fields": {
            manager: [field for field in selected if isinstance(field, str)]
        },
        "arguments": {"manager": manager, "fields": selected},
        "output": {
            "data": deepcopy(rows),
            "total_count": len(rows),
            "has_more": False,
            "complete": True,
        },
        "error": False,
    }


def relation_schema(manager, relation, target, path, types):
    return {
        "id": f"schema-{manager}",
        "name": "get_manager_schema",
        "arguments": {"manager": manager},
        "output": {
            "manager": manager,
            "contract_version": 2,
            "type": f"{manager}Type",
            "relations": [{"name": relation, "target": target, "path": path}],
            "types": types,
        },
        "error": False,
    }


def observation(expectation, calls):
    observed, judgment = control(expectation)
    records = [
        {
            "id": f"evidence-{call['id']}-{manager}",
            "manager": manager,
            "origin": "tool",
            "call_id": call["id"],
        }
        for call in calls
        if call["name"] == "query"
        for manager in call["managers"]
    ]
    observed["trace"]["tool_calls"] = calls
    observed["trace"]["evidence"] = records
    refs = [record["id"] for record in records]
    observed["extraction"]["evidence_ids"] = refs
    observed["citations"] = refs
    for item in observed["fact_support"].values():
        item["evidence_ids"] = refs
    for item in judgment["checks"].values():
        item["evidence_ids"] = ["answer", *refs]
    bind_control_trace(observed, judgment)
    return observed, judgment


def exclusion_control():
    facts = {
        "result_ids": ["PROJ-Q"],
        "material_ids": ["MAT-B"],
        "constraints": {"all_materials": ["MAT-B"]},
    }
    reference = {
        "fields": {
            "result_ids": {"manager": "Project", "shape": "list"},
            "material_ids": {"manager": "Material", "shape": "list"},
        },
        "managers": {
            "Project": [{"code": "PROJ-Q", "name": "Project Q"}],
            "Material": [
                {"code": "MAT-B", "name": "Brass"},
                {"code": "MAT-C", "name": "Copper"},
            ],
        },
        "paths": [
            {
                "path": ["constraints", "all_materials"],
                "manager": "Material",
                "shape": "list",
            }
        ],
    }
    expectation = contract(facts, ["Project"], reference)
    schema = relation_schema(
        "Project",
        "materialsList",
        "Material",
        ["materialsList", "items"],
        {
            "ProjectType": {
                "kind": "object",
                "fields": {"materialsList": {"type": "MaterialPage"}},
            },
            "MaterialPage": {
                "kind": "object",
                "fields": {"items": {"type": "[MaterialType!]!"}},
            },
            "MaterialType": {"kind": "reference", "manager": "Material"},
        },
    )
    materials = query(
        "Material",
        [
            {"id": 41, "code": "MAT-B", "name": "Brass"},
            {"id": 88, "code": "MAT-C", "name": "Copper"},
        ],
    )
    projects = query(
        "Project",
        [
            {
                "id": 902,
                "code": "PROJ-Q",
                "name": "Project Q",
                "materialsList": {
                    "items": [{"id": 41, "code": "MAT-B", "name": "Brass"}],
                    "pageInfo": {"totalCount": 1},
                },
            }
        ],
        [
            "id",
            "code",
            "name",
            {
                "materialsList": [
                    {"items": ["id", "code", "name"]},
                    {"pageInfo": ["totalCount"]},
                ]
            },
        ],
    )
    projects["managers"].append("Material")
    projects["manager_fields"]["Material"] = ["id", "code", "name"]
    observed, judgment = observation(expectation, [schema, materials, projects])
    observed["facts"]["constraints"]["none_materials"] = [88]
    return expectation, observed, judgment


def cost_control(alias="actual_net_costs", project_scoped=False):
    facts = {
        "metric": "actual_net_project_cost",
        "unit": "EUR",
        "source_kinds": ["actual"],
        "period": {"start": "2040-01-01", "end_exclusive": "2041-01-01"},
        "values": {"WBS-Q": "157.25"},
        "total": "157.25",
    }
    reference = {
        "managers": {"WbsElement": [{"code": "WBS-Q", "name": "Custom work"}]},
        "fields": {"values": {"manager": "WbsElement", "shape": "mapping_keys"}},
    }
    if project_scoped:
        facts["constraints"] = {"kind": "actual", "project": "PROJ-S"}
        reference["managers"]["Project"] = [
            {"code": "PROJ-S", "name": "Scoped project"}
        ]
    expectation = contract(facts, ["ProjectCost"], reference)
    schema = relation_schema(
        "ProjectCost",
        "wbs",
        "WbsElement",
        ["wbs"],
        {
            "ProjectCostType": {
                "kind": "object",
                "fields": {"wbs": {"type": "WbsType"}},
            },
            "WbsType": {"kind": "reference", "manager": "WbsElement"},
        },
    )
    costs = query(
        "ProjectCost",
        [
            {
                "id": 411,
                "code": "COST-Q",
                "kind": "actual",
                "currency": "EUR",
                "costDate": "2040-07-04",
                "netAmount": 157.25,
                "wbsId": 731,
                "wbs": {"id": 731, "code": "WBS-Q", "name": "Custom work"},
            }
        ],
        [
            "id",
            "code",
            "kind",
            "currency",
            "costDate",
            "netAmount",
            "wbsId",
            {"wbs": ["id", "code", "name"]},
        ],
    )
    costs["managers"].append("WbsElement")
    costs["manager_fields"]["WbsElement"] = ["id", "code", "name"]
    schema["output"]["root_fields"] = {
        "projectCostList": {
            "type": "ProjectCostPage",
            "arguments": {
                "filter": {"type": "ProjectCostFilter"},
                "exclude": {"type": "ProjectCostFilter"},
                "page": {"type": "Int"},
                "pageSize": {"type": "Int"},
                "orderBy": {"type": "[CostOrdering!]"},
            },
        }
    }
    schema["output"]["types"]["ProjectCostFilter"] = {
        "kind": "input",
        "fields": {
            "costDate_Gte": {"type": "Date"},
            "costDate_Lt": {"type": "Date"},
            "currency_Exact": {"type": "String"},
            "kind_Exact": {"type": "String"},
            "projectId_Exact": {"type": "ID"},
            "id_Exact": {"type": "ID"},
            "wbsId_Exact": {"type": "ID"},
            "netAmount_Gt": {"type": "Float"},
        },
    }
    costs["arguments"]["arguments"] = {
        "filter": {
            "costDate_Gte": "2040-01-01",
            "costDate_Lt": "2041-01-01",
            "kind_Exact": "actual",
            "currency_Exact": "EUR",
        }
    }
    calls = [schema, costs]
    if project_scoped:
        costs["arguments"]["fields"].append("projectId")
        costs["manager_fields"]["ProjectCost"].append("projectId")
        costs["output"]["data"][0]["projectId"] = 827
        costs["arguments"]["arguments"]["filter"]["projectId_Exact"] = 827
        calls.insert(
            1,
            query("Project", [{"id": 827, "code": "PROJ-S", "name": "Scoped project"}]),
        )
    observed, judgment = observation(expectation, calls)
    observed["facts"]["metric"] = alias
    return expectation, observed, judgment


def relation_argument_control(argument="filter", default=None, explicit=None):
    expectation, observed, judgment = exclusion_control()
    schema = observed["trace"]["tool_calls"][0]["output"]
    schema["types"]["MaterialFilter"] = {
        "kind": "input",
        "fields": {"id_Exact": {"type": "ID"}},
    }
    definition = {"type": "MaterialFilter", "default": default}
    schema["types"]["ProjectType"]["fields"]["materialsList"]["arguments"] = {
        argument: definition,
        "pageSize": {"type": "Int", "default": 200},
        "orderBy": {
            "type": "[MaterialOrdering!]",
            "default": [{"field": "code", "direction": "ASC"}],
        },
    }
    if explicit is not None:
        observed["trace"]["tool_calls"][-1]["arguments"]["fields"][-1] = {
            "field": "materialsList",
            "arguments": explicit,
            "fields": [{"items": ["id", "code", "name"]}, {"pageInfo": ["totalCount"]}],
        }
    bind_control_trace(observed, judgment)
    return expectation, observed, judgment


def forecast_control():
    forecast = {
        "method": "OLS",
        "training_years": [2020, 2021, 2022],
        "actuals": {"2020": 10, "2021": 12, "2022": 14},
        "unit": "pieces",
        "scenario_kind": "sensitivity_not_confidence",
        "scenario_fraction": "0.20",
        "missing_years": [],
        "status": "available",
        "slope": 2,
        "intercept": 10,
        "values": {"2024": 18, "2025": 20},
        "scenario": {"2024": [14.4, 21.6], "2025": [16, 24]},
    }
    expectation = contract(
        {"customer_ids": ["BUYER-Q"], "forecast": forecast},
        ["CustomerOutlook"],
        {
            "managers": {"Customer": [{"code": "BUYER-Q", "name": "Buyer Q"}]},
            "fields": {"customer_ids": {"manager": "Customer", "shape": "list"}},
        },
        ["scenario_not_confidence"],
    )
    schema = relation_schema(
        "CustomerOutlook",
        "customer",
        "Customer",
        ["customer"],
        {
            "CustomerOutlookType": {
                "kind": "object",
                "fields": {"customer": {"type": "CustomerType"}},
            },
            "CustomerType": {"kind": "reference", "manager": "Customer"},
        },
    )
    outlook = query(
        "CustomerOutlook",
        [
            {
                "customer": {"id": 619, "code": "BUYER-Q", "name": "Buyer Q"},
                "year": 2024,
                "forecast": 18,
                "historyComplete": True,
                "scenarioLow": 14.4,
                "scenarioHigh": 21.6,
            },
            {
                "customer": {"id": 619, "code": "BUYER-Q", "name": "Buyer Q"},
                "year": 2025,
                "forecast": 20,
                "historyComplete": True,
                "scenarioLow": 16,
                "scenarioHigh": 24,
            },
        ],
        [
            {"customer": ["id", "code", "name"]},
            "year",
            "forecast",
            "historyComplete",
            "scenarioLow",
            "scenarioHigh",
        ],
    )
    outlook["managers"].append("Customer")
    outlook["manager_fields"]["Customer"] = ["id", "code", "name"]
    observed, judgment = observation(expectation, [schema, outlook])
    return expectation, observed, judgment


def shipment_forecast_control():
    expectation, _, _ = forecast_control()
    expectation["source_requirements"] = [
        {"role": "actual_history", "alternatives": [["Shipment"]]}
    ]
    expectation["manager_interfaces"] = {"Shipment": "Database"}
    customer = query("Customer", [{"id": 619, "code": "BUYER-Q", "name": "Buyer Q"}])
    shipments = query(
        "Shipment",
        [
            {
                "id": 101,
                "quantity": 10,
                "shippedAt": "2020-06-15",
                "source": "actual",
                "unit": "pieces",
            },
            {
                "id": 102,
                "quantity": 12,
                "shippedAt": "2021-06-15",
                "source": "actual",
                "unit": "pieces",
            },
            {
                "id": 103,
                "quantity": 14,
                "shippedAt": "2022-06-15",
                "source": "actual",
                "unit": "pieces",
            },
        ],
    )
    shipments["arguments"]["arguments"] = {
        "filter": {
            "project": {"customerId_Exact": 619},
            "shippedAt_Gte": "2020-01-01",
            "shippedAt_Lt": "2023-01-01",
        }
    }
    schema = {
        "id": "schema-Shipment",
        "name": "get_manager_schema",
        "arguments": {"manager": "Shipment"},
        "output": {
            "manager": "Shipment",
            "contract_version": 2,
            "type": "ShipmentType",
            "relations": [],
            "root_fields": {
                "shipmentList": {
                    "type": "ShipmentPage",
                    "arguments": {
                        "filter": {"type": "ShipmentFilter"},
                        "exclude": {"type": "ShipmentFilter"},
                        "page": {"type": "Int"},
                        "pageSize": {"type": "Int"},
                        "orderBy": {"type": "[ShipmentOrdering!]"},
                    },
                }
            },
            "types": {
                "ShipmentType": {
                    "kind": "object",
                    "fields": {
                        field: {"type": "String"}
                        for field in shipments["arguments"]["fields"]
                    },
                },
                "ShipmentFilter": {
                    "kind": "input",
                    "fields": {
                        "id_Exact": {"type": "Int"},
                        "project": {"type": "ProjectFilter"},
                        "shippedAt_Gte": {"type": "String"},
                        "shippedAt_Lt": {"type": "String"},
                    },
                },
                "ProjectFilter": {
                    "kind": "input",
                    "fields": {
                        "id_Exact": {"type": "Int"},
                        "customerId_Exact": {"type": "Int"},
                    },
                },
            },
        },
        "error": False,
    }
    observed, judgment = observation(expectation, [customer, schema, shipments])
    observed["facts"]["forecast"].update(
        status="complete", scenario_kind="sensitivity", total=38, nonnegative_clamp=True
    )
    return expectation, observed, judgment


@pytest.mark.parametrize("builder", [exclusion_control, cost_control])
def test_evidence_supported_representation_passes_without_changing_saved_facts(builder):
    expectation, observed, judgment = builder()
    before = deepcopy(observed)
    result = score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )
    assert result["passed"], result
    assert observed == before


@pytest.mark.parametrize("alias", ["actual_net_cost", "actual_net_costs"])
def test_actual_net_cost_label_aliases_require_the_actual_project_ledger(alias):
    expectation, observed, judgment = cost_control(alias)
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


def test_project_scoped_wbs_cost_alias_uses_project_identity_separately_from_wbs_group():
    expectation, observed, judgment = cost_control(project_scoped=True)
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize("project_id", [999, True, None])
def test_wbs_cost_alias_rejects_a_different_or_unproved_project_scope(project_id):
    expectation, observed, judgment = cost_control(project_scoped=True)
    observed["trace"]["tool_calls"][-1]["output"]["data"][0]["projectId"] = project_id
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


def test_complete_shipment_history_supports_the_same_forecast_presentations():
    expectation, observed, judgment = shipment_forecast_control()
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_customer",
        "extra_status_filter",
        "short_period",
        "missing_year",
        "wrong_quantity",
        "wrong_unit",
        "bool_quantity",
        "planned_source",
    ],
)
def test_shipment_history_must_keep_actual_customer_period_years_and_units(mutation):
    expectation, observed, judgment = shipment_forecast_control()
    call = observed["trace"]["tool_calls"][-1]
    filters = call["arguments"]["arguments"]["filter"]
    row = call["output"]["data"][0]
    if mutation == "wrong_customer":
        filters["project"]["customerId_Exact"] = 888
    elif mutation == "extra_status_filter":
        filters["project"]["status_Exact"] = "active"
    elif mutation == "short_period":
        filters["shippedAt_Lt"] = "2022-01-01"
    elif mutation == "missing_year":
        call["output"]["data"].pop()
        call["output"]["total_count"] = 2
    elif mutation == "wrong_quantity":
        row["quantity"] = 11
    elif mutation == "wrong_unit":
        row["unit"] = "kg"
    elif mutation == "bool_quantity":
        row["quantity"] = True
    else:
        row["source"] = "existing_plan"
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "change",
    [
        {"status": "complete"},
        {"scenario_kind": "sensitivity"},
        {"total": 38},
        {"nonnegative_clamp": True},
        {
            "status": "complete",
            "scenario_kind": "sensitivity",
            "total": 38,
            "nonnegative_clamp": True,
        },
    ],
)
def test_forecast_representation_is_checked_against_exact_source_rows(change):
    expectation, observed, judgment = forecast_control()
    observed["facts"]["forecast"].update(change)
    before = deepcopy(observed)
    result = score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )
    assert result["passed"], result
    assert observed == before


@pytest.mark.parametrize("builder", [exclusion_control, cost_control, forecast_control])
@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "failed",
        "partial",
        "wrong_manager",
        "unselected",
        "missing_support",
        "stale_binding",
        "semantic_failure",
    ],
)
def test_representation_never_repairs_missing_or_contradictory_evidence(
    builder, mutation
):
    expectation, observed, judgment = builder()
    if builder is forecast_control:
        observed["facts"]["forecast"].update(status="complete", total=38)
    field = (
        "forecast"
        if builder is forecast_control
        else "metric"
        if builder is cost_control
        else "constraints"
    )
    call = observed["trace"]["tool_calls"][-1]
    if mutation == "missing":
        observed["trace"]["tool_calls"].remove(call)
    elif mutation == "failed":
        call["error"] = True
    elif mutation == "partial":
        call["output"].update(complete=False, total_count=999, has_more=True)
    elif mutation == "wrong_manager":
        call["root_manager"] = "Unrelated"
    elif mutation == "unselected":
        call["arguments"]["fields"] = ["id"]
    elif mutation == "missing_support":
        observed["fact_support"][field]["evidence_ids"] = []
    elif mutation == "semantic_failure":
        judgment["checks"]["answer_supported"]["passed"] = False
    else:
        call["output"]["data"][0]["ignored_metadata"] = "changed"
    if mutation != "stale_binding":
        bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize("exclusions", [[41], [999], [True], [], [88, 88], ["UNKNOWN"]])
def test_extra_exclusions_must_resolve_and_be_absent_from_every_project(exclusions):
    expectation, observed, judgment = exclusion_control()
    observed["facts"]["constraints"]["none_materials"] = exclusions
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "change",
    [
        {"total": 37},
        {"nonnegative_clamp": False},
        {"nonnegative_clamp": 1},
        {"status": "unknown"},
        {"status": "insufficient_history"},
        {"scenario_kind": "confidence_interval"},
        {"scenario_kind": "unknown"},
        {"method": "CAGR"},
        {"scenario_fraction": 0.25},
        {"values": {"2024": 19, "2025": 20}},
        {"training_years": [2020, 2021, 2023]},
        {"actuals": {"2020": 10, "2021": 12, "2022": 0}},
        {"unit": "kg"},
        {"confidence_level": 0.95},
    ],
)
def test_forecast_canonicalization_rejects_wrong_or_unknown_claims(change):
    expectation, observed, judgment = forecast_control()
    observed["facts"]["forecast"].update(
        status="complete", **change
    ) if "status" not in change else observed["facts"]["forecast"].update(change)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "label",
    ["plan_net_costs", "actual_gross_cost", "actual_net_revenue", "cost", "unknown"],
)
def test_metric_normalization_rejects_unknown_or_different_cost_basis(label):
    expectation, observed, judgment = cost_control(label)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "plan"),
        ("currency", "USD"),
        ("costDate", "2039-07-04"),
        ("netAmount", 158.25),
        ("netAmount", True),
        ("wbsId", 999),
    ],
)
def test_cost_alias_cannot_grant_credit_to_a_contradictory_source_row(field, value):
    expectation, observed, judgment = cost_control()
    observed["trace"]["tool_calls"][-1]["output"]["data"][0][field] = value
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("year", 2026),
        ("forecast", 19),
        ("historyComplete", False),
        ("scenarioLow", 15),
        ("scenarioHigh", 22),
    ],
)
def test_forecast_alias_cannot_grant_credit_to_a_contradictory_source_row(field, value):
    expectation, observed, judgment = forecast_control()
    observed["facts"]["forecast"].update(status="complete", total=38)
    observed["trace"]["tool_calls"][-1]["output"]["data"][0][field] = value
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "builder,field,value",
    [
        (cost_control, "metric", {}),
        (
            exclusion_control,
            "constraints",
            {"all_materials": [{}], "none_materials": [88]},
        ),
        (forecast_control, "forecast", {"status": "complete"}),
    ],
)
def test_malformed_fact_shapes_fail_without_crashing_the_scorer(builder, field, value):
    expectation, observed, judgment = builder()
    observed["facts"][field] = value
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "field",
    [
        "actuals",
        "slope",
        "intercept",
        "training_years",
        "method",
        "missing_years",
        "scenario_fraction",
        "values",
        "scenario",
    ],
)
def test_forecast_projection_never_invents_missing_history_or_computation_facts(field):
    expectation, observed, judgment = forecast_control()
    observed["facts"]["forecast"].update(
        status="complete", total=38, nonnegative_clamp=True
    )
    observed["facts"]["forecast"].pop(field)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "mutation",
    [
        "filtered_material_relation",
        "partial_material_relation",
        "excluded_material_present",
        "material_identity_contradiction",
        "unknown_constraint",
    ],
)
def test_extra_exclusion_requires_unfiltered_complete_material_membership(mutation):
    expectation, observed, judgment = exclusion_control()
    call = observed["trace"]["tool_calls"][-1]
    row = call["output"]["data"][0]
    if mutation == "filtered_material_relation":
        call["arguments"]["fields"][-1] = {
            "field": "materialsList",
            "arguments": {"filter": {"id_Exact": 41}},
            "fields": [{"items": ["id", "code", "name"]}, {"pageInfo": ["totalCount"]}],
        }
    elif mutation == "partial_material_relation":
        row["materialsList"]["pageInfo"]["totalCount"] = 2
    elif mutation == "excluded_material_present":
        row["materialsList"]["items"].append(
            {"id": 88, "code": "MAT-C", "name": "Copper"}
        )
        row["materialsList"]["pageInfo"]["totalCount"] = 2
    elif mutation == "material_identity_contradiction":
        observed["trace"]["tool_calls"][1]["output"]["data"][1]["name"] = "Brass"
    else:
        observed["facts"]["constraints"]["unreviewed_exclusion"] = [88]
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize("builder", [cost_control, forecast_control])
def test_missing_unsupported_numeric_rows_do_not_become_evidence(builder):
    expectation, observed, judgment = builder()
    if builder is forecast_control:
        observed["facts"]["forecast"].update(status="complete", total=38)
    observed["trace"]["tool_calls"][-1]["output"]["data"] = []
    observed["trace"]["tool_calls"][-1]["output"].update(total_count=0)
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize("label", ["MAT-C", 88, "88"])
def test_extra_exclusion_accepts_only_equivalent_selected_identity_spellings(label):
    expectation, observed, judgment = exclusion_control()
    observed["facts"]["constraints"]["none_materials"] = [label]
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("total", 158.25),
        ("unit", "USD"),
        ("period", {"start": "2039-01-01", "end_exclusive": "2040-01-01"}),
        ("values", {"UNKNOWN": 157.25}),
    ],
)
def test_cost_metric_alias_does_not_change_period_unit_amount_or_result_identity(
    field, value
):
    expectation, observed, judgment = cost_control()
    observed["facts"][field] = value
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "field,value", [("forecast", True), ("scenarioLow", False), ("historyComplete", 1)]
)
def test_forecast_source_boolean_values_never_alias_numeric_or_completion_claims(
    field, value
):
    expectation, observed, judgment = forecast_control()
    observed["facts"]["forecast"].update(status="complete", total=38)
    observed["trace"]["tool_calls"][-1]["output"]["data"][0][field] = value
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize("identifier", ["E072", "E093", "E118", "E120"])
def test_unchanged_byte_bound_saved_adjudications_accept_equivalent_presentations(
    identifier,
):
    directory = os.environ.get("GM_REVIEW_EVIDENCE_DIR")
    if not directory:
        pytest.skip(
            "Exact saved controls require the packet's read-only evidence directory"
        )
    packet = json.loads((Path(directory) / f"{identifier}-report.json").read_text())
    adjudication = packet["judgments"][0]
    before = deepcopy(adjudication)
    result = score(
        expected(identifier),
        adjudication["observation"],
        adjudication["semantic_judgment"],
    )
    assert result["passed"], result
    assert adjudication == before
    assert packet["run"]["turns"][0]["user"] == case(identifier)["turns"][0]


@pytest.mark.parametrize("argument", ["filter", "exclude"])
def test_material_relation_defaults_cannot_hide_an_excluded_material(argument):
    expectation, observed, judgment = relation_argument_control(
        argument, {"id_Exact": 41}
    )
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize("argument", ["filter", "exclude"])
@pytest.mark.parametrize("default", [None, {}])
def test_empty_material_relation_defaults_and_native_ordering_preserve_full_membership(
    argument, default
):
    expectation, observed, judgment = relation_argument_control(argument, default)
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize("argument", ["filter", "exclude"])
@pytest.mark.parametrize("explicit", [None, {}])
def test_explicit_empty_relation_argument_overrides_a_narrowing_default(
    argument, explicit
):
    expectation, observed, judgment = relation_argument_control(
        argument, {"id_Exact": 41}, {argument: explicit}
    )
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"search": "Brass"},
        {"includeInactive": False},
        {"filter": {"id_Exact": 41}},
        {"exclude": {"id_Exact": 88}},
        {"filter": []},
        {"filter": False},
    ],
)
def test_unknown_or_narrowing_material_relation_arguments_do_not_prove_absence(
    arguments,
):
    expectation, observed, judgment = relation_argument_control(explicit=arguments)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


def test_empty_relation_argument_applies_loaded_input_field_defaults():
    expectation, observed, judgment = relation_argument_control(default={})
    observed["trace"]["tool_calls"][0]["output"]["types"]["MaterialFilter"]["fields"][
        "id_Exact"
    ]["default"] = 41
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "mutation",
    [
        "unavailable_filter_definition",
        "foreign_schema",
        "ambiguous_schema",
        "future_schema",
        "contradictory_default",
    ],
)
def test_absence_proof_requires_the_query_bound_default_definitions(mutation):
    expectation, observed, judgment = relation_argument_control(default={})
    schema = observed["trace"]["tool_calls"][0]
    if mutation == "unavailable_filter_definition":
        schema["output"]["types"].pop("MaterialFilter")
    elif mutation == "foreign_schema":
        schema["arguments"]["manager"] = "Other"
    elif mutation == "ambiguous_schema":
        duplicate = deepcopy(schema)
        duplicate["id"] = "schema-project-conflict"
        duplicate["output"]["types"]["ProjectType"]["fields"]["materialsList"][
            "arguments"
        ]["filter"]["default"] = {"id_Exact": 41}
        observed["trace"]["tool_calls"].insert(1, duplicate)
    elif mutation == "future_schema":
        observed["trace"]["tool_calls"].remove(schema)
        observed["trace"]["tool_calls"].append(schema)
    else:
        schema["output"]["types"]["ProjectType"]["fields"]["materialsList"][
            "arguments"
        ]["filter"]["default_graphql"] = "{id_Exact: 41}"
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize("field", ["values", "total"])
def test_cost_numeric_facts_require_selected_ledger_rows_in_their_own_support(field):
    expectation, observed, judgment = cost_control()
    observed["fact_support"][field]["evidence_ids"] = [
        record["id"]
        for record in observed["trace"]["evidence"]
        if record["manager"] == "WbsElement"
    ]
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "narrowing",
    [
        {"id_Exact": 411},
        {"wbsId_Exact": 731},
        {"netAmount_Gt": 150},
        {"projectId_Exact": 827},
        {"unknown_filter": "hidden"},
    ],
)
def test_complete_cost_query_does_not_certify_an_arbitrary_filtered_subset(narrowing):
    expectation, observed, judgment = cost_control()
    observed["trace"]["tool_calls"][-1]["arguments"]["arguments"]["filter"].update(
        narrowing
    )
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_start",
        "missing_end",
        "wrong_start",
        "missing_kind",
        "planned_kind",
        "exclude",
        "search",
        "default_exclude",
        "default_search",
        "default_id_input",
    ],
)
def test_cost_projection_requires_exact_full_period_and_effective_native_scope(
    mutation,
):
    expectation, observed, judgment = cost_control()
    schema = observed["trace"]["tool_calls"][0]["output"]
    native = observed["trace"]["tool_calls"][-1]["arguments"]["arguments"]
    args = schema["root_fields"]["projectCostList"]["arguments"]
    if mutation == "missing_start":
        native["filter"].pop("costDate_Gte")
    elif mutation == "missing_end":
        native["filter"].pop("costDate_Lt")
    elif mutation == "wrong_start":
        native["filter"]["costDate_Gte"] = "2040-07-01"
    elif mutation == "missing_kind":
        native["filter"].pop("kind_Exact")
    elif mutation == "planned_kind":
        native["filter"]["kind_Exact"] = "plan"
    elif mutation == "exclude":
        native["exclude"] = {"id_Exact": 999}
    elif mutation == "search":
        native["search"] = "COST-Q"
    elif mutation == "default_exclude":
        args["exclude"]["default"] = {"id_Exact": 999}
    elif mutation == "default_search":
        args["search"] = {"type": "String", "default": "COST-Q"}
    else:
        schema["types"]["ProjectCostFilter"]["fields"]["id_Exact"]["default"] = 411
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize("empty", [None, {}])
def test_declared_cost_exclusion_and_ordering_defaults_preserve_scope(empty):
    expectation, observed, judgment = cost_control()
    args = observed["trace"]["tool_calls"][0]["output"]["root_fields"][
        "projectCostList"
    ]["arguments"]
    args["exclude"]["default"] = empty
    args["orderBy"]["default"] = [{"field": "id", "direction": "ASC"}]
    bind_control_trace(observed, judgment)
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


def test_exact_bound_cost_filter_default_can_supply_the_declared_full_scope():
    expectation, observed, judgment = cost_control()
    schema = observed["trace"]["tool_calls"][0]["output"]
    native = observed["trace"]["tool_calls"][-1]["arguments"]["arguments"]
    schema["root_fields"]["projectCostList"]["arguments"]["filter"]["default"] = (
        native.pop("filter")
    )
    bind_control_trace(observed, judgment)
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_filter_ast",
        "missing_filter_ast",
        "invalid_filter_ast",
        "numeric_bool_ast",
        "numeric_float_ast",
        "nested_order_ast",
        "input_field_ast",
    ],
)
def test_every_captured_default_requires_exact_type_aware_json_and_ast_agreement(
    mutation,
):
    expectation, observed, judgment = cost_control()
    schema = observed["trace"]["tool_calls"][0]["output"]
    native = observed["trace"]["tool_calls"][-1]["arguments"]["arguments"]
    definitions = schema["root_fields"]["projectCostList"]["arguments"]
    definitions["filter"]["default"] = native.pop("filter")
    definitions["filter"]["default_graphql"] = (
        '{costDate_Gte: "2040-01-01", costDate_Lt: "2041-01-01", '
        'kind_Exact: "actual", currency_Exact: "EUR"}'
    )
    if mutation == "extra_filter_ast":
        definitions["filter"]["default_graphql"] = (
            '{id_Exact: 411, costDate_Gte: "2040-01-01", '
            'costDate_Lt: "2041-01-01", kind_Exact: "actual"}'
        )
    elif mutation == "missing_filter_ast":
        definitions["filter"]["default_graphql"] = '{kind_Exact: "actual"}'
    elif mutation == "invalid_filter_ast":
        definitions["filter"]["default_graphql"] = "{"
    elif mutation == "numeric_bool_ast":
        definitions["pageSize"].update(default=True, default_graphql="1")
    elif mutation == "numeric_float_ast":
        definitions["pageSize"].update(default=1, default_graphql="1.0")
    elif mutation == "nested_order_ast":
        definitions["orderBy"].update(
            default=[{"field": "id", "direction": "ASC"}],
            default_graphql='[{field: "id", direction: DESC}]',
        )
    else:
        schema["types"]["ProjectCostFilter"]["fields"]["kind_Exact"].update(
            default="actual", default_graphql='"plan"'
        )
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


def test_matching_native_nonempty_default_json_and_ast_can_supply_cost_scope():
    expectation, observed, judgment = cost_control()
    schema = observed["trace"]["tool_calls"][0]["output"]
    native = observed["trace"]["tool_calls"][-1]["arguments"]["arguments"]
    definitions = schema["root_fields"]["projectCostList"]["arguments"]
    definitions["filter"].update(
        default=native.pop("filter"),
        default_graphql=(
            '{costDate_Gte: "2040-01-01", costDate_Lt: "2041-01-01", '
            'kind_Exact: "actual", currency_Exact: "EUR"}'
        ),
    )
    definitions["orderBy"].update(
        default=[{"field": "id", "direction": "ASC"}],
        default_graphql='[{field: "id", direction: ASC}]',
    )
    bind_control_trace(observed, judgment)
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize(
    "mutation",
    [
        "default_exclude",
        "explicit_search",
        "default_search",
        "root_input_default",
        "project_input_default",
        "missing_input_definition",
        "foreign_schema",
        "future_schema",
    ],
)
def test_complete_shipment_rows_require_the_full_bound_effective_history_scope(
    mutation,
):
    expectation, observed, judgment = shipment_forecast_control()
    calls = observed["trace"]["tool_calls"]
    schema = calls[-2]["output"]
    definitions = schema["root_fields"]["shipmentList"]["arguments"]
    if mutation == "default_exclude":
        definitions["exclude"]["default"] = {"id_Exact": 999}
    elif mutation in {"explicit_search", "default_search"}:
        definitions["search"] = {"type": "String"}
        if mutation == "explicit_search":
            calls[-1]["arguments"]["arguments"]["search"] = "subset"
        else:
            definitions["search"]["default"] = "subset"
    elif mutation == "root_input_default":
        schema["types"]["ShipmentFilter"]["fields"]["id_Exact"]["default"] = 101
    elif mutation == "project_input_default":
        schema["types"]["ProjectFilter"]["fields"]["id_Exact"]["default"] = 700
    elif mutation == "missing_input_definition":
        schema["types"].pop("ProjectFilter")
    elif mutation == "foreign_schema":
        schema["manager"] = "Invoice"
    else:
        calls[-2:] = calls[-2:][::-1]
    bind_control_trace(observed, judgment)
    assert not score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize("empty", [None, {}])
def test_native_empty_shipment_exclusion_and_ordering_defaults_preserve_full_history(
    empty,
):
    expectation, observed, judgment = shipment_forecast_control()
    definitions = observed["trace"]["tool_calls"][-2]["output"]["root_fields"][
        "shipmentList"
    ]["arguments"]
    definitions["exclude"]["default"] = empty
    definitions["orderBy"]["default"] = [{"field": "id", "direction": "ASC"}]
    bind_control_trace(observed, judgment)
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


@pytest.mark.parametrize("empty", [None, {}])
def test_explicit_empty_shipment_exclusion_overrides_a_narrowing_native_default(empty):
    expectation, observed, judgment = shipment_forecast_control()
    calls = observed["trace"]["tool_calls"]
    calls[-2]["output"]["root_fields"]["shipmentList"]["arguments"]["exclude"][
        "default"
    ] = {"id_Exact": 999}
    calls[-1]["arguments"]["arguments"]["exclude"] = empty
    bind_control_trace(observed, judgment)
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]


def test_matching_native_shipment_filter_default_can_supply_full_customer_history_scope():
    expectation, observed, judgment = shipment_forecast_control()
    calls = observed["trace"]["tool_calls"]
    definitions = calls[-2]["output"]["root_fields"]["shipmentList"]["arguments"]
    definitions["filter"]["default"] = calls[-1]["arguments"]["arguments"].pop("filter")
    bind_control_trace(observed, judgment)
    assert score_turn(
        expectation,
        observed,
        registry=expectation["manager_interfaces"],
        semantic_judgment=judgment,
    )["passed"]
