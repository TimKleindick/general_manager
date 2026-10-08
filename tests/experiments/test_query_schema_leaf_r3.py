"""Only bound known composite types can contradict a leaf selection."""

from copy import deepcopy

import pytest

from experiments.gm_eval.evidence_rows import query_identity_rows
from tests.experiments.test_gm_eval_scoring import (
    bind_control_trace,
    expected,
    identity_control,
    score,
)


def schema_leaf_control(kind="object", name="projects", structured=False):
    contract = expected("E106")
    observation, judgment = identity_control(
        contract,
        "Customer",
        [{"id": 407, "code": "C01"}, {"id": 822, "code": "C02"}],
        [407, 822],
    )
    query = observation["trace"]["tool_calls"][0]
    query["arguments"]["fields"].append({"field": name} if structured else name)
    query["manager_fields"]["Customer"] = deepcopy(query["arguments"]["fields"])
    for row in query["output"]["data"]:
        row[name] = {"items": [{"id": 99}], "pageInfo": {"totalCount": 2}}
    schema = {
        "id": "declared-customer",
        "name": "get_manager_schema",
        "error": False,
        "arguments": {"manager": "Customer", "view": "full"},
        "output": {
            "manager": "Customer",
            "contract_version": 2,
            "schema_view": "full",
            "snapshot": "a" * 64,
            "type": "CustomerType",
            "relations": [],
            "types": {
                "CustomerType": {
                    "kind": "object",
                    "fields": {
                        "id": {"type": "Int!"},
                        "code": {"type": "String!"},
                        name: {"type": "[Envelope!]!"},
                    },
                },
                "Envelope": {"kind": kind, "fields": {"items": {"type": "[Int!]!"}}},
                "Int": {"kind": "scalar"},
                "String": {"kind": "scalar"},
            },
        },
    }
    observation["trace"]["tool_calls"].insert(0, schema)
    bind_control_trace(observation, judgment)
    return contract, observation, judgment


@pytest.mark.parametrize("name", ["items", "edges", "projects"])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("kind", ["object", "reference"])
def test_known_composite_leaf_cannot_credit_root_identity(name, structured, kind):
    contract, observation, judgment = schema_leaf_control(kind, name, structured)
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert result["identity_normalization"]["mappings"] == []
    assert result["dimensions"]["R"]["status"] != "pass"
    assert not result["passed"]
    assert observation == before


@pytest.mark.parametrize("kind", ["scalar", "enum", "unsupported"])
def test_known_noncomposite_kind_does_not_infer_object_from_json(kind):
    contract, observation, judgment = schema_leaf_control(kind, "edges")
    result = score(contract, observation, judgment)
    assert result["passed"]
    assert result["dimensions"]["R"]["status"] == "pass"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "after",
        "failed",
        "foreign",
        "future",
        "snapshot_mismatch",
        "duplicate_id",
        "ambiguous",
        "unknown_type",
    ],
)
def test_unbound_or_unknown_schema_cannot_invent_composite_contradiction(mutation):
    contract, observation, judgment = schema_leaf_control()
    calls = observation["trace"]["tool_calls"]
    schema, query = calls
    if mutation == "missing":
        calls.remove(schema)
    elif mutation == "after":
        calls.reverse()
    elif mutation == "failed":
        schema["error"] = True
    elif mutation == "foreign":
        schema["arguments"]["manager"] = schema["output"]["manager"] = "Foreign"
    elif mutation == "future":
        schema["source_turn"], query["source_turn"] = 1, 0
    elif mutation == "snapshot_mismatch":
        schema["arguments"]["snapshot"] = "b" * 64
    elif mutation == "duplicate_id":
        calls.insert(0, deepcopy(schema))
    elif mutation == "ambiguous":
        second = deepcopy(schema)
        second["id"] = "conflicting"
        second["output"]["snapshot"] = "b" * 64
        calls.insert(1, second)
    else:
        schema["output"]["types"].pop("Envelope")
    bind_control_trace(observation, judgment)
    rows, proofs = query_identity_rows(query, calls, "Customer")
    assert len(rows) == len(proofs) == 2
    assert score(contract, observation, judgment)["passed"]


def test_modern_overview_declares_composite_without_loading_details():
    contract, observation, judgment = schema_leaf_control()
    schema = observation["trace"]["tool_calls"][0]
    schema["arguments"]["view"] = schema["output"]["schema_view"] = "overview"
    schema["output"]["output_fields"] = schema["output"]["types"]["CustomerType"][
        "fields"
    ]
    schema["output"]["type_manifest"] = {"Envelope": {"kind": "object"}}
    schema["output"]["types"] = {}
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["identity_normalization"]["mappings"] == []
    assert not result["passed"]


@pytest.mark.parametrize("kind", ["object", "scalar"])
def test_nested_list_signature_keeps_the_named_type_selection_contract(kind):
    contract, observation, judgment = schema_leaf_control(kind)
    schema = observation["trace"]["tool_calls"][0]
    schema["output"]["types"]["CustomerType"]["fields"]["projects"]["type"] = (
        "[[Envelope!]!]!"
    )
    for row in observation["trace"]["tool_calls"][-1]["output"]["data"]:
        row["projects"] = [[row["projects"]]]
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["passed"] is (kind == "scalar")
    if kind == "object":
        assert result["identity_normalization"]["mappings"] == []
