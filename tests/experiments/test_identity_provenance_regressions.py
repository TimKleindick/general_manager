"""Generic identity controls, separate from any historical model score."""

from copy import deepcopy
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from graphql import build_schema

from experiments.gm_eval.evidence_rows import query_identity_rows
from experiments.gm_eval.scoring import _returned_identities
from general_manager.api.graphql import GraphQL
from general_manager.chat import graphql_contract
from general_manager.chat.schema_inspection import inspect_manager_schema
from tests.experiments.test_gm_eval_scoring import (
    bind_control_trace,
    control,
    expected,
    identity_control,
    score,
)


def identity_evidence(rows):
    return {
        "ev-component": {
            "manager": "Component",
            "identity_rows": rows,
            "identity_proofs": [
                {"row_path": ["data", i], "query_call_id": "query-components"}
                for i in range(len(rows))
            ],
        }
    }


SOURCES = [
    {"code": "COMP-X", "name": "First component"},
    {"code": "COMP-Y", "name": "Second component"},
]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("identifier", [407, "id-407"])
def test_identity_free_duplicate_does_not_poison_independent_selected_identity(
    reverse, identifier
):
    rows = [{"id": identifier, "code": "COMP-X"}, {"id": identifier, "family": "x"}]
    if reverse:
        rows.reverse()
    evidence = identity_evidence(rows)
    before = deepcopy(evidence)
    mappings, blocked = _returned_identities("Component", SOURCES, evidence)
    assert blocked == {}
    assert mappings[str(identifier)]["canonical_code"] == "COMP-X"
    assert len(mappings[str(identifier)]["proof"]) == 1
    assert evidence == before


@pytest.mark.parametrize(
    "bad_row",
    [
        {"id": 407, "code": "UNKNOWN"},
        {"id": 407, "code": "COMP-Y"},
        {"id": 407, "code": "COMP-X", "name": "Wrong name"},
        {"id": 407, "name": "Unknown name"},
        {"id": 407, "name": None},
        {"id": 407, "code": None},
        {"id": 908, "code": "COMP-X"},
    ],
)
def test_explicit_identity_contradictions_still_block_mapping(bad_row):
    mappings, blocked = _returned_identities(
        "Component",
        SOURCES,
        identity_evidence([{"id": 407, "code": "COMP-X"}, bad_row]),
    )
    assert "407" not in mappings
    assert "407" in blocked


def test_identity_free_rows_alone_never_invent_identity():
    mappings, _ = _returned_identities(
        "Component", SOURCES, identity_evidence([{"id": 407}, {"id": True}])
    )
    assert mappings == {}
    evidence = identity_evidence([{"id": 407, "code": "COMP-X"}])
    evidence["ev-component"]["manager"] = "Other"
    assert _returned_identities("Component", SOURCES, evidence)[0] == {}


def project_control(contract):
    observation, judgment = control(contract)
    identity, _ = identity_control(
        contract,
        "Project",
        [{"id": 407, "code": "P01"}],
        contract["facts"]["result_ids"],
    )
    observation["trace"]["tool_calls"] = [
        identity["trace"]["tool_calls"][0] if call["id"] == "call-Project" else call
        for call in observation["trace"]["tool_calls"]
    ]
    return observation, judgment


def test_root_identity_requires_actual_selection_of_identity_fields():
    contract = expected("E106")
    observation, judgment = identity_control(
        contract,
        "Customer",
        [{"id": 611, "code": "C01"}, {"id": 822, "code": "C02"}],
        [611, 822],
    )
    observation["trace"]["tool_calls"][0]["arguments"]["fields"] = ["id"]
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["identity_normalization"]["mappings"] == []
    assert result["dimensions"]["R"]["status"] != "pass"


@pytest.mark.parametrize(
    "metadata",
    [
        {"complete": False},
        {"has_more": True, "total_count": 2},
        {"has_more": False, "total_count": 2},
        {"status": "error"},
        {"errors": ["resolver failed"]},
        {"total_count": True},
    ],
)
def test_incomplete_or_failed_query_cannot_supply_reachability_identity(metadata):
    contract = expected("E089")
    observation, judgment = project_control(contract)
    observation["facts"]["constraints"]["reachable_from"] = 407
    call = next(
        row for row in observation["trace"]["tool_calls"] if row["id"] == "call-Project"
    )
    call["output"].update(metadata)
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["identity_normalization"]["mappings"] == []
    assert result["dimensions"]["R"]["status"] != "pass"


def paginated_control(native=False):
    contract = expected("E106")
    observation, judgment = identity_control(
        contract, "Customer", [{"id": 611, "code": "C01"}], [611, 822]
    )
    first = observation["trace"]["tool_calls"][0]
    if native:
        first["arguments"]["arguments"] = {
            "page": 1,
            "pageSize": 1,
            "orderBy": [{"field": "id", "direction": "ASC"}],
        }
    else:
        first["arguments"].update(limit=100, offset=0)
    first["output"].update(total_count=2, has_more=True, complete=False)
    second = deepcopy(first)
    second["id"] = "call-Customer-page2"
    if native:
        second["arguments"]["arguments"]["page"] = 2
    else:
        second["arguments"]["offset"] = 1
    second["output"].update(data=[{"id": 822, "code": "C02"}], has_more=False)
    observation["trace"]["tool_calls"].append(second)
    reference = "ev-Customer-page2"
    observation["trace"]["evidence"].append(
        {
            "id": reference,
            "manager": "Customer",
            "origin": "tool",
            "call_id": second["id"],
        }
    )
    observation["extraction"]["evidence_ids"].append(reference)
    observation["citations"].append(reference)
    for support in observation["fact_support"].values():
        support["evidence_ids"].append(reference)
    bind_control_trace(observation, judgment)
    return contract, observation, judgment


@pytest.mark.parametrize("native", [False, True])
def test_complete_consistent_query_windows_remain_eligible(native):
    contract, observation, judgment = paginated_control(native)
    assert score(contract, observation, judgment)["dimensions"]["R"]["status"] == "pass"


def test_default_native_order_direction_is_equivalent_to_explicit_ascending():
    contract, observation, judgment = paginated_control(native=True)
    observation["trace"]["tool_calls"][0]["arguments"]["arguments"]["orderBy"][0].pop(
        "direction"
    )
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["dimensions"]["R"]["status"] == "pass"


@pytest.mark.parametrize("count", [None, True, -1, 2])
def test_partial_or_unknown_nested_page_cannot_supply_identity(count):
    contract, observation, judgment = paginated_control()
    for call in observation["trace"]["tool_calls"]:
        call["arguments"]["fields"].append(
            {"related": [{"items": ["id"]}, {"pageInfo": ["totalCount"]}]}
        )
        call["output"]["data"][0]["related"] = {
            "items": [{"id": 91}],
            "pageInfo": {"totalCount": count},
        }
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["dimensions"]["R"]["status"] != "pass"


def test_offline_scorer_remains_importable_without_django_settings():
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    imported = subprocess.run(  # noqa: S603 - fixed interpreter and literal import probe
        [sys.executable, "-B", "-c", "import experiments.gm_eval.scoring"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    "mutation",
    [
        "missing_window",
        "conflicting_window",
        "filter_change",
        "count_change",
        "wrong_has_more",
        "failed_window",
    ],
)
def test_incomplete_or_inconsistent_query_windows_cannot_prove_identity(
    native, mutation
):
    contract, observation, judgment = paginated_control(native)
    second = observation["trace"]["tool_calls"][1]
    args = second["arguments"]["arguments"] if native else second["arguments"]
    if mutation == "missing_window":
        args["page" if native else "offset"] = 3
    elif mutation == "conflicting_window":
        args["page" if native else "offset"] = 1 if native else 0
    elif mutation == "filter_change":
        second["arguments"]["filters"] = {"code": "C02"}
    elif mutation == "count_change":
        second["output"]["total_count"] = 3
    elif mutation == "wrong_has_more":
        second["output"]["has_more"] = True
    else:
        second["error"] = True
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["dimensions"]["R"]["status"] != "pass"


def test_reachability_constraint_normalizes_from_selected_project_evidence():
    contract = expected("E089")
    observation, judgment = project_control(contract)
    observation["facts"]["constraints"]["reachable_from"] = 407
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["dimensions"]["R"]["status"] == "pass"
    assert (
        result["identity_normalization"]["normalized_facts"]["constraints"][
            "reachable_from"
        ]
        == "P01"
    )
    assert observation["facts"]["constraints"]["reachable_from"] == 407


@pytest.mark.parametrize("identifier", [908, True, None, "P02"])
def test_reachability_wrong_unproven_or_invalid_ids_never_pass(identifier):
    contract = expected("E089")
    observation, judgment = project_control(contract)
    observation["facts"]["constraints"]["reachable_from"] = identifier
    bind_control_trace(observation, judgment)
    assert score(contract, observation, judgment)["dimensions"]["R"]["status"] != "pass"


def test_planned_revenue_project_has_explicit_scalar_identity_semantics():
    contract = expected("E098")
    observation, judgment = project_control(contract)
    observation["facts"]["planned_revenue"]["project"] = 407
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["dimensions"]["C"]["status"] == "pass"
    assert (
        result["identity_normalization"]["normalized_facts"]["planned_revenue"][
            "project"
        ]
        == "P01"
    )


@pytest.fixture
def collection_contract(monkeypatch):
    native = build_schema("""
        input DeepInput { next: DeepInput, value: String }
        input ContainerFilter { deep: DeepInput }
        type Component { id: Int!, code: String!, privateValue: String }
        type PageInfo { totalCount: Int! }
        type ComponentPage { items: [Component!]!, pageInfo: PageInfo! }
        type Container { id: Int!, components: ComponentPage }
        type ContainerPage { items: [Container!]!, pageInfo: PageInfo! }
        type Query { containerList(filter: ContainerFilter): ContainerPage,
                     componentList: ComponentPage }
    """)
    managers = {}
    types = {}
    for name in ("Container", "Component"):
        managers[name] = type(name, (), {"chat_exposed": True})
        types[name] = type(name + "Type", (), {})
        native.get_type(name).graphene_type = types[name]
    monkeypatch.setattr(GraphQL, "manager_registry", managers)
    monkeypatch.setattr(GraphQL, "graphql_type_registry", types)
    monkeypatch.setattr(
        GraphQL, "get_schema", lambda: SimpleNamespace(graphql_schema=native)
    )
    graphql_contract.clear_contract_cache()
    yield managers
    graphql_contract.clear_contract_cache()


def collection_query():
    return {
        "id": "query-container",
        "name": "query",
        "root_manager": "Container",
        "managers": ["Container", "Component"],
        "source_turn": 0,
        "arguments": {
            "manager": "Container",
            "fields": [{"components": [{"items": ["id", "code"]}]}],
        },
        "output": {
            "data": [{"components": {"items": [{"id": 407, "code": "COMP-X"}]}}],
            "has_more": False,
            "complete": True,
        },
    }


def schema_call(overview):
    return {
        "id": "schema-container",
        "name": "get_manager_schema",
        "source_turn": 0,
        "arguments": {"manager": "Container", "view": "overview"},
        "output": overview,
    }


def test_native_overview_supplies_only_exact_relation_path_signatures(
    collection_contract,
):
    overview = inspect_manager_schema("Container")
    full = inspect_manager_schema("Container", view="full")
    assert overview["relation_types"] == {
        "components": [full["types"]["ComponentPage"]["fields"]["items"]["type"]]
    }
    assert "types" not in overview
    assert overview["type_manifest"]["Component"] == {
        "kind": "reference",
        "manager": "Component",
    }
    assert "PageInfo" not in overview["type_manifest"]
    assert "DeepInput" not in overview["type_manifest"]
    assert overview["schema_complete"] is False
    assert overview["snapshot"] == full["snapshot"]
    q = collection_query()
    rows, proofs = query_identity_rows(q, [schema_call(overview), q], "Component")
    assert rows == [{"id": 407, "code": "COMP-X"}]
    assert proofs[0]["row_path"] == ["data", 0, "components", "items", 0]
    assert proofs[0]["schema_call_ids"] == ["schema-container"]
    assert proofs[0]["schema_snapshots"] == {"Container": overview["snapshot"]}


def test_hidden_relation_does_not_expose_wrapper_or_target(collection_contract):
    collection_contract["Component"].chat_exposed = False
    overview = inspect_manager_schema("Container")
    assert "components" not in overview["output_fields"]
    assert "ComponentPage" not in overview.get("types", {})
    assert "components" not in overview.get("relation_types", {})
    assert "Component" not in overview["type_manifest"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_path_types",
        "wrong_target",
        "stale_snapshot",
        "unselected",
        "failed_query",
        "wrong_tail",
        "malformed_tail",
        "wrong_length",
        "conflicting_loaded_type",
    ],
)
def test_collection_identity_requires_actual_typed_selected_successful_evidence(
    collection_contract, mutation
):
    overview = inspect_manager_schema("Container")
    q = collection_query()
    if mutation == "missing_path_types":
        overview.pop("relation_types", None)
    elif mutation == "wrong_target":
        overview["type_manifest"]["Component"]["manager"] = "Other"
    elif mutation == "stale_snapshot":
        overview["snapshot"] = "stale"
    elif mutation == "unselected":
        q["arguments"]["fields"] = ["id"]
    elif mutation == "failed_query":
        q["error"] = True
    elif mutation == "wrong_tail":
        overview["relation_types"]["components"] = ["[PageInfo!]!"]
    elif mutation == "malformed_tail":
        overview["relation_types"]["components"] = [True]
    elif mutation == "wrong_length":
        overview["relation_types"]["components"] = []
    elif mutation == "conflicting_loaded_type":
        overview["types"] = {
            "ComponentPage": {
                "kind": "object",
                "fields": {"items": {"type": "[PageInfo!]!"}},
            }
        }
    assert query_identity_rows(q, [schema_call(overview), q], "Component") == ([], [])
