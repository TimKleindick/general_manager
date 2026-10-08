"""The chat read contract must execute the names it advertises."""

from __future__ import annotations

from types import SimpleNamespace
import json

import graphene
import pytest
from graphql import GraphQLError
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.schema_index import build_schema_index, find_exposed_path
from general_manager.chat.tools import get_manager_schema, query


@pytest.fixture
def native_schema():
    GraphQL.reset_registry()
    calls = []

    class MaterialFilter(graphene.InputObjectType):
        code = graphene.String()
        is_active = graphene.Boolean()

    class RelatedFilter(graphene.InputObjectType):
        any = graphene.InputField(MaterialFilter)

    class ProjectFilter(graphene.InputObjectType):
        materials_list = graphene.InputField(RelatedFilter)

    class PageInfo(graphene.ObjectType):
        total_count = graphene.Int(required=True)

    class Secret(graphene.ObjectType):
        value = graphene.String()

    class Material(graphene.ObjectType):
        code = graphene.String(required=True)
        is_active = graphene.Boolean()
        density_g_cm3 = graphene.Float()
        shipped_at = graphene.Date()
        exact_name = graphene.String(name="custom_spelling")
        secret = graphene.Field(Secret)
        label = graphene.String(target_unit=graphene.String(default_value="kg"))

        @staticmethod
        def resolve_label(root, info, target_unit):
            return target_unit

    class MaterialPage(graphene.ObjectType):
        items = graphene.List(Material, required=True)
        page_info = graphene.Field(PageInfo, required=True)

    rows = [
        dict(code=f"M{i}", is_active=True, density_g_cm3=7.8, exact_name="exact")
        for i in range(3)
    ]

    class ProjectCommercial(graphene.ObjectType):
        code = graphene.String()
        materials_list = graphene.Field(
            MaterialPage, page_size=graphene.Int(), filter=MaterialFilter()
        )

        @staticmethod
        def resolve_materials_list(root, info, page_size=2, filter=None):
            calls.append(("nested", page_size, filter))
            return {"items": rows[:page_size], "page_info": {"total_count": 3}}

    class ProjectPage(graphene.ObjectType):
        items = graphene.List(ProjectCommercial, required=True)
        page_info = graphene.Field(PageInfo, required=True)

    class Query(graphene.ObjectType):
        material_list = graphene.Field(
            MaterialPage,
            page_size=graphene.Int(),
            page=graphene.Int(),
            filter=MaterialFilter(),
        )
        project_commercial_list = graphene.Field(
            ProjectPage,
            page_size=graphene.Int(),
            filter=ProjectFilter(),
            name="actualProjects",
        )

        @staticmethod
        def resolve_material_list(root, info, page_size=100, page=1, filter=None):
            if getattr(info.context, "deny_read", False):
                message = "read denied"
                raise GraphQLError(message)
            calls.append(("root", page_size, filter, info.context))
            return {
                "items": rows[(page - 1) * page_size : page * page_size],
                "page_info": {"total_count": 3},
            }

        @staticmethod
        def resolve_project_commercial_list(root, info, page_size=100, filter=None):
            calls.append(("project", page_size, filter))
            return {"items": [{"code": "P1"}], "page_info": {"total_count": 1}}

    GraphQL.manager_registry = {
        name: SimpleNamespace(chat_exposed=name != "Secret", __doc__=f"Domain {name}")
        for name in ("Material", "ProjectCommercial", "Secret")
    }
    GraphQL.graphql_type_registry = {
        "Material": Material,
        "ProjectCommercial": ProjectCommercial,
        "Secret": Secret,
    }
    GraphQL._schema = graphene.Schema(query=Query)
    yield SimpleNamespace(
        calls=calls,
        query_type=Query,
        Material=Material,
        ProjectCommercial=ProjectCommercial,
    )
    GraphQL.reset_registry()


def test_schema_exposes_runtime_names_types_and_custom_root(native_schema):
    summary = get_manager_schema("Material")
    assert summary["contract_version"] == 2
    assert "isActive" in summary["fields"]
    assert "custom_spelling" in summary["fields"]
    assert "is_active" not in summary["fields"]
    assert "secret" not in summary["relations"]
    assert summary["types"]["Material"]["fields"]["code"]["type"] == "String!"
    assert (
        summary["types"]["Material"]["fields"]["label"]["arguments"]["targetUnit"][
            "default"
        ]
        == "kg"
    )
    assert get_manager_schema("ProjectCommercial")["roots"] == ["actualProjects"]


def test_native_fields_and_filters_execute_without_aliases(native_schema):
    context = SimpleNamespace(user="alice")
    result = query(
        manager="Material",
        filters={"isActive": True},
        fields=["code", "isActive", "densityGCm3", "custom_spelling"],
        limit=2,
        context=context,
    )
    assert result["data"][0] == {
        "code": "M0",
        "isActive": True,
        "densityGCm3": 7.8,
        "custom_spelling": "exact",
    }
    assert result["has_more"] is True
    assert native_schema.calls[-1] == ("root", 2, {"is_active": True}, context)


def test_custom_root_nested_filter_wrapper_and_arguments(native_schema):
    result = query(
        manager="ProjectCommercial",
        filters={"materialsList": {"any": {"code": "M0"}}},
        fields=[
            "code",
            {
                "field": "materialsList",
                "arguments": {"pageSize": 1},
                "fields": [
                    {
                        "items": [
                            "code",
                            {"field": "label", "arguments": {"targetUnit": "g"}},
                        ]
                    },
                    {"pageInfo": ["totalCount"]},
                ],
            },
        ],
    )
    assert result["data"][0]["materialsList"] == {
        "items": [{"code": "M0", "label": "g"}],
        "pageInfo": {"totalCount": 3},
    }
    assert native_schema.calls[0][2] == {"materials_list": {"any": {"code": "M0"}}}


def test_paths_include_real_directed_wrapper_edges(native_schema):
    assert find_exposed_path("ProjectCommercial", "Material") == [
        "materialsList",
        "items",
    ]
    assert find_exposed_path("Material", "ProjectCommercial") is None


@pytest.mark.parametrize(
    "fields,filters",
    [
        (["is_active"], {}),
        (["madeUp"], {}),
        (["code"], {"isActive": "yes"}),
        ([{"secret": ["value"]}], {}),
        (["code"], {"is_active": True}),
    ],
)
def test_invalid_or_hidden_requests_never_reach_resolver(
    native_schema, fields, filters
):
    with pytest.raises(ValueError):
        query(manager="Material", filters=filters, fields=fields)
    assert native_schema.calls == []


def test_runtime_schema_replacement_changes_names_without_manual_cache_clear(
    native_schema,
):
    first = get_manager_schema("Material")
    GraphQL._schema = graphene.Schema(
        query=native_schema.query_type, auto_camelcase=False
    )
    second = get_manager_schema("Material")
    assert "isActive" in first["fields"] and "is_active" in second["fields"]
    result = query(
        manager="Material", fields=["is_active"], filters={"is_active": True}
    )
    assert result["data"][0]["is_active"] is True


def test_wildcard_only_selects_leaf_fields(native_schema):
    result = query(manager="Material", fields=["*"], filters={})
    assert len(result["data"]) == 3
    assert "secret" not in result["data"][0]


def test_compact_index_does_not_embed_expanded_types(native_schema):
    index = build_schema_index()
    assert all("types" not in summary for summary in index.values())
    assert len(json.dumps(index)) < 4000


@override_settings(GENERAL_MANAGER={"CHAT": {"max_results": 2}})
def test_result_caps_apply_to_nested_pages(native_schema):
    result = query(
        manager="ProjectCommercial",
        filters={},
        fields=[
            {
                "field": "materialsList",
                "arguments": {"pageSize": 1000},
                "fields": [{"items": ["code"]}, {"pageInfo": ["totalCount"]}],
            }
        ],
    )
    assert len(result["data"][0]["materialsList"]["items"]) == 2
    assert result["complete"] is False


def test_http_and_websocket_share_compact_planner_projection(native_schema):
    from general_manager.chat.consumer import ChatConsumer
    from general_manager.chat.views import _planned_catalog_summary

    settings = SimpleNamespace(catalog_source=None)
    http = _planned_catalog_summary(settings)
    websocket = ChatConsumer._planned_catalog_summary(settings)
    assert http == websocket
    assert all(
        "fields" not in item and "filters" not in item and "types" not in item
        for item in http["schema"].values()
    )
    assert http["contract_version"] == 2


def test_native_root_page_metadata_does_not_claim_more_or_complete(native_schema):
    result = query(
        manager="Material",
        filters={},
        fields=["code"],
        arguments={"page": 2, "pageSize": 2},
    )
    assert result["data"] == [{"code": "M2"}]
    assert result["has_more"] is False
    assert result["complete"] is False


def test_legacy_names_are_not_recovered_by_an_alias(native_schema):
    with pytest.raises(ValueError, match="contract 2"):
        query(manager="Material", filters={}, fields=["density_g_cm3"])
    assert native_schema.calls == []


def test_schema_argument_enum_defaults_are_json_values(native_schema):
    class Direction(graphene.Enum):
        ASC = "ASC"
        DESC = "DESC"

    class Query(graphene.ObjectType):
        materials = graphene.Field(
            native_schema.query_type._meta.fields["material_list"].type,
            direction=Direction(default_value=Direction.ASC),
        )

    GraphQL._schema = graphene.Schema(query=Query)
    summary = get_manager_schema("Material")
    assert (
        summary["root_fields"]["materials"]["arguments"]["direction"]["default"]
        == "ASC"
    )
    json.dumps(summary)


def test_native_execution_preserves_resolver_permissions(native_schema):
    with pytest.raises(ValueError, match="read denied"):
        query(
            manager="Material",
            filters={},
            fields=["code"],
            context=SimpleNamespace(deny_read=True),
        )
    assert native_schema.calls == []


def test_native_capabilities_include_exact_argument_names_and_type_defaults(
    native_schema,
):
    summary = get_manager_schema("Material")
    assert (
        summary["root_fields"]["materialList"]["arguments"]["pageSize"]["type"] == "Int"
    )
    with pytest.raises(ValueError, match="Unknown GraphQL argument"):
        query(
            manager="Material", filters={}, fields=["code"], arguments={"page_size": 1}
        )
    with pytest.raises(ValueError, match="Root is not"):
        query(manager="Material", filters={}, fields=["code"], root="actualProjects")
    assert native_schema.calls == []


def test_custom_pagination_and_filter_argument_names_use_runtime_bindings(
    native_schema,
):
    original = native_schema.query_type._meta.fields["material_list"]

    class Query(graphene.ObjectType):
        renamed = graphene.Field(
            original.type,
            page_size=graphene.Int(name="takeRecords"),
            filter=graphene.Argument(original.args["filter"].type, name="whereExact"),
        )
        resolve_renamed = native_schema.query_type.resolve_material_list

    GraphQL._schema = graphene.Schema(query=Query)
    summary = get_manager_schema("Material")
    assert "isActive" in summary["filters"]
    result = query(
        manager="Material", filters={"isActive": True}, fields=["code"], limit=1
    )
    assert result["data"] == [{"code": "M0"}]
    assert native_schema.calls[-1][:3] == ("root", 1, {"is_active": True})


@pytest.mark.parametrize("custom_names", [False, True])
@pytest.mark.parametrize("cap", [1, 2, 100, None])
def test_native_pagination_defaults_match_explicit_arguments(
    native_schema, custom_names, cap
):
    original = native_schema.query_type._meta.fields["material_list"]
    page_name = "startingPage" if custom_names else "page"
    size_name = "takeRecords" if custom_names else "pageSize"

    class Query(graphene.ObjectType):
        materials = graphene.Field(
            original.type,
            page=graphene.Int(default_value=2, name=page_name),
            page_size=graphene.Int(default_value=2, name=size_name),
        )
        resolve_materials = native_schema.query_type.resolve_material_list

    GraphQL._schema = graphene.Schema(query=Query)
    with override_settings(GENERAL_MANAGER={"CHAT": {"max_results": cap}}):
        omitted = query(manager="Material", filters={}, fields=["code"])
        explicit = query(
            manager="Material",
            filters={},
            fields=["code"],
            arguments={page_name: 2, size_name: 2},
        )
    assert omitted == explicit
    assert omitted["data"] == [{"code": "M1" if cap == 1 else "M2"}]
    assert omitted["has_more"] is (cap == 1)
    assert omitted["complete"] is False


def test_nested_page_preserves_native_page_size_default(native_schema):
    project = native_schema.ProjectCommercial
    project._meta.fields["materials_list"].args["page_size"].default_value = 1
    GraphQL._schema = graphene.Schema(query=native_schema.query_type)
    result = query(
        manager="ProjectCommercial",
        filters={},
        fields=[{"materialsList": [{"items": ["code"]}]}],
    )
    assert result["data"][0]["materialsList"]["items"] == [{"code": "M0"}]
    assert result["complete"] is False


@pytest.mark.parametrize("input_default", [False, True])
@pytest.mark.parametrize("list_wrapper", [False, True])
def test_hidden_input_defaults_cannot_bypass_chat_exposure(
    native_schema, input_default, list_wrapper
):
    class SecretFilter(graphene.InputObjectType):
        value = graphene.String()

    SecretFilter._general_manager_filter_owner = GraphQL.manager_registry["Secret"]

    class Filter(graphene.InputObjectType):
        secret_filter = graphene.InputField(SecretFilter, name="privateChoice")

    if input_default:
        Filter._meta.fields["secret_filter"].default_value = {"value": "hidden"}

    default = {} if input_default else {"secret_filter": {"value": "hidden"}}

    class Query(graphene.ObjectType):
        materials = graphene.Field(
            native_schema.query_type._meta.fields["material_list"].type,
            page_size=graphene.Int(),
            filter=graphene.Argument(
                graphene.List(Filter) if list_wrapper else Filter,
                default_value=(default,) if list_wrapper else default,
                name="whereExact",
            ),
        )
        resolve_materials = native_schema.query_type.resolve_material_list

    GraphQL._schema = graphene.Schema(query=Query)
    assert (
        "whereExact"
        not in get_manager_schema("Material")["root_fields"]["materials"]["arguments"]
    )
    with pytest.raises(ValueError, match="chat-exposed"):
        query(manager="Material", filters={}, fields=["code"])
    assert native_schema.calls == []


def test_advertised_root_selection_example_executes_native_rows(native_schema):
    from general_manager.chat.tool_metadata import (
        TOOL_INPUT_SCHEMAS,
        READ_TOOL_GUIDANCE,
    )

    fields = TOOL_INPUT_SCHEMAS["query"]["properties"]["fields"]
    assert "root" in fields["description"] and "row" in fields["description"]
    assert "root" in READ_TOOL_GUIDANCE and "adapter" in READ_TOOL_GUIDANCE
    result = query(manager="Material", filters={}, fields=fields["examples"][0])
    assert result["data"] == [{"code": "M0"}, {"code": "M1"}, {"code": "M2"}]


@override_settings(GENERAL_MANAGER={"CHAT": {"max_results": 2}})
def test_large_offset_uses_bounded_native_pages(native_schema):
    result = query(
        manager="Material", filters={}, fields=["code"], limit=2, offset=1_000_000
    )
    assert result["data"] == []
    assert result["complete"] is False
    assert all(call[1] <= 2 for call in native_schema.calls)
    assert len(native_schema.calls) <= 2


@override_settings(GENERAL_MANAGER={"CHAT": {"max_results": 2}})
def test_unaligned_offset_collects_requested_window_with_bounded_pages(native_schema):
    result = query(manager="Material", filters={}, fields=["code"], limit=2, offset=1)
    assert result["data"] == [{"code": "M1"}, {"code": "M2"}]
    assert result["total_count"] == 3
    assert result["has_more"] is False
    assert result["complete"] is False
    assert all(call[1] <= 2 for call in native_schema.calls)
    assert len(native_schema.calls) == 2


def test_unknown_total_count_is_conservative_not_a_tool_failure(
    native_schema, monkeypatch
):
    schema = GraphQL.get_schema()
    execute = schema.execute

    def unknown_count(*args, **kwargs):
        result = execute(*args, **kwargs)
        result.data["materialList"]["pageInfo"]["totalCount"] = None
        return result

    monkeypatch.setattr(schema, "execute", unknown_count)
    result = query(manager="Material", filters={}, fields=["code"], limit=2)
    assert result["data"] == [{"code": "M0"}, {"code": "M1"}]
    assert result["total_count"] is None
    assert result["has_more"] is None
    assert result["complete"] is False
