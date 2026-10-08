"""Targeted hidden-enum variant of the irrelevant-copy regression."""

import importlib.util
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest
from graphql import (
    GraphQLArgument,
    GraphQLEnumType,
    GraphQLEnumValue,
    GraphQLField,
    GraphQLScalarType,
    GraphQLSchema,
    assert_valid_schema,
    graphql_sync,
)

from general_manager.api.graphql import GraphQL
from general_manager.chat.schema_inspection import inspect_manager_schema
from tests.unit.test_schema_inspection import exposed_schema as _exposed_schema

exposed_schema = _exposed_schema


@pytest.mark.parametrize("internal_value", ["enum_value", "argument_default"])
def test_hidden_manager_enum_internal_value_does_not_break_visible_schema(
    exposed_schema, monkeypatch, internal_value
):
    original, other = exposed_schema
    other.chat_exposed = False
    marker = RLock()
    if internal_value == "enum_value":
        private_type = GraphQLEnumType(
            "InternalState", {"OPEN": GraphQLEnumValue(marker)}
        )
        original.get_type("Other").fields["state"] = GraphQLField(private_type)
        assert private_type.serialize(marker) == "OPEN"
    else:
        private_type = GraphQLScalarType(
            "InternalState", serialize=lambda _value: "opaque"
        )
        original.get_type("Other").fields["state"] = GraphQLField(
            private_type,
            args={"config": GraphQLArgument(private_type, default_value=marker)},
            resolve=lambda _obj, _info, **args: args["config"],
        )
    schema = GraphQLSchema(
        query=original.query_type, types=[*original.type_map.values(), private_type]
    )
    monkeypatch.setattr(
        GraphQL, "get_schema", lambda: SimpleNamespace(graphql_schema=schema)
    )
    assert_valid_schema(schema)
    result = graphql_sync(
        schema,
        "{ partList { items { code } } }",
        root_value={"partList": {"items": [{"code": "P01"}]}},
    )
    assert result.errors is None
    private_result = graphql_sync(
        schema,
        "{ otherList { items { state } } }",
        root_value={"otherList": {"items": [{"state": marker}]}},
    )
    assert private_result.errors is None
    assert private_result.data["otherList"]["items"][0]["state"] == (
        "OPEN" if internal_value == "enum_value" else "opaque"
    )
    path = Path(
        "/Users/tim/Documents/Codex/2026-10-03/task-4/verification/v26-working-source/src/general_manager/chat/graphql_contract.py"
    )
    spec = importlib.util.spec_from_file_location("reviewer_enum_baseline_v26", path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    old = baseline.manager_schema("Part")
    assert "Other" not in old["types"] and "InternalState" not in old["types"]
    print(
        "VALID_GRAPHQL_QUERIES_AND_V26_PASS; hidden type absent from visible contract",
        internal_value,
    )
    observed = inspect_manager_schema("Part")
    assert observed["schema_view"] == "overview"
    assert "InternalState" not in observed["type_manifest"]


@pytest.mark.parametrize("scope", ["hidden", "reference", "unreachable"])
def test_unobserved_default_serializer_is_never_called(
    exposed_schema, monkeypatch, scope
):
    original, other = exposed_schema
    other.chat_exposed = scope == "reference"
    calls = []

    def private_serializer(value):
        calls.append(value)
        message = "unobserved private serializer executed"
        raise AssertionError(message)

    private_type = GraphQLScalarType("PrivateConfig", serialize=private_serializer)
    original.get_type("Other").fields["state"] = GraphQLField(
        private_type,
        args={"config": GraphQLArgument(private_type, default_value=RLock())},
    )
    if scope == "unreachable":
        original.query_type.fields.pop("otherList")
        original.get_type("Part").fields.pop("other")
        # The standalone type stays registered but has no visible reachability.
    schema = GraphQLSchema(
        query=original.query_type, types=[*original.type_map.values(), private_type]
    )
    monkeypatch.setattr(
        GraphQL, "get_schema", lambda: SimpleNamespace(graphql_schema=schema)
    )
    assert_valid_schema(schema)
    overview = inspect_manager_schema("Part")
    assert "PrivateConfig" not in overview["type_manifest"]
    assert calls == []
    if scope == "reference":
        assert overview["type_manifest"]["Other"] == {
            "kind": "reference",
            "manager": "Other",
        }


def test_visible_opaque_enum_default_uses_native_name_and_snapshot(
    exposed_schema, monkeypatch
):
    original, _ = exposed_schema
    opened, closed = RLock(), RLock()
    state = GraphQLEnumType(
        "OpaqueState",
        {"OPEN": GraphQLEnumValue(opened), "CLOSED": GraphQLEnumValue(closed)},
    )
    original.get_type("Part").fields["opaqueState"] = GraphQLField(state)
    argument = GraphQLArgument(state, default_value=opened)
    original.query_type.fields["partList"].args["state"] = argument
    schema = GraphQLSchema(
        query=original.query_type, types=[*original.type_map.values(), state]
    )
    monkeypatch.setattr(
        GraphQL, "get_schema", lambda: SimpleNamespace(graphql_schema=schema)
    )
    assert_valid_schema(schema)
    assert state.serialize(opened) == "OPEN"
    full = inspect_manager_schema("Part", view="full")
    assert full["types"]["OpaqueState"] == {
        "kind": "enum",
        "values": ["OPEN", "CLOSED"],
    }
    assert full["root_fields"]["partList"]["arguments"]["state"] == {
        "type": "OpaqueState",
        "default": "OPEN",
        "default_graphql": "OPEN",
    }
    argument.default_value = closed
    fresh = inspect_manager_schema("Part", view="full")
    assert fresh["snapshot"] != full["snapshot"]
    assert fresh["root_fields"]["partList"]["arguments"]["state"]["default"] == "CLOSED"
    state.values["LATER"] = GraphQLEnumValue(RLock())
    assert inspect_manager_schema("Part")["snapshot"] != fresh["snapshot"]
    from general_manager.chat.schema_inspection import SchemaInspectionError

    with pytest.raises(SchemaInspectionError, match="schema_snapshot_mismatch"):
        inspect_manager_schema(
            "Part", view="detail", types=["OpaqueState"], snapshot=full["snapshot"]
        )


def test_visible_opaque_scalar_default_is_serialized_without_string_coercion(
    exposed_schema, monkeypatch
):
    original, _ = exposed_schema
    marker = RLock()
    scalar = GraphQLScalarType(
        "OpaqueConfig",
        serialize=lambda value: "opaque" if value is marker else "wrong_identity",
    )
    original.query_type.fields["partList"].args["config"] = GraphQLArgument(
        scalar, default_value=marker
    )
    schema = GraphQLSchema(
        query=original.query_type, types=[*original.type_map.values(), scalar]
    )
    monkeypatch.setattr(
        GraphQL, "get_schema", lambda: SimpleNamespace(graphql_schema=schema)
    )
    full = inspect_manager_schema("Part", view="full")
    default = full["root_fields"]["partList"]["arguments"]["config"]
    assert default == {
        "type": "OpaqueConfig",
        "default": "opaque",
        "default_graphql": '"opaque"',
    }
    scalar.serialize = lambda value: "changed" if value is marker else "wrong_identity"
    fresh = inspect_manager_schema("Part", view="full")
    assert fresh["snapshot"] != full["snapshot"]
    assert (
        fresh["root_fields"]["partList"]["arguments"]["config"]["default"] == "changed"
    )


def test_wrapper_metadata_is_not_observable_contract_data(exposed_schema):
    original, _ = exposed_schema
    before = inspect_manager_schema("Part", view="full")
    original.query_type.fields["partList"].type.application_lock = RLock()
    original.get_type("Part").fields["code"].type.application_lock = RLock()
    assert inspect_manager_schema("Part", view="full") == before
