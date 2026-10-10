"""Versioned evaluation semantics; synthetic controls never score a live model."""

from copy import deepcopy
import json

import pytest

from experiments.gm_eval import adjudication, catalog, oracle
from tests.experiments.test_gm_eval_scoring import (
    expected,
    case,
    control,
    score,
    bind_control_trace,
)


@pytest.mark.parametrize("identifier", ["E001", "E004", "E083", "E106"])
def test_ordinary_answer_uses_actual_internal_grounding(identifier):
    contract = expected(identifier)
    observation, judgment = control(contract)
    observation["citations"] = []
    assert contract["citation_policy"] == "internal_grounding"
    assert score(contract, observation, judgment)["dimensions"]["A"]["status"] == "pass"
    observation["fact_support"][
        next(
            rule["field"]
            for dimension in ("R", "C", "I")
            for rule in contract["dimensions"][dimension]["checks"]
        )
    ]["evidence_ids"] = []
    assert score(contract, observation, judgment)["dimensions"]["A"]["status"] == "fail"


@pytest.mark.parametrize(
    "question",
    [
        "Nenne XYZ mit Quelle.",
        "Identify XYZ and cite your sources.",
        "Identifie XYZ avec les sources.",
    ],
)
def test_explicit_source_requests_remain_strict(question):
    row = case("E001")
    row["turns"] = [question]
    contract = oracle.expected_turn(row, 0)
    observation, judgment = control(contract)
    observation["citations"] = []
    assert contract["citation_policy"] == "visible_required"
    assert not score(contract, observation, judgment)["passed"]


def test_reference_version_keeps_explicit_and_legacy_policy():
    assert catalog.REFERENCE_VERSION == "1.10"
    assert expected("E098")["citation_policy"] == "visible_required"
    assert expected("E100", 1)["citation_policy"] == "visible_required"
    legacy = expected("E001")
    legacy.update(schema_version="1.4", citation_policy="visible_required")
    observation, judgment = control(legacy)
    observation["citations"] = []
    assert not score(legacy, observation, judgment)["passed"]


@pytest.mark.parametrize("unit", ["g/cm3", "g/cm³", "g/cm^3"])
def test_density_unit_typography_is_equivalent_and_audited(unit):
    contract = expected("E119")
    observation, judgment = control(contract)
    observation["facts"]["unit"] = unit
    result = score(contract, observation, judgment)
    assert result["dimensions"]["C"]["status"] == "pass"
    assert result["unit_normalization"]["raw_observed"] == unit
    assert result["unit_normalization"]["normalized_observed"] == "g/cm3"
    assert observation["facts"]["unit"] == unit


@pytest.mark.parametrize(
    "unit", ["kg/m³", "g/m³", "g/cm²", "kg/cm3", "pieces", "G/cm³"]
)
def test_unit_normalization_does_not_convert_scale_dimension_or_case(unit):
    contract = expected("E119")
    observation, judgment = control(contract)
    observation["facts"]["unit"] = unit
    assert score(contract, observation, judgment)["dimensions"]["C"]["status"] == "fail"


def test_unit_normalization_is_only_for_unit_field_and_new_references():
    contract = expected("E119")
    observation, judgment = control(contract)
    observation["facts"]["unit"] = "g/cm³"
    observation["facts"]["metric"] = "density³"
    assert score(contract, observation, judgment)["dimensions"]["C"]["status"] == "fail"
    observation["facts"]["metric"] = "density"
    legacy = deepcopy(contract)
    legacy.pop("unit_normalization", None)
    legacy["schema_version"] = "1.4"
    assert score(legacy, observation, judgment)["dimensions"]["C"]["status"] == "fail"


def _project_control(rows=None):
    contract = expected("E121")
    observation, judgment = control(contract)
    rows = [{"id": 1, "code": "P01", "name": "Aurora"}] if rows is None else rows
    observation["trace"]["tool_calls"].append(
        {
            "id": "project-query",
            "name": "query",
            "root_manager": "Project",
            "arguments": {"manager": "Project", "fields": ["id", "code", "name"]},
            "managers": ["Project"],
            "manager_fields": {"Project": ["id", "code", "name"]},
            "output": {"data": rows, "total_count": len(rows), "has_more": False},
        }
    )
    observation["trace"]["evidence"].append(
        {
            "id": "project-evidence",
            "manager": "Project",
            "origin": "tool",
            "call_id": "project-query",
        }
    )
    observation["extraction"]["evidence_ids"].append("project-evidence")
    observation["facts"].update(
        result_ids=[1], values={"1": 30}, constraints={"project": 1}
    )
    bind_control_trace(observation, judgment)
    return contract, observation, judgment


def test_nested_project_id_uses_unique_actual_evidence_without_filling_missing_facts():
    contract, observation, judgment = _project_control()
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "pass"
    assert result["dimensions"]["C"]["status"] == "pass"
    assert result["identity_normalization"]["normalized_facts"]["constraints"] == {
        "project": "P01"
    }
    assert any(
        row.get("path") == ["constraints", "project"]
        and row["evidence_ids"] == ["project-evidence"]
        for row in result["identity_normalization"]["mappings"]
    )
    assert observation["facts"]["constraints"] == {"project": 1}
    observation["facts"]["result_ids"] = None
    observation["facts"]["constraints"] = {}
    missing = score(contract, observation, judgment)
    assert missing["identity_normalization"]["normalized_facts"]["result_ids"] is None
    assert missing["identity_normalization"]["normalized_facts"]["constraints"] == {}
    assert missing["dimensions"]["R"]["status"] == "fail"


@pytest.mark.parametrize(
    "rows", [[], [{"id": 1}], [{"id": 1, "code": "P01"}, {"id": 1, "code": "P02"}]]
)
def test_nested_identity_never_uses_gold_to_resolve_missing_or_ambiguous_evidence(rows):
    contract, observation, judgment = _project_control(rows)
    result = score(contract, observation, judgment)
    assert result["primary_failure"] == "judge_failure"
    assert (
        result["identity_normalization"]["normalized_facts"]["constraints"]["project"]
        == 1
    )


def test_field_meanings_and_identity_paths_are_value_free():
    contract = expected("E121")
    shapes = adjudication.fact_schemas(contract)
    assert "Project" in shapes["values"]["description"]
    assert "keys" in shapes["values"]["description"]
    assert "metric" in shapes["values"]["description"]
    assert "named" in shapes["result_ids"]["description"]
    assert "Project" in shapes["constraints"]["properties"]["project"]["description"]
    contract["facts"]["values"] = {"DO_NOT_LEAK": "99999999"}
    contract["facts"]["result_ids"] = ["DO_NOT_LEAK"]
    contract["facts"]["constraints"] = {"project": "DO_NOT_LEAK"}
    serialized = json.dumps(adjudication.fact_schemas(contract))
    assert (
        "DO_NOT_LEAK" not in serialized
        and "99999999" not in serialized
        and "P01" not in serialized
    )
    assert shapes == adjudication.fact_schemas(contract)


def _steel_control():
    contract = expected("E101")
    observation, judgment = control(contract)
    rows = [
        {"id": 2, "code": "M02", "family": "steel", "isActive": True, "active": True},
        {"id": 4, "code": "M04", "family": "steel", "isActive": True, "active": True},
        {"id": 5, "code": "M05", "family": "steel", "isActive": True, "active": False},
    ]
    fields = list(rows[0])
    observation["trace"]["tool_calls"] = [
        {
            "id": "call-Material",
            "name": "query",
            "root_manager": "Material",
            "arguments": {"manager": "Material", "fields": fields},
            "managers": ["Material"],
            "manager_fields": {"Material": fields},
            "output": {
                "data": rows,
                "total_count": 3,
                "has_more": False,
                "complete": True,
            },
        }
    ]
    observation["facts"]["constraints"] = {"family": "steel"}
    bind_control_trace(observation, judgment)
    return contract, observation, judgment


def test_steel_effective_scope_is_proved_separately_from_explicit_answer_constraints():
    contract, observation, judgment = _steel_control()
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert result["passed"]
    assert contract["facts"]["constraints"] == {"family": "steel", "is_active": True}
    assert observation == before
    assert result["selection_scope"]["effective_constraints"] == {
        "family": "steel",
        "is_active": True,
    }
    assert result["selection_scope"]["explicit_constraints"] == {"family": "steel"}
    assert result["selection_scope"]["evidence_ids"] == ["ev-Material"]


@pytest.mark.parametrize(
    "damage",
    [
        "deleted",
        "missing_flag",
        "wrong_family",
        "partial",
        "missing_row",
        "no_reference",
        "stale",
        "explicit_deleted",
        "explicit_active_only",
        "semantic_contradiction",
    ],
)
def test_steel_scope_preserves_deletion_completeness_binding_and_claim_controls(damage):
    contract, observation, judgment = _steel_control()
    output = observation["trace"]["tool_calls"][0]["output"]
    if damage == "deleted":
        output["data"][-1]["isActive"] = False
    elif damage == "missing_flag":
        output["data"][-1].pop("isActive")
    elif damage == "wrong_family":
        output["data"][-1]["family"] = "copper"
    elif damage == "partial":
        output.update(complete=False, has_more=True, total_count=4)
    elif damage == "missing_row":
        output["data"].pop()
        output["total_count"] = 2
    elif damage == "no_reference":
        observation["fact_support"]["result_ids"]["evidence_ids"] = []
    elif damage == "stale":
        observation["extraction"]["answer_sha256"] = "stale"
    elif damage == "explicit_deleted":
        observation["facts"]["constraints"]["is_active"] = False
    elif damage == "explicit_active_only":
        observation["facts"]["constraints"]["active"] = True
    elif damage == "semantic_contradiction":
        judgment["checks"]["model_specific_deletion_semantics"]["passed"] = False
    if damage in {"deleted", "missing_flag", "wrong_family", "partial", "missing_row"}:
        bind_control_trace(observation, judgment)
    assert not score(contract, observation, judgment)["passed"]


def test_e101_followup_status_contradiction_remains_a_candidate_failure():
    contract = expected("E101", 1)
    observation, judgment = control(contract)
    judgment["checks"]["model_specific_deletion_semantics"]["passed"] = False
    judgment["checks"]["answer_supported"]["passed"] = False
    result = score(contract, observation, judgment)
    assert result["primary_failure"] == "model_task_failure"
    assert result["dimensions"]["A"]["status"] == "fail"


@pytest.mark.parametrize(
    "support", ["invalid", {"result_ids": []}, {"result_ids": {"evidence_ids": [{}]}}]
)
def test_selection_scope_rejects_malformed_support_without_crashing(support):
    contract, observation, judgment = _steel_control()
    observation["fact_support"] = support
    assert not score(contract, observation, judgment)["passed"]


def test_steel_absent_explicit_scope_is_not_filled_from_gold():
    contract, observation, judgment = _steel_control()
    observation["facts"]["constraints"] = None
    observation["fact_support"]["constraints"] = {
        "status": "absent",
        "answer_quotes": [],
        "evidence_ids": [],
    }
    result = score(contract, observation, judgment)
    assert result["passed"]
    assert result["selection_scope"]["effective_constraints"]["is_active"] is True
    assert result["identity_normalization"]["normalized_facts"]["constraints"] is None
    assert observation["facts"]["constraints"] is None


def test_nested_identity_keeps_wrong_resolved_project_and_missing_results_wrong():
    contract, observation, judgment = _project_control(
        [{"id": 1, "code": "P02", "name": "Boreal Machinery"}]
    )
    # Use an unambiguous actual returned code; no name inference is needed.
    observation["trace"]["tool_calls"][-1]["output"]["data"][0].pop("name")
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "fail"
    assert result["identity_normalization"]["normalized_facts"]["constraints"] == {
        "project": "P02"
    }


def test_unit_typography_does_not_rescale_a_wrong_density_value():
    contract = expected("E119")
    observation, judgment = control(contract)
    observation["facts"].update(unit="g/cm³", values={"M01": "8960"})
    assert score(contract, observation, judgment)["dimensions"]["C"]["status"] == "fail"


def test_future_source_request_does_not_change_current_turn_policy():
    first = expected("E100", 0)
    assert first["citation_policy"] == "internal_grounding"
    assert expected("E100", 1)["citation_policy"] == "visible_required"


def _paged_steel_control():
    contract, observation, judgment = _steel_control()
    template = observation["trace"]["tool_calls"][0]
    rows = template["output"]["data"]
    calls, evidence = [], []
    for page, row in enumerate(rows, 1):
        call = deepcopy(template)
        call["id"] = f"page-{page}"
        call["arguments"].update(
            root="materialList",
            limit=1,
            arguments={
                "filter": {"family": "steel", "isActive": True},
                "orderBy": [{"field": "id", "direction": "ASC"}],
                "page": page,
                "pageSize": 1,
            },
        )
        call["output"].update(data=[row], has_more=page < 3, complete=False)
        calls.append(call)
        evidence.append(
            {
                "id": f"ev-page-{page}",
                "manager": "Material",
                "origin": "tool",
                "call_id": call["id"],
            }
        )
    observation["trace"]["tool_calls"] = calls
    observation["trace"]["evidence"] = evidence
    refs = [row["id"] for row in evidence]
    observation["extraction"]["evidence_ids"] = refs[:]
    for support in observation["fact_support"].values():
        support["evidence_ids"] = refs[:]
    for check in judgment["checks"].values():
        check["evidence_ids"] = ["answer", *refs]
    observation["citations"] = []
    bind_control_trace(observation, judgment)
    return contract, observation, judgment


def test_complete_bound_native_pages_prove_steel_scope_without_inserting_claims():
    contract, observation, judgment = _paged_steel_control()
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert result["passed"]
    assert result["selection_scope"]["selected_ids_proved"] == ["M02", "M04", "M05"]
    assert result["selection_scope"]["evidence_ids"] == [
        "ev-page-1",
        "ev-page-2",
        "ev-page-3",
    ]
    assert observation == before


@pytest.mark.parametrize(
    "damage",
    [
        "missing_page",
        "duplicate_page",
        "filter_change",
        "total_change",
        "missing_support",
        "missing_extraction",
        "duplicate_row",
        "has_more",
        "page_size_change",
        "missing_order",
        "order_change",
        "invalid_page",
        "offset",
        "truncated_page",
        "deleted",
        "missing_flag",
        "stale",
    ],
)
def test_paged_scope_requires_complete_consistent_bound_pages(damage):
    contract, observation, judgment = _paged_steel_control()
    calls = observation["trace"]["tool_calls"]
    args = calls[1]["arguments"]["arguments"]
    if damage == "missing_page":
        calls.pop(1)
        observation["trace"]["evidence"].pop(1)
    elif damage == "duplicate_page":
        args["page"] = 1
    elif damage == "filter_change":
        args["filter"]["isActive"] = False
    elif damage == "total_change":
        calls[1]["output"]["total_count"] = 4
    elif damage == "missing_support":
        observation["fact_support"]["result_ids"]["evidence_ids"].remove("ev-page-2")
    elif damage == "missing_extraction":
        observation["extraction"]["evidence_ids"].remove("ev-page-2")
    elif damage == "duplicate_row":
        calls[1]["output"]["data"] = deepcopy(calls[0]["output"]["data"])
    elif damage == "has_more":
        calls[-1]["output"]["has_more"] = True
    elif damage == "page_size_change":
        args["pageSize"] = 2
    elif damage == "missing_order":
        for call in calls:
            call["arguments"]["arguments"].pop("orderBy")
    elif damage == "order_change":
        args["orderBy"][0]["direction"] = "DESC"
    elif damage == "invalid_page":
        args["page"] = True
    elif damage == "offset":
        calls[1]["arguments"]["offset"] = 1
    elif damage == "truncated_page":
        calls[1]["output"]["data"] = []
    elif damage == "deleted":
        calls[-1]["output"]["data"][0]["isActive"] = False
    elif damage == "missing_flag":
        calls[-1]["output"]["data"][0].pop("isActive")
    bind_control_trace(observation, judgment)
    if damage == "stale":
        observation["extraction"]["evidence_trace_sha256"] = "stale"
    result = score(contract, observation, judgment)
    assert not result["passed"]
    assert result["selection_scope"]["status"] != "pass"


def _reverse_object_keys(value):
    if isinstance(value, dict):
        return {
            key: _reverse_object_keys(item) for key, item in reversed(value.items())
        }
    if isinstance(value, list):
        return [_reverse_object_keys(item) for item in value]
    return value


@pytest.mark.parametrize(
    "variation",
    [
        "outer_keys",
        "filter_keys",
        "recursive_keys",
        "all_default_asc",
        "mixed_default_asc",
        "combined",
    ],
)
def test_equivalent_page_arguments_preserve_scope_proof(variation):
    contract, observation, judgment = _paged_steel_control()
    calls = observation["trace"]["tool_calls"]
    middle = calls[1]["arguments"]
    if variation == "outer_keys":
        calls[1]["arguments"] = dict(reversed(middle.items()))
    elif variation == "filter_keys":
        filters = middle["arguments"]["filter"]
        middle["arguments"]["filter"] = dict(reversed(filters.items()))
    if variation == "all_default_asc":
        for call in calls:
            call["arguments"]["arguments"]["orderBy"][0].pop("direction")
    elif variation in {"mixed_default_asc", "combined"}:
        middle["arguments"]["orderBy"][0].pop("direction")
    if variation in {"recursive_keys", "combined"}:
        calls[1]["arguments"] = _reverse_object_keys(middle)
    bind_control_trace(observation, judgment)
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert result["passed"]
    assert result["selection_scope"]["evidence_ids"] == [
        "ev-page-1",
        "ev-page-2",
        "ev-page-3",
    ]
    assert observation == before


def test_scope_direction_default_matches_actual_graphql_input_type():
    from general_manager.api.graphql_ordering import create_ordering_types

    types = create_ordering_types(
        None, scope="evaluation_scope_default", field_paths={"id": "id"}
    )
    assert types is not None
    assert types.input_type._meta.fields["direction"].default_value.value == "ASC"


@pytest.mark.parametrize(
    "damage",
    [
        "desc",
        "filter",
        "total",
        "fact_binding",
        "extraction_binding",
        "projection_array",
        "filter_array",
        "ordering_array",
        "null_direction",
    ],
)
def test_query_equivalence_keeps_arrays_values_and_bindings_strict(damage):
    contract, observation, judgment = _paged_steel_control()
    calls = observation["trace"]["tool_calls"]
    for call in calls:
        native = call["arguments"]["arguments"]
        native["orderBy"] = [{"field": "family"}, {"field": "id"}]
        native["filter"]["id_In"] = [2, 4, 5]
    middle = calls[1]["arguments"]
    native = middle["arguments"]
    if damage == "desc":
        native["orderBy"][-1]["direction"] = "DESC"
    elif damage == "filter":
        native["filter"]["family"] = "copper"
    elif damage == "total":
        calls[1]["output"]["total_count"] = 4
    elif damage == "fact_binding":
        observation["fact_support"]["result_ids"]["evidence_ids"].remove("ev-page-2")
    elif damage == "extraction_binding":
        observation["extraction"]["evidence_ids"].remove("ev-page-2")
    elif damage == "projection_array":
        middle["fields"].reverse()
    elif damage == "filter_array":
        native["filter"]["id_In"].reverse()
    elif damage == "ordering_array":
        native["orderBy"].reverse()
    elif damage == "null_direction":
        native["orderBy"][-1]["direction"] = None
    calls[1]["arguments"] = _reverse_object_keys(middle)
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert not result["passed"]
    assert result["selection_scope"]["status"] == "fail"
