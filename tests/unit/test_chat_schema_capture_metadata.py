"""Independent GraphQL schema inspection checks against frozen v27."""

import importlib.util
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest
from graphql import assert_valid_schema, build_schema, graphql_sync

from general_manager.api.graphql import GraphQL
from general_manager.chat.schema_inspection import (
    inspect_manager_schema,
    SchemaInspectionError,
)
from tests.unit.test_schema_inspection import exposed_schema as _exposed_schema

exposed_schema = _exposed_schema


def baseline_contract():
    path = Path(
        "/Users/tim/Documents/Codex/2026-10-03/task-4/verification/v26-working-source/src/general_manager/chat/graphql_contract.py"
    )
    spec = importlib.util.spec_from_file_location(
        "reviewer_baseline_v26_contract", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("metadata_location", ["schema", "hidden_type"])
def test_non_contract_metadata_must_not_break_schema_capture(
    exposed_schema, metadata_location
):
    schema, other = exposed_schema
    other.chat_exposed = False
    target = schema if metadata_location == "schema" else schema.get_type("Other")
    target.extensions["application_lock"] = RLock()
    assert_valid_schema(schema)
    execution = graphql_sync(
        schema,
        "{ partList { items { code } } }",
        root_value={"partList": {"items": [{"code": "P01"}]}},
    )
    assert execution.errors is None
    baseline = baseline_contract().manager_schema("Part")
    assert baseline["types"]["Part"]["fields"]["code"]["type"] == "String!"
    assert "Other" not in baseline["types"]
    print("VALID_EXECUTABLE_SCHEMA_AND_V26_INSPECTION_PASS", metadata_location)
    observed = inspect_manager_schema("Part")
    assert observed["schema_view"] == "overview"
    assert observed["output_fields"]["code"]["type"] == "String!"


def test_same_type_names_with_different_definitions_in_new_schema_invalidate_token(
    exposed_schema, monkeypatch
):
    old, _ = exposed_schema
    overview = inspect_manager_schema("Part")
    new = build_schema("""
      enum State { OPEN CLOSED NEW }
      input PartFilter { code: Int }
      type Part { code: Int!, state: State }
      type Info { totalCount: Int }
      type PartPage { items: [Part!]!, pageInfo: Info! }
      type Query { partList(filter: PartFilter, pageSize: Int = 7): PartPage }
    """)
    new.get_type("Part").graphene_type = old.get_type("Part").graphene_type
    monkeypatch.setattr(
        GraphQL, "get_schema", lambda: SimpleNamespace(graphql_schema=new)
    )
    fresh = inspect_manager_schema("Part")
    assert fresh["snapshot"] != overview["snapshot"]
    with pytest.raises(SchemaInspectionError, match="schema_snapshot_mismatch"):
        inspect_manager_schema(
            "Part", view="detail", types=["Part"], snapshot=overview["snapshot"]
        )
    selected = inspect_manager_schema(
        "Part",
        view="detail",
        types=["Part", "State", "PartFilter"],
        snapshot=fresh["snapshot"],
    )
    full = inspect_manager_schema("Part", view="full")
    assert selected["types"] == {
        name: full["types"][name] for name in selected["types"]
    }
    assert selected["types"]["Part"]["fields"]["code"]["type"] == "Int!"
    assert selected["types"]["State"]["values"] == ["OPEN", "CLOSED", "NEW"]


def test_real_graphene_capture_retains_concrete_named_types():
    from tests.experiments.test_gm_eval_harness import _child

    report = _child("""
import json
from experiments.gm_eval.runtime import bootstrap
runtime = bootstrap(manager_count=5)
from general_manager.chat.graphql_contract import runtime_schema, _copy_contract_schema, capture_manager_contract
original = runtime_schema()
copy = _copy_contract_schema(original)
for name, named in original.type_map.items():
    frozen = copy.get_type(name)
    assert type(frozen) is type(named), name
    assert frozen is not named, name
    assert getattr(frozen, 'graphene_type', None) is getattr(named, 'graphene_type', None), name
full, token = capture_manager_contract('Customer')
assert full['types'][full['type']]['kind'] == 'object'
print(json.dumps({'concrete_types': len(original.type_map), 'snapshot': token}))
""")
    assert report["concrete_types"] > 0
    print("REAL_GRAPHENE_NAMED_TYPES", report["concrete_types"])


@pytest.mark.parametrize(
    "location", ["field", "argument", "input_field", "enum_value", "resolver"]
)
def test_irrelevant_metadata_is_omitted_without_changing_contract(
    exposed_schema, location
):
    schema, _ = exposed_schema
    before = inspect_manager_schema("Part", view="full")
    target = {
        "field": schema.get_type("Part").fields["code"],
        "argument": schema.query_type.fields["partList"].args["pageSize"],
        "input_field": schema.get_type("PartFilter").fields["code"],
        "enum_value": schema.get_type("State").values["OPEN"],
    }.get(location)
    if location == "resolver":

        class Resolver:
            def __init__(self):
                self.lock = RLock()

            def resolve(self, root, info):
                return "P01"

        schema.get_type("Part").fields["code"].resolve = Resolver().resolve
    else:
        target.extensions["application_lock"] = RLock()
    assert_valid_schema(schema)
    observed = inspect_manager_schema("Part", view="full")
    assert observed == before
