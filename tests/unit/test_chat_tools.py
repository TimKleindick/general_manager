from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import graphene
from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.schema_index import (
    build_schema_index,
    clear_schema_index_cache,
    find_exposed_path,
    get_manager_schema_summary,
    search_manager_summaries,
)
from general_manager.chat.system_prompt import build_system_prompt
from general_manager.chat.tools import (
    execute_chat_tool,
    find_path,
    get_manager_schema,
    get_tool_definitions,
    search_managers,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.path_mapping import PathMap
from tests.utils.simple_manager_interface import BaseTestInterface


class ChatSchemaIndexTests(SimpleTestCase):
    def setUp(self) -> None:
        GraphQL.reset_registry()
        GeneralManagerMeta.all_classes.clear()
        GeneralManagerMeta.pending_graphql_interfaces.clear()
        GeneralManagerMeta.pending_attribute_initialization.clear()
        PathMap.mapping.clear()
        if hasattr(PathMap, "instance"):
            delattr(PathMap, "instance")

        class MaterialInterface(BaseTestInterface):
            @staticmethod
            def get_attribute_types() -> dict[str, dict[str, object]]:
                return {"name": {"type": str}, "density": {"type": float}}

        class MaterialManager(GeneralManager):
            Interface = MaterialInterface

        class PartInterface(BaseTestInterface):
            @staticmethod
            def get_attribute_types() -> dict[str, dict[str, object]]:
                return {"name": {"type": str}}

        class PartManager(GeneralManager):
            Interface = PartInterface

        class SecretInterface(BaseTestInterface):
            @staticmethod
            def get_attribute_types() -> dict[str, dict[str, object]]:
                return {"code": {"type": str}}

        class SecretManager(GeneralManager):
            Interface = SecretInterface
            chat_exposed = False

        self.MaterialManager = MaterialManager
        self.PartManager = PartManager
        self.SecretManager = SecretManager

        class MaterialType(graphene.ObjectType):
            """Materials used by parts."""

            name = graphene.String()
            density = graphene.Float()

        class PartType(graphene.ObjectType):
            """Inventory part."""

            name = graphene.String()
            material = graphene.Field(MaterialType)

        class SecretType(graphene.ObjectType):
            """Hidden admin data."""

            code = graphene.String()

        class MaterialFilter(graphene.InputObjectType):
            name = graphene.String()
            density_gt = graphene.Float()

        GraphQL.graphql_type_registry = {
            "MaterialManager": MaterialType,
            "PartManager": PartType,
            "SecretManager": SecretType,
        }
        GraphQL.graphql_filter_type_registry = {"MaterialManager": MaterialFilter}
        GraphQL.manager_registry = {
            "MaterialManager": MaterialManager,
            "PartManager": PartManager,
            "SecretManager": SecretManager,
        }
        GraphQL._schema = graphene.Schema(
            query=type("Query", (graphene.ObjectType,), {})
        )

        PathMap.mapping[("PartManager", "MaterialManager")] = SimpleNamespace(
            path=["material"]
        )
        PathMap.mapping[("PartManager", "SecretManager")] = SimpleNamespace(
            path=["secret"]
        )
        PathMap.mapping[("SecretManager", "MaterialManager")] = SimpleNamespace(
            path=["material"]
        )

    def tearDown(self) -> None:
        clear_schema_index_cache()
        GraphQL.reset_registry()
        GeneralManagerMeta.all_classes.clear()
        GeneralManagerMeta.pending_graphql_interfaces.clear()
        GeneralManagerMeta.pending_attribute_initialization.clear()
        PathMap.mapping.clear()
        if hasattr(PathMap, "instance"):
            delattr(PathMap, "instance")
        super().tearDown()

    def test_build_schema_index_excludes_hidden_managers(self) -> None:
        index = build_schema_index()

        assert set(index.keys()) == {"MaterialManager", "PartManager"}
        assert "SecretManager" not in index

    def test_build_schema_index_is_cached_until_explicitly_cleared(self) -> None:
        first = build_schema_index()
        GraphQL.graphql_type_registry.pop("PartManager")
        GraphQL.manager_registry.pop("PartManager")

        second = build_schema_index()

        assert second is first
        assert set(second.keys()) == {"MaterialManager", "PartManager"}

        clear_schema_index_cache()
        refreshed = build_schema_index()

        assert set(refreshed.keys()) == {"MaterialManager"}

    def test_search_managers_matches_name_description_and_fields(self) -> None:
        results = search_manager_summaries("materials density")

        assert [result["manager"] for result in results] == ["MaterialManager"]
        assert results[0]["description"] == "Materials used by parts."

    def test_get_manager_schema_summary_returns_fields_relations_and_filters(
        self,
    ) -> None:
        summary = get_manager_schema_summary("PartManager")

        assert summary["manager"] == "PartManager"
        assert summary["fields"] == ["name"]
        assert summary["relations"] == [
            {"name": "material", "target": "MaterialManager"}
        ]
        assert summary["filters"] == []

    def test_hidden_manager_schema_is_not_exposed(self) -> None:
        assert get_manager_schema_summary("SecretManager") is None

    def test_find_exposed_path_rejects_hidden_destinations(self) -> None:
        assert find_exposed_path("PartManager", "SecretManager") is None

    def test_find_exposed_path_discovers_reverse_multi_hop_paths(self) -> None:
        assert find_exposed_path("MaterialManager", "PartManager") == ["material"]

    def test_tool_wrappers_delegate_to_schema_index(self) -> None:
        assert search_managers("inventory")[0]["manager"] == "PartManager"
        assert get_manager_schema("MaterialManager") == {
            "manager": "MaterialManager",
            "description": "Materials used by parts.",
            "fields": ["density", "name"],
            "relations": [],
            "filters": ["density_gt", "name"],
        }
        assert find_path("PartManager", "MaterialManager") == ["material"]

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "tool_strategy": "direct",
            }
        }
    )
    def test_direct_tool_strategy_generates_query_tool_per_exposed_manager(
        self,
    ) -> None:
        tools = get_tool_definitions()

        names = {tool["name"] for tool in tools}
        assert "query_materialmanager" in names
        assert "query_partmanager" in names
        assert "query_secretmanager" not in names
        assert "search_managers" not in names

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "tool_strategy": "direct",
            }
        }
    )
    def test_direct_tool_strategy_dispatches_manager_query_tool(self) -> None:
        with patch(
            "general_manager.chat.tools.query",
            return_value={"data": [{"name": "Steel"}]},
        ) as query_tool:
            result = execute_chat_tool(
                "query_materialmanager",
                {"filters": {"name": "Steel"}, "fields": ["name"]},
                None,
            )

        assert result == {"data": [{"name": "Steel"}]}
        query_tool.assert_called_once_with(
            manager="MaterialManager",
            filters={"name": "Steel"},
            fields=["name"],
            limit=None,
            offset=0,
            context=None,
        )

    def test_default_tool_strategy_exposes_structured_input_schemas(self) -> None:
        tools = {tool["name"]: tool for tool in get_tool_definitions()}

        assert tools["search_managers"]["input_schema"]["required"] == ["query"]
        assert tools["get_manager_schema"]["input_schema"]["required"] == ["manager"]
        assert tools["find_path"]["input_schema"]["required"] == [
            "from_manager",
            "to_manager",
        ]
        assert tools["query"]["input_schema"]["required"] == ["manager", "fields"]
        assert tools["mutate"]["input_schema"]["required"] == ["mutation", "input"]

    def test_system_prompt_includes_tool_usage_examples(self) -> None:
        prompt = build_system_prompt()

        assert "Tool calling rules" in prompt
        assert "Rule 1 DISCOVERY" in prompt
        assert "MUST call search_managers" in prompt
        assert "Rule 2 EXPLORATION" in prompt
        assert "find_path" in prompt
        assert "Rule 3 COMPLETE ALL TOOL CALLS BEFORE ANSWERING" in prompt
        assert "[tool:query]" in prompt
        assert "Rule 4 TRUST RESULTS" in prompt
        assert '"filters": {"material__name": "Steel"}' in prompt
        assert '"filters": {"parts__material__name": "Cobalt"}' in prompt
        assert "Example tool call for search_managers:" in prompt
        assert '"query": "parts"' in prompt
        assert "Example tool call for query:" in prompt
        assert '"manager": "PartManager"' in prompt
        assert '"fields": ["name", {"material": ["name"]}]' in prompt
        assert "Example tool call for mutate:" in prompt
        assert '"confirmed": true' in prompt
