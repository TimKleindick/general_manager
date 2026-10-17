"""Unmodeled recursive group outputs cannot cross the chat contract boundary."""

from types import SimpleNamespace

from graphql import build_schema, graphql_sync
import pytest

from general_manager.api.graphql import GraphQL
from general_manager.chat import graphql_contract as contract


@pytest.fixture(params=(False, True), ids=("hidden-material", "exposed-material"))
def group_schema(request, monkeypatch):
    schema = build_schema("""
        type MaterialType { code: String! }
        type ProjectType {
            code: String!, material: MaterialType,
            materialsGroups: MaterialGroupPage, ownGroups: ProjectGroupPage,
            directGroups: [MaterialGroupType!], wrappedGroups: CustomGroupPage
        }
        type MaterialGroupType { code: String!, amount: Float! }
        type ProjectGroupType { code: String! }
        type MaterialGroupPage { items: [MaterialGroupType!]!, pageInfo: PageInfo! }
        type ProjectGroupPage { items: [ProjectGroupType!]!, pageInfo: PageInfo! }
        type CustomGroupPage { items: [MaterialGroupType!]!, pageInfo: PageInfo! }
        type MaterialPage { items: [MaterialType!]!, pageInfo: PageInfo! }
        type ProjectPage { items: [ProjectType!]!, pageInfo: PageInfo! }
        type PageInfo { totalCount: Int! }
        type Query { projectList: ProjectPage, materialList: MaterialPage }
    """)
    managers = {
        "Project": type("Project", (), {"chat_exposed": True}),
        "Material": type("Material", (), {"chat_exposed": request.param}),
    }
    entity_types = {name: type(name + "Entity", (), {}) for name in managers}
    for name, source in entity_types.items():
        schema.get_type(name + "Type").graphene_type = source
        for suffix in ("GroupType", "GroupPage"):
            schema.get_type(name + suffix).graphene_type = type(
                name + suffix,
                (),
                {"_general_manager_group_owner": managers[name]},
            )
    monkeypatch.setattr(GraphQL, "manager_registry", managers)
    monkeypatch.setattr(GraphQL, "graphql_type_registry", entity_types)
    monkeypatch.setattr(
        GraphQL, "get_schema", lambda: SimpleNamespace(graphql_schema=schema)
    )
    contract.clear_contract_cache()
    yield schema, managers
    contract.clear_contract_cache()


def test_recursive_group_outputs_are_not_advertised_or_expanded(group_schema):
    _, managers = group_schema
    exported, _ = contract.capture_manager_contract("Project")
    fields = exported["types"]["ProjectType"]["fields"]
    assert {"materialsGroups", "ownGroups", "directGroups", "wrappedGroups"}.isdisjoint(
        fields
    )
    assert "native recursive grouped outputs" in exported["unsupported"]
    assert all("Group" not in name for name in exported["types"])
    assert ("material" in fields) == managers["Material"].chat_exposed
    if managers["Material"].chat_exposed:
        assert exported["types"]["MaterialType"] == {
            "kind": "reference",
            "manager": "Material",
        }
    summary = contract.compact_index()["Project"]
    assert {"materialsGroups", "ownGroups", "directGroups", "wrappedGroups"}.isdisjoint(
        summary["fields"]
    )


@pytest.mark.parametrize(
    "field", ("materialsGroups", "ownGroups", "directGroups", "wrappedGroups")
)
def test_recursive_group_selection_fails_before_any_native_execution(
    group_schema, field
):
    schema, _ = group_schema
    executions = []
    schema.query_type.fields["projectList"].resolve = (
        lambda *_args, **_kwargs: executions.append(True)
    )
    nested = ["code"] if field == "directGroups" else [{"items": ["code"]}]
    compiler = contract.ReadCompiler(schema, max_results=25)
    with pytest.raises(
        contract.ChatReadContractError, match="Unsupported native recursive grouped"
    ):
        compiler.selection(schema.get_type("ProjectType"), ["code", {field: nested}])
    assert executions == []


def test_chat_exclusion_does_not_remove_native_group_fields_or_queries(group_schema):
    schema, _ = group_schema
    schema.query_type.fields["projectList"].resolve = lambda *_args, **_kwargs: {
        "items": [
            {
                "code": "P",
                "materialsGroups": {
                    "items": [{"code": "M", "amount": 3.5}],
                    "pageInfo": {"totalCount": 1},
                },
            }
        ],
        "pageInfo": {"totalCount": 1},
    }
    result = graphql_sync(
        schema,
        "{ projectList { items { materialsGroups { items { code amount } } } } }",
    )
    assert result.errors is None
    assert result.data["projectList"]["items"][0]["materialsGroups"]["items"] == [
        {"code": "M", "amount": 3.5}
    ]
    assert "materialsGroups" in schema.get_type("ProjectType").fields
    assert (
        "materialsGroups"
        not in contract.capture_manager_contract("Project")[0]["types"]["ProjectType"][
            "fields"
        ]
    )


def test_exposure_rebuild_changes_snapshot_without_reopening_group_paths(group_schema):
    _, managers = group_schema
    _, old_snapshot = contract.capture_manager_contract("Project")
    managers["Material"].chat_exposed = not managers["Material"].chat_exposed
    contract.clear_contract_cache()
    rebuilt, new_snapshot = contract.capture_manager_contract("Project")
    assert new_snapshot != old_snapshot
    fields = rebuilt["types"]["ProjectType"]["fields"]
    assert {"materialsGroups", "ownGroups", "directGroups", "wrappedGroups"}.isdisjoint(
        fields
    )
    assert ("material" in fields) == managers["Material"].chat_exposed


def test_native_group_factories_bind_the_actual_manager_on_all_generated_types():
    from tests.experiments.test_gm_eval_harness import _child

    data = _child("""
import json
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=5,snapshot='RANK',variant='weight',seed=17)
try:
    from general_manager.api.graphql import GraphQL
    bindings=[]
    for registry in (GraphQL._group_type_registry,GraphQL._group_page_type_registry):
        for name,group_type in registry.items():
            manager=GraphQL.manager_registry[name]
            bindings.append(getattr(group_type,'_general_manager_group_owner',None) is manager)
    print(json.dumps({'bindings':bindings}))
finally:
    runtime.close()
""")
    assert data["bindings"]
    assert all(data["bindings"])
