"""Selective schema detail preserves executable definitions and exposure."""

from types import SimpleNamespace

import pytest
from graphql import build_schema, GraphQLEnumValue

from general_manager.api.graphql import GraphQL
from general_manager.chat import graphql_contract as contract
from general_manager.chat.schema_inspection import (
    inspect_manager_schema,
    SchemaInspectionError,
)


@pytest.fixture
def exposed_schema(monkeypatch):
    schema = build_schema("""
      enum State { OPEN CLOSED }
      input NestedFilter { state: State = OPEN, next: NestedFilter }
      input OtherFilter { code: String }
      input PartFilter { code: String, nested: NestedFilter, other: OtherFilter }
      union Mystery = Part | Other
      type Part { code: String!, state: State, other: Other, mystery: Mystery }
      type Other { secret: String }
      type Info { totalCount: Int }
      type PartPage { items: [Part!]!, pageInfo: Info! }
      type OtherPage { items: [Other!]!, pageInfo: Info! }
      type Query { partList(filter: PartFilter, pageSize: Int = 25): PartPage,
                   otherList: OtherPage }
    """)

    class Part:
        chat_exposed = True

    class Other:
        chat_exposed = True

    class PartType:
        pass

    class OtherType:
        pass

    schema.get_type("Part").graphene_type = PartType
    schema.get_type("Other").graphene_type = OtherType
    schema.get_type("OtherFilter").graphene_type = SimpleNamespace(
        _general_manager_filter_owner=Other
    )
    monkeypatch.setattr(GraphQL, "manager_registry", {"Part": Part, "Other": Other})
    monkeypatch.setattr(
        GraphQL, "graphql_type_registry", {"Part": PartType, "Other": OtherType}
    )
    monkeypatch.setattr(
        GraphQL, "get_schema", lambda: SimpleNamespace(graphql_schema=schema)
    )
    contract.clear_contract_cache()
    yield schema, Other
    contract.clear_contract_cache()


def test_overview_and_selected_detail_exactly_match_full(exposed_schema):
    full = contract.manager_schema("Part")
    overview = inspect_manager_schema("Part")
    assert overview["schema_view"] == "overview"
    assert overview["schema_complete"] is False
    assert overview["root_fields"] == full["root_fields"]
    assert overview["output_fields"] == full["types"]["Part"]["fields"]
    assert set(overview["type_manifest"]) < set(full["types"])
    assert "types" not in overview
    names = [
        n
        for n, v in overview["type_manifest"].items()
        if v["kind"] in {"input", "enum"}
    ]
    detail = inspect_manager_schema(
        "Part", view="detail", types=names, snapshot=overview["snapshot"]
    )
    assert detail["types"] == {n: full["types"][n] for n in sorted(names)}
    assert detail["schema_complete"] is False
    assert detail["snapshot"] == overview["snapshot"]
    assert detail["type_manifest"]["NestedFilter"] == {"kind": "input"}
    nested = inspect_manager_schema(
        "Part", view="detail", types=["NestedFilter"], snapshot=detail["snapshot"]
    )
    assert nested["types"]["NestedFilter"] == full["types"]["NestedFilter"]
    assert nested["types"]["NestedFilter"]["fields"]["state"]["default"] == "OPEN"
    explicit_full = inspect_manager_schema("Part", view="full")
    assert explicit_full["schema_complete"] is True
    assert all(explicit_full[k] == value for k, value in full.items())


def test_related_manager_remains_a_reference(exposed_schema):
    overview = inspect_manager_schema("Part")
    assert overview["type_manifest"]["Other"] == {
        "kind": "reference",
        "manager": "Other",
    }
    detail = inspect_manager_schema(
        "Part", view="detail", types=["Other"], snapshot=overview["snapshot"]
    )
    assert detail["types"]["Other"] == {"kind": "reference", "manager": "Other"}
    other = inspect_manager_schema("Other")
    assert other["output_fields"]["secret"] == {"type": "String"}
    with pytest.raises(SchemaInspectionError):
        inspect_manager_schema(
            "Other", view="detail", types=["Other"], snapshot=overview["snapshot"]
        )


@pytest.mark.parametrize("change", ["enum", "default", "exposure"])
def test_schema_changes_invalidate_details_without_cache_bypass(exposed_schema, change):
    schema, other = exposed_schema
    old = inspect_manager_schema("Part")
    if change == "enum":
        schema.get_type("State").values["NEW"] = GraphQLEnumValue("NEW")
    elif change == "default":
        schema.query_type.fields["partList"].args["pageSize"].default_value = 7
    else:
        other.chat_exposed = False
    with pytest.raises(SchemaInspectionError, match="schema_snapshot_mismatch"):
        inspect_manager_schema(
            "Part", view="detail", types=["Part"], snapshot=old["snapshot"]
        )
    fresh = inspect_manager_schema("Part")
    assert fresh["snapshot"] != old["snapshot"]
    if change == "exposure":
        assert "Other" not in fresh["type_manifest"]
        assert "other" not in fresh["output_fields"]
        assert inspect_manager_schema("Other") is None


@pytest.mark.parametrize(
    "names", [[], ["Part", "NotExposed"], ["Part", "Part"], "Part", [True]]
)
def test_invalid_selectors_are_atomic(exposed_schema, names):
    overview = inspect_manager_schema("Part")
    with pytest.raises(SchemaInspectionError):
        inspect_manager_schema(
            "Part", view="detail", types=names, snapshot=overview["snapshot"]
        )


def test_public_defaults_are_explicit_and_python_helper_keeps_full(exposed_schema):
    from general_manager.chat.tools import get_manager_schema, execute_chat_tool

    full = contract.manager_schema("Part")
    assert get_manager_schema("Part") == full
    assert (
        execute_chat_tool("get_manager_schema", {"manager": "Part"}, None)[
            "schema_view"
        ]
        == "overview"
    )
    assert get_manager_schema("Part", view="overview")["schema_view"] == "overview"
    error = execute_chat_tool(
        "get_manager_schema",
        {"manager": "Part", "view": "detail", "types": ["Part"], "snapshot": "stale"},
        None,
    )
    assert error["status"] == "error"
    assert "types" not in error


def test_every_exposed_type_reference_is_loadable_reference_or_unsupported(
    exposed_schema,
):
    import re

    overview = inspect_manager_schema("Part")
    full = inspect_manager_schema("Part", view="full")
    for name, definition in full["types"].items():
        assert definition["kind"] in {
            "input",
            "object",
            "enum",
            "scalar",
            "reference",
            "unsupported",
        }
        selected = inspect_manager_schema(
            "Part", view="detail", types=[name], snapshot=overview["snapshot"]
        )
        assert selected["types"][name] == definition
        assert selected["type_manifest"][name]["kind"] == definition["kind"]
        for field in definition.get("fields", {}).values():
            refs = [
                field["type"],
                *[a["type"] for a in field.get("arguments", {}).values()],
            ]
            assert all(
                re.sub(r"[\[\]!]", "", ref) in selected["type_manifest"] for ref in refs
            )
    assert overview["type_manifest"]["Mystery"] == {"kind": "unsupported"}
    assert full["types"]["Mystery"] == {"kind": "unsupported"}


def test_capture_freezes_exposure_definitions_and_defaults_before_serializing(
    exposed_schema, monkeypatch
):
    schema, other = exposed_schema
    original = contract._argument_info
    triggered = False

    def mutate_during_serialization(argument):
        nonlocal triggered
        if not triggered:
            triggered = True
            other.chat_exposed = False
            schema.query_type.fields["partList"].args["pageSize"].default_value = 9
            schema.get_type("State").values["LATER"] = GraphQLEnumValue("LATER")
        return original(argument)

    monkeypatch.setattr(contract, "_argument_info", mutate_during_serialization)
    observed = inspect_manager_schema("Part", view="full")
    assert observed["root_fields"]["partList"]["arguments"]["pageSize"]["default"] == 25
    assert "Other" in observed["types"] and "OtherFilter" in observed["types"]
    assert observed["types"]["State"]["values"] == ["OPEN", "CLOSED"]
    fresh = inspect_manager_schema("Part", view="full")
    assert fresh["snapshot"] != observed["snapshot"]
    assert fresh["root_fields"]["partList"]["arguments"]["pageSize"]["default"] == 9
    assert "Other" not in fresh["types"] and "OtherFilter" not in fresh["types"]


@pytest.mark.parametrize(
    "args",
    [
        {"view": "overview", "types": ["Part"]},
        {"view": "full", "snapshot": "a" * 64},
        {"types": None},
        {"snapshot": None},
        {"view": None},
        {"unknown": True},
        {"view": "detail", "types": ["Part"]},
        {"view": "detail", "types": ["Part", "Unknown"], "snapshot": "a" * 64},
    ],
)
def test_dispatch_rejects_invalid_selectors_without_full_or_partial_fallback(
    exposed_schema, args
):
    from general_manager.chat.tools import execute_chat_tool

    result = execute_chat_tool("get_manager_schema", {"manager": "Part", **args}, None)
    assert result["status"] == "error"
    assert set(result) == {"status", "code"}
