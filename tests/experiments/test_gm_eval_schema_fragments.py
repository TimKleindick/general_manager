"""Selected identities require compatible, manager-bound schema evidence."""

from copy import deepcopy

import pytest

from experiments.gm_eval.evidence_rows import query_identity_rows

SNAPSHOT = "a" * 64


def schema(view="overview", manager="Container", snapshot=SNAPSHOT):
    value = {
        "contract_version": 2,
        "manager": manager,
        "schema_view": view,
        "snapshot": snapshot,
        "schema_complete": view == "full",
    }
    if view == "detail":
        value["types"] = {
            "ContainerFilter": {"kind": "input", "fields": {"id": {"type": "Int"}}},
            "Int": {"kind": "scalar"},
        }
        return value
    value.update(
        type="ContainerType",
        relations=[{"name": "component", "path": ["component"], "target": "Component"}],
        output_fields={"component": {"type": "ComponentType!"}},
        type_manifest={
            "ContainerType": {"kind": "object"},
            "ComponentType": {"kind": "reference", "manager": "Component"},
        },
    )
    if view == "full":
        value["types"] = {
            "ContainerType": {
                "kind": "object",
                "fields": deepcopy(value["output_fields"]),
            },
            "ComponentType": {"kind": "reference", "manager": "Component"},
        }
    return value


def query():
    return {
        "id": "query-1",
        "name": "query",
        "root_manager": "Container",
        "managers": ["Container", "Component"],
        "source_turn": 0,
        "arguments": {
            "manager": "Container",
            "fields": ["id", {"component": ["id", "code"]}],
        },
        "output": {
            "data": [{"id": 701, "component": {"id": 209, "code": "COMP-A"}}],
            "complete": True,
            "has_more": False,
        },
    }


def schema_call(value, identifier):
    args = {"manager": value["manager"], "view": value["schema_view"]}
    if value["schema_view"] == "detail":
        args.update(snapshot=value["snapshot"], types=list(value["types"]))
    return {
        "id": identifier,
        "name": "get_manager_schema",
        "source_turn": 0,
        "arguments": args,
        "output": value,
    }


def extract(*values, q=None):
    q = q or query()
    calls = [schema_call(s, f"schema-{i}") for i, s in enumerate(values)]
    return query_identity_rows(q, [*calls, q], "Component")


@pytest.mark.parametrize(
    "views", [("overview",), ("overview", "detail"), ("full", "detail")]
)
def test_compatible_views_bind_selected_reference(views):
    rows, proofs = extract(*(schema(view) for view in views))
    assert rows == [{"id": 209, "code": "COMP-A"}]
    assert proofs[0]["row_path"] == ["data", 0, "component"]
    assert proofs[0]["schema_call_ids"] == [f"schema-{i}" for i in range(len(views))]
    assert proofs[0]["schema_snapshots"] == {"Container": SNAPSHOT}
    assert proofs[0]["selected_fields"] == ["code", "id"]


def test_different_snapshots_cannot_authorize_identity():
    assert extract(schema(), schema("detail", snapshot="b" * 64)) == ([], [])


@pytest.mark.parametrize("snapshot", [None, "", "not-a-digest"])
def test_selective_view_requires_a_real_snapshot(snapshot):
    value = schema()
    value["snapshot"] = snapshot
    assert extract(value) == ([], [])


def test_same_named_conflicting_type_invalidates_manager():
    first = schema("full")
    second = schema("detail")
    second["types"]["ComponentType"] = {"kind": "reference", "manager": "Other"}
    assert extract(first, second) == ([], [])


def test_overview_and_explicit_owner_fields_must_agree():
    value = schema("full")
    value["types"]["ContainerType"]["fields"]["component"]["type"] = "String"
    assert extract(value) == ([], [])


def test_manifest_and_explicit_reference_must_agree():
    value = schema("full")
    value["types"]["ComponentType"]["manager"] = "Other"
    assert extract(value) == ([], [])


def test_conflicting_manifest_between_views_invalidates_manager():
    second = schema()
    second["type_manifest"]["ComponentType"]["manager"] = "Other"
    assert extract(schema(), second) == ([], [])


def test_no_unselected_or_extra_nested_identity_fields():
    q = query()
    q["arguments"]["fields"] = ["id"]
    assert extract(schema(), q=q) == ([], [])
    q = query()
    q["output"]["data"][0]["component"]["unselected_alias"] = "NOT-SELECTED"
    assert extract(schema(), q=q)[0] == [{"id": 209, "code": "COMP-A"}]


def test_detail_alone_cannot_invent_root_or_relation():
    assert extract(schema("detail")) == ([], [])


@pytest.mark.parametrize(
    "typename",
    ["[[ComponentType]]", "ComponentType]", "[ComponentType", "3ComponentType"],
)
def test_malformed_or_unsupported_nested_type_is_ineligible(typename):
    value = schema()
    value["output_fields"]["component"]["type"] = typename
    assert extract(value) == ([], [])


def collection_schema():
    value = schema()
    value["relations"] = [
        {"name": "components", "path": ["components", "items"], "target": "Component"}
    ]
    value["output_fields"] = {"components": {"type": "ComponentPage"}}
    value["type_manifest"]["ComponentPage"] = {"kind": "object"}
    return value


def collection_query():
    q = query()
    q["arguments"]["fields"] = [{"components": [{"items": ["id", "code"]}]}]
    q["output"]["data"] = [
        {
            "components": {
                "items": [{"id": 209, "code": "COMP-A"}, {"id": 210, "code": "COMP-B"}]
            }
        }
    ]
    return q


def test_collection_requires_loaded_intermediate_type_definition():
    assert extract(collection_schema(), q=collection_query()) == ([], [])
    detail = schema("detail")
    detail["types"]["ComponentPage"] = {
        "kind": "object",
        "fields": {"items": {"type": "[ComponentType!]!"}},
    }
    rows, proofs = extract(collection_schema(), detail, q=collection_query())
    assert rows == [{"id": 209, "code": "COMP-A"}, {"id": 210, "code": "COMP-B"}]
    assert proofs[1]["row_path"] == ["data", 0, "components", "items", 1]
    assert proofs[1]["schema_call_ids"] == ["schema-0", "schema-1"]


def test_unsupported_manifest_never_becomes_a_manager_reference():
    value = schema()
    value["type_manifest"]["ComponentType"] = {"kind": "unsupported", "reason": "union"}
    assert extract(value) == ([], [])


def test_duplicate_or_future_call_ids_do_not_authorize_rows():
    q = query()
    call = schema_call(schema(), "schema-1")
    assert query_identity_rows(q, [call, deepcopy(call), q], "Component") == ([], [])
    assert query_identity_rows(q, [q, call], "Component") == ([], [])
    call["source_turn"] = 1
    assert query_identity_rows(q, [call, q], "Component") == ([], [])


def test_wrong_detail_snapshot_argument_does_not_supply_missing_definition():
    detail = schema("detail")
    detail["types"]["ComponentPage"] = {
        "kind": "object",
        "fields": {"items": {"type": "[ComponentType]"}},
    }
    q = collection_query()
    calls = [
        schema_call(collection_schema(), "overview"),
        schema_call(detail, "detail"),
        q,
    ]
    calls[1]["arguments"]["snapshot"] = "b" * 64
    assert query_identity_rows(q, calls, "Component") == ([], [])


def test_legacy_identical_full_schemas_remain_supported():
    value = schema("full")
    value.pop("schema_view")
    value.pop("snapshot")
    q = query()
    args = {"manager": "Container"}
    calls = [
        {
            "id": f"legacy-{i}",
            "name": "get_manager_schema",
            "arguments": args,
            "output": deepcopy(value),
        }
        for i in range(2)
    ]
    rows, proofs = query_identity_rows(q, [*calls, q], "Component")
    assert rows == [{"id": 209, "code": "COMP-A"}]
    assert proofs[0]["schema_call_ids"] == ["legacy-0", "legacy-1"]


@pytest.mark.parametrize("kind", ["default", "enum"])
def test_conflicting_loaded_definitions_stay_ineligible(kind):
    first = schema("detail")
    second = schema("detail")
    if kind == "default":
        first["types"]["ContainerFilter"]["fields"]["id"]["default_value"] = 1
        second["types"]["ContainerFilter"]["fields"]["id"]["default_value"] = 2
    else:
        first["types"]["Condition"] = {"kind": "enum", "values": ["READY", "HELD"]}
        second["types"]["Condition"] = {"kind": "enum", "values": ["READY", "GONE"]}
    assert extract(schema(), first, second) == ([], [])


def deep_case():
    component = {
        "contract_version": 2,
        "manager": "Component",
        "schema_view": "overview",
        "snapshot": "b" * 64,
        "schema_complete": False,
        "type": "ComponentType",
        "relations": [{"name": "part", "path": ["part"], "target": "Part"}],
        "output_fields": {"part": {"type": "PartType"}},
        "type_manifest": {
            "ComponentType": {"kind": "object"},
            "PartType": {"kind": "reference", "manager": "Part"},
        },
    }
    q = query()
    q["managers"].append("Part")
    q["arguments"]["fields"][1]["component"].append({"part": ["id", "code"]})
    q["output"]["data"][0]["component"]["part"] = {"id": 407, "code": "PART-Q"}
    calls = [
        schema_call(schema(), "container-schema"),
        schema_call(component, "component-schema"),
        q,
    ]
    return q, calls


def test_deeper_reference_requires_each_managers_own_snapshot():
    q, calls = deep_case()
    rows, proofs = query_identity_rows(q, calls, "Part")
    assert rows == [{"id": 407, "code": "PART-Q"}]
    assert proofs[0]["row_path"] == ["data", 0, "component", "part"]
    assert proofs[0]["schema_call_ids"] == ["container-schema", "component-schema"]
    assert proofs[0]["schema_snapshots"] == {
        "Container": SNAPSHOT,
        "Component": "b" * 64,
    }
    calls.pop(1)
    assert query_identity_rows(q, calls, "Part") == ([], [])


def test_unbound_intermediate_manager_cannot_authorize_descendant():
    q, calls = deep_case()
    q["managers"].remove("Component")
    assert query_identity_rows(q, calls, "Part") == ([], [])


@pytest.mark.parametrize("binding", ["Container Component", ["Component"]])
def test_query_manager_binding_must_be_a_list_including_root(binding):
    q = query()
    q["managers"] = binding
    assert extract(schema(), q=q) == ([], [])


def legacy_extract(value):
    q = query()
    calls = [
        {
            "id": f"legacy-{i}",
            "name": "get_manager_schema",
            "arguments": {"manager": "Container"},
            "output": deepcopy(value),
        }
        for i in range(2)
    ]
    return query_identity_rows(q, [*calls, q], "Component")


@pytest.mark.parametrize(
    "keep_snapshot, keep_complete", [(False, True), (True, True), (False, False)]
)
def test_unmarked_overview_never_authorizes_nested_identity(
    keep_snapshot, keep_complete
):
    value = schema()
    value.pop("schema_view")
    if not keep_snapshot:
        value.pop("snapshot")
    if not keep_complete:
        value.pop("schema_complete")
    assert legacy_extract(value) == ([], [])


@pytest.mark.parametrize("missing_type", ["ContainerType", "ComponentType"])
def test_unmarked_schema_requires_loaded_owner_and_reference(missing_type):
    value = schema("full")
    value.pop("schema_view")
    value.pop("snapshot")
    del value["types"][missing_type]
    assert legacy_extract(value) == ([], [])


def test_unmarked_incomplete_flag_cannot_claim_legacy_full():
    value = schema("full")
    value.pop("schema_view")
    value["schema_complete"] = False
    assert legacy_extract(value) == ([], [])


def test_null_view_does_not_claim_legacy_compatibility():
    value = schema("full")
    value["schema_view"] = None
    assert legacy_extract(value) == ([], [])


@pytest.mark.parametrize("keep_snapshot", [False, True])
def test_real_legacy_types_work_without_modern_manifest_or_complete_flag(keep_snapshot):
    value = schema("full")
    for name in ("schema_view", "schema_complete", "type_manifest"):
        value.pop(name)
    if not keep_snapshot:
        value.pop("snapshot")
    rows, proofs = legacy_extract(value)
    assert rows == [{"id": 209, "code": "COMP-A"}]
    assert proofs[0]["schema_call_ids"] == ["legacy-0", "legacy-1"]
    assert proofs[0]["row_path"] == ["data", 0, "component"]
    if keep_snapshot:
        assert proofs[0]["schema_snapshots"] == {"Container": SNAPSHOT}
    else:
        assert "schema_snapshots" not in proofs[0]
