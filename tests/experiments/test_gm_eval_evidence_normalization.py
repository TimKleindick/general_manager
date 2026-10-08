"""Evidence-bound label and relation identity regressions, with synthetic controls."""

from copy import deepcopy

import pytest

from tests.experiments.test_gm_eval_scoring import (
    bind_control_trace,
    control,
    expected,
    identity_control,
    score,
)


def designation_control(value="Kupfer (Copper)"):
    contract = expected("E119")
    observation, judgment = identity_control(
        contract,
        "Material",
        [
            {
                "id": 1,
                "code": "M01",
                "name": "Copper",
                "aliases": "Kupfer | cuivre | CU-ETP",
                "densityGCm3": 8.96,
            }
        ],
        [1],
    )
    observation["facts"]["designation"] = value
    bind_control_trace(observation, judgment)
    return contract, observation, judgment


@pytest.mark.parametrize(
    "value",
    ["Kupfer (Copper)", "Copper (Kupfer)", "Kupfer", "cuivre (Copper)", "CU-ETP"],
)
def test_designation_uses_only_the_bound_returned_name_and_aliases(value):
    contract, observation, judgment = designation_control(value)
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert result["passed"]
    audit = result["designation_normalization"]
    assert audit["raw_observed"] == value
    assert audit["normalized_observed"] == "Copper"
    assert audit["evidence_ids"] == ["ev-Material"]
    assert audit["proof"][0]["row_path"] == ["data", 0]
    assert observation == before


@pytest.mark.parametrize(
    "value",
    [
        "Copper (Brass)",
        "Copper (Kupfer) extra",
        "Wrong Copper",
        "copper",
        "cobre",
        "Copper (cobre)",
        "Copper (Kupfer (Copper))",
        "Copper / Brass",
        None,
        ["Copper"],
    ],
)
def test_designation_does_not_accept_partial_fuzzy_translated_or_foreign_names(value):
    contract, observation, judgment = designation_control(value)
    assert score(contract, observation, judgment)["dimensions"]["R"]["status"] == "fail"


@pytest.mark.parametrize(
    "mutation",
    [
        "unselected_alias",
        "missing_alias",
        "wrong_evidence",
        "unbound_result",
        "wrong_manager",
        "conflicting_name",
        "conflicting_aliases",
        "ambiguous_rows",
        "stale_trace",
        "missing_extraction",
    ],
)
def test_designation_requires_unique_bound_actual_selected_record(mutation):
    contract, observation, judgment = designation_control()
    call = observation["trace"]["tool_calls"][0]
    if mutation == "unselected_alias":
        call["arguments"]["fields"].remove("aliases")
    elif mutation == "missing_alias":
        call["output"]["data"][0].pop("aliases")
    elif mutation == "wrong_evidence":
        observation["fact_support"]["designation"]["evidence_ids"] = []
    elif mutation == "unbound_result":
        observation["fact_support"]["result_ids"]["evidence_ids"] = []
    elif mutation == "wrong_manager":
        call["arguments"]["manager"] = "Customer"
    elif mutation == "conflicting_name":
        call["output"]["data"][0]["name"] = "Brass"
    elif mutation == "conflicting_aliases":
        call["output"]["data"].append({**call["output"]["data"][0], "aliases": "cobre"})
    elif mutation == "ambiguous_rows":
        call["output"]["data"].append({**call["output"]["data"][0], "id": 2})
    elif mutation == "missing_extraction":
        observation.pop("extraction")
    if mutation not in {"stale_trace", "missing_extraction"}:
        bind_control_trace(observation, judgment)
    elif mutation == "stale_trace":
        call["output"]["data"][0]["aliases"] += " | unrelated"
    result = score(contract, observation, judgment)
    assert not result["passed"]
    assert result["designation_normalization"]["normalized_observed"] != "Copper"


def test_designation_normalization_does_not_change_other_fields():
    contract, observation, judgment = designation_control()
    observation["facts"]["metric"] = "density (Copper)"
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "pass"
    assert result["dimensions"]["C"]["status"] == "fail"


def relation_control(shape="single", structured=False):
    contract = expected("E121")
    observation, judgment = control(contract)
    entity = {"id": 1, "code": "P01", "name": "Aurora"}
    path = (
        ["project"]
        if shape == "single"
        else ["projects", "items"]
        if shape == "page"
        else ["projects", "edges", "node"]
    )
    nested = (
        entity
        if shape == "single"
        else {"items": [entity], "pageInfo": {"totalCount": 1}}
        if shape == "page"
        else {"edges": [{"node": entity}], "pageInfo": {"totalCount": 1}}
    )
    children = ["id", "code", "name"]
    for hop in reversed(path):
        children = (
            [{"field": hop, "fields": children}] if structured else [{hop: children}]
        )
    if shape in {"page", "connection"}:
        page_fields = children[0]["fields"] if structured else children[0]["projects"]
        page_fields.append(
            {"field": "pageInfo", "fields": ["totalCount"]}
            if structured
            else {"pageInfo": ["totalCount"]}
        )
    types = {
        "ShipmentType": {
            "kind": "object",
            "fields": {
                path[0]: {
                    "type": "ProjectType"
                    if shape == "single"
                    else "ProjectPage"
                    if shape == "page"
                    else "ProjectConnection"
                }
            },
        },
        "ProjectType": {"kind": "reference", "manager": "Project"},
        "ProjectPage": {
            "kind": "object",
            "fields": {
                "items": {"type": "[ProjectType!]!"},
                "pageInfo": {"type": "ProjectPageInfo!"},
            },
        },
        "ProjectPageInfo": {
            "kind": "object",
            "fields": {"totalCount": {"type": "Int!"}},
        },
        "ProjectConnection": {
            "kind": "object",
            "fields": {
                "edges": {"type": "[ProjectEdge!]!"},
                "pageInfo": {"type": "ProjectPageInfo!"},
            },
        },
        "ProjectEdge": {"kind": "object", "fields": {"node": {"type": "ProjectType"}}},
    }
    schema = {
        "id": "schema-shipment",
        "name": "get_manager_schema",
        "arguments": {"manager": "Shipment"},
        "output": {
            "manager": "Shipment",
            "contract_version": 2,
            "type": "ShipmentType",
            "relations": [{"name": path[0], "path": path, "target": "Project"}],
            "types": types,
        },
        "error": False,
    }
    fields = [
        "id",
        "code",
        "quantity",
        "unit",
        "source",
        "shippedAt",
        "projectId",
        *children,
    ]
    row = {
        "id": 11,
        "code": "SH0011",
        "quantity": 30,
        "unit": "pieces",
        "source": "actual",
        "shippedAt": "2026-09-15",
        "projectId": 1,
        path[0]: nested,
    }
    query = {
        "id": "shipment-query",
        "name": "query",
        "arguments": {"manager": "Shipment", "fields": fields},
        "root_manager": "Shipment",
        "managers": ["Shipment", "Project"],
        "manager_fields": {
            "Shipment": [k for k in row],
            "Project": ["id", "code", "name"],
        },
        "output": {
            "data": [row],
            "total_count": 1,
            "has_more": False,
            "complete": True,
        },
        "error": False,
    }
    observation["trace"]["tool_calls"] = [schema, query]
    for evidence in observation["trace"]["evidence"]:
        evidence["call_id"] = query["id"]
    observation["trace"]["evidence"].append(
        {
            "id": "ev-Project",
            "manager": "Project",
            "origin": "tool",
            "call_id": query["id"],
        }
    )
    observation["extraction"]["evidence_ids"].append("ev-Project")
    for support in observation["fact_support"].values():
        support["evidence_ids"].append("ev-Project")
    observation["facts"].update(
        result_ids=[1], values={"1": 30}, constraints={"project": 1}
    )
    bind_control_trace(observation, judgment)
    return contract, observation, judgment


@pytest.mark.parametrize("shape", ["single", "page", "connection"])
@pytest.mark.parametrize("structured", [False, True])
def test_selected_typed_relation_rows_resolve_project_identity(shape, structured):
    contract, observation, judgment = relation_control(shape, structured)
    original = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert result["passed"]
    assert result["identity_normalization"]["normalized_facts"]["result_ids"] == ["P01"]
    proof = result["identity_normalization"]["mappings"][0]["proof"][0]
    assert proof["schema_call_ids"] == ["schema-shipment"]
    assert proof["query_call_id"] == "shipment-query"
    assert proof["row_path"][0:2] == ["data", 0]
    assert observation == original


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_schema",
        "schema_after_query",
        "schema_error",
        "schema_wrong_owner",
        "wrong_target",
        "untyped_target",
        "wrong_type",
        "ambiguous_relation",
        "ambiguous_schema",
        "unselected",
        "unselected_id",
        "duplicate_selection",
        "missing",
        "null",
        "scalar_fk_only",
        "unrelated_nested",
        "query_error",
        "wrong_root",
        "missing_identity",
        "conflicting_identity",
        "ambiguous_identity",
    ],
)
def test_nested_identity_requires_consistent_schema_selection_output_and_binding(
    mutation,
):
    contract, observation, judgment = relation_control()
    schema, query = observation["trace"]["tool_calls"]
    row = query["output"]["data"][0]
    if mutation == "missing_schema":
        observation["trace"]["tool_calls"].remove(schema)
    elif mutation == "schema_after_query":
        observation["trace"]["tool_calls"].reverse()
    elif mutation == "schema_error":
        schema["error"] = True
    elif mutation == "schema_wrong_owner":
        schema["arguments"]["manager"] = "Customer"
    elif mutation == "wrong_target":
        schema["output"]["relations"][0]["target"] = "Customer"
    elif mutation == "untyped_target":
        schema["output"]["types"]["ProjectType"].pop("manager")
    elif mutation == "wrong_type":
        schema["output"]["types"]["ShipmentType"]["fields"]["project"]["type"] = (
            "String"
        )
    elif mutation == "ambiguous_relation":
        schema["output"]["relations"].append(
            {"name": "project", "path": ["project"], "target": "Customer"}
        )
    elif mutation == "ambiguous_schema":
        other = deepcopy(schema)
        other["id"] = "schema-2"
        other["output"]["relations"][0]["target"] = "Customer"
        observation["trace"]["tool_calls"].insert(1, other)
    elif mutation == "unselected":
        query["arguments"]["fields"] = ["projectId"]
    elif mutation == "unselected_id":
        query["arguments"]["fields"][-1] = {"project": ["code", "name"]}
    elif mutation == "duplicate_selection":
        query["arguments"]["fields"].append({"project": ["id", "code"]})
    elif mutation == "missing":
        row.pop("project")
    elif mutation == "null":
        row["project"] = None
    elif mutation == "scalar_fk_only":
        row.pop("project")
        row["projectId"] = 1
    elif mutation == "unrelated_nested":
        row["arbitrary"] = row.pop("project")
    elif mutation == "query_error":
        query["error"] = True
    elif mutation == "wrong_root":
        query["root_manager"] = "Project"
    elif mutation == "missing_identity":
        row["project"] = {"id": 1}
    elif mutation == "conflicting_identity":
        row["project"]["name"] = "Borealis"
    elif mutation == "ambiguous_identity":
        query["output"]["data"].append(
            {**row, "project": {"id": 1, "code": "P02", "name": "Beacon"}}
        )
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert not result["passed"]
    assert result["identity_normalization"]["normalized_facts"]["result_ids"] == [1]


@pytest.mark.parametrize(
    "nested",
    [
        None,
        {},
        {"items": []},
        {"items": [None]},
        {"items": [{"id": 1}]},
        {"edges": [{"node": {"id": 1, "code": "P01"}}]},
    ],
)
def test_missing_null_or_wrong_connection_path_does_not_supply_identity(nested):
    contract, observation, judgment = relation_control("page")
    observation["trace"]["tool_calls"][-1]["output"]["data"][0]["projects"] = nested
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["primary_failure"] == "judge_failure"


def test_relation_metadata_or_rows_cannot_be_added_after_judge_binding():
    contract, observation, judgment = relation_control()
    observation["trace"]["tool_calls"][-1]["output"]["data"][0]["project"]["code"] = (
        "P02"
    )
    result = score(contract, observation, judgment)
    assert result["primary_failure"] == "judge_failure"
    assert result["identity_normalization"]["mappings"] == []


@pytest.mark.parametrize("shape", ["single", "page", "connection"])
def test_relation_row_container_must_match_the_declared_graphql_type(shape):
    contract, observation, judgment = relation_control(shape)
    row = observation["trace"]["tool_calls"][-1]["output"]["data"][0]
    if shape == "single":
        row["project"] = [row["project"]]
    elif shape == "page":
        row["projects"]["items"] = row["projects"]["items"][0]
    else:
        row["projects"]["edges"][0]["node"] = [row["projects"]["edges"][0]["node"]]
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["primary_failure"] == "judge_failure"


def test_designation_rule_is_not_specific_to_material_or_copper():
    contract = expected("E001")
    contract["facts"]["designation"] = "XYZ Industrie"
    contract["dimensions"]["R"]["checks"].append(
        {"field": "designation", "expected": "XYZ Industrie", "comparison": "exact"}
    )
    observation, judgment = identity_control(
        contract,
        "Customer",
        [
            {
                "id": 9,
                "code": "C01",
                "name": "XYZ Industrie",
                "aliases": ["XYZ", "Industrie XYZ"],
            }
        ],
        [9],
    )
    observation["facts"]["designation"] = "XYZ (XYZ Industrie)"
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["passed"]
    assert result["designation_normalization"]["normalized_observed"] == "XYZ Industrie"


def test_deeper_relations_retain_each_typed_hop_and_its_schema_binding():
    from experiments.gm_eval.evidence_rows import query_identity_rows

    _, observation, _ = relation_control()
    calls = observation["trace"]["tool_calls"]
    query = calls[-1]
    customer = {"id": 9, "code": "C01", "name": "XYZ Industrie"}
    query["arguments"]["fields"][-1]["project"].append(
        {"customer": ["id", "code", "name"]}
    )
    query["output"]["data"][0]["project"]["customer"] = customer
    query["managers"].append("Customer")
    calls.insert(
        1,
        {
            "id": "schema-project",
            "name": "get_manager_schema",
            "arguments": {"manager": "Project"},
            "output": {
                "manager": "Project",
                "contract_version": 2,
                "type": "ProjectType",
                "relations": [
                    {"name": "customer", "target": "Customer", "path": ["customer"]}
                ],
                "types": {
                    "ProjectType": {
                        "kind": "object",
                        "fields": {"customer": {"type": "CustomerType"}},
                    },
                    "CustomerType": {"kind": "reference", "manager": "Customer"},
                },
            },
        },
    )
    rows, proofs = query_identity_rows(query, calls, "Customer")
    assert rows == [customer]
    assert proofs[0]["row_path"] == ["data", 0, "project", "customer"]
    assert proofs[0]["schema_call_ids"] == ["schema-shipment", "schema-project"]
    calls.pop(1)
    assert query_identity_rows(query, calls, "Customer") == ([], [])


def test_multiple_distinct_relation_paths_cannot_merge_conflicting_identities():
    contract, observation, judgment = relation_control()
    schema, query = observation["trace"]["tool_calls"]
    schema["output"]["relations"].append(
        {"name": "otherProject", "path": ["otherProject"], "target": "Project"}
    )
    schema["output"]["types"]["ShipmentType"]["fields"]["otherProject"] = {
        "type": "ProjectType"
    }
    query["arguments"]["fields"].append({"otherProject": ["id", "code"]})
    query["output"]["data"][0]["otherProject"] = {"id": 1, "code": "P02"}
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["primary_failure"] == "judge_failure"


@pytest.mark.parametrize(
    "mutation", ["duplicate_query_id", "duplicate_schema_id", "future_schema_turn"]
)
def test_ambiguous_or_future_call_bindings_cannot_supply_nested_identity(mutation):
    contract, observation, judgment = relation_control()
    calls = observation["trace"]["tool_calls"]
    schema, query = calls
    if mutation == "duplicate_query_id":
        calls.insert(1, deepcopy(query))
    elif mutation == "duplicate_schema_id":
        calls.insert(1, deepcopy(schema))
    else:
        schema["source_turn"] = 1
        query["source_turn"] = 0
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["primary_failure"] == "judge_failure"


def test_alias_cannot_override_another_known_entity_name():
    contract, observation, judgment = designation_control("Copper (Brass)")
    observation["trace"]["tool_calls"][0]["output"]["data"][0]["aliases"] += " | Brass"
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["dimensions"]["R"]["status"] == "fail"
