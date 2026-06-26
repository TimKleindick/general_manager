from __future__ import annotations

from types import SimpleNamespace

import graphene
from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.schema_index import clear_schema_index_cache
from general_manager.chat.system_prompt import build_system_prompt
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.path_mapping import PathMap
from tests.utils.simple_manager_interface import BaseTestInterface


def test_system_prompt_requires_query_after_successful_path_for_record_questions() -> (
    None
):
    prompt = build_system_prompt()

    assert "If find_path returns a non-empty path for a record question" in prompt
    assert "call query on the destination manager" in prompt
    assert "do not say there is no path" in prompt


class ChatSystemPromptTests(SimpleTestCase):
    def setUp(self) -> None:
        clear_schema_index_cache()
        GraphQL.reset_registry()
        GeneralManagerMeta.all_classes.clear()
        GeneralManagerMeta.pending_graphql_interfaces.clear()
        GeneralManagerMeta.pending_attribute_initialization.clear()
        PathMap.mapping.clear()
        if hasattr(PathMap, "instance"):
            delattr(PathMap, "instance")

        class PartInterface(BaseTestInterface):
            pass

        class PartManager(GeneralManager):
            Interface = PartInterface
            chat_exposed = True

        class MaterialInterface(BaseTestInterface):
            pass

        class MaterialManager(GeneralManager):
            Interface = MaterialInterface
            chat_exposed = True

        class SecretInterface(BaseTestInterface):
            pass

        class SecretManager(GeneralManager):
            Interface = SecretInterface
            chat_exposed = False

        class PartType(graphene.ObjectType):
            """Inventory part."""

            name = graphene.String()

        class MaterialType(graphene.ObjectType):
            """Raw material."""

            name = graphene.String()

        class SecretType(graphene.ObjectType):
            """Hidden admin manager."""

            code = graphene.String()

        GraphQL.graphql_type_registry = {
            "PartManager": PartType,
            "MaterialManager": MaterialType,
            "SecretManager": SecretType,
        }
        GraphQL.manager_registry = {
            "PartManager": PartManager,
            "MaterialManager": MaterialManager,
            "SecretManager": SecretManager,
        }
        PathMap.mapping[("PartManager", "MaterialManager")] = SimpleNamespace(
            path=["material"]
        )
        PathMap.mapping[("PartManager", "SecretManager")] = SimpleNamespace(
            path=["secret"]
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

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "system_prompt": "Always cite manager names.",
            }
        }
    )
    def test_build_system_prompt_includes_tools_managers_relationships_and_developer_prompt(
        self,
    ) -> None:
        prompt = build_system_prompt()

        assert "search_managers" in prompt
        assert "get_manager_schema" in prompt
        assert "find_path" in prompt
        assert "query" in prompt
        assert "mutate" in prompt
        assert "PartManager: Inventory part." in prompt
        assert "MaterialManager: Raw material." in prompt
        assert "PartManager -> MaterialManager" in prompt
        assert "Always cite manager names." in prompt

    def test_build_system_prompt_excludes_hidden_managers(self) -> None:
        prompt = build_system_prompt()

        assert "SecretManager" not in prompt
        assert "Hidden admin manager." not in prompt

    def test_build_system_prompt_includes_tool_decision_answer_and_safety_contracts(
        self,
    ) -> None:
        prompt = build_system_prompt()

        assert "Tool decision process" in prompt
        assert "If the exact manager is unknown, call search_managers first." in prompt
        assert (
            "If fields, filters, or relation names are uncertain, call "
            "get_manager_schema before query."
        ) in prompt
        assert "For cross-manager questions, call find_path" in prompt
        assert "Answer rules" in prompt
        assert "Answer data questions only from tool results." in prompt
        assert "Copy record values exactly from the tool JSON" in prompt
        assert (
            "If query returns no rows, say that no matching records were found."
            in prompt
        )
        assert (
            "If query returns one or more rows, never say no matching records" in prompt
        )
        assert (
            "Do not ask whether to run another query after a successful query" in prompt
        )
        assert "Do not include code fences" in prompt
        assert (
            "Do not propose another query after data has already been returned"
            in prompt
        )
        assert "include the requested schema field names" in prompt
        assert "mention the relevant manager names and path terms" in prompt
        assert "label nested relation values with the relation name" in prompt
        assert "If an earlier query failed but a later query succeeded" in prompt
        assert (
            "If any successful query in this turn returned rows that answer the "
            "question, include those rows even if a later query returned no rows."
            in prompt
        )
        assert "name the requested row type" in prompt
        assert 'Never use wildcard field selections like "*"' in prompt
        assert "Mutation safety" in prompt
        assert "Never call mutate unless the user clearly requests a write." in prompt

    def test_build_system_prompt_includes_target_manager_and_unavailable_rules(
        self,
    ) -> None:
        prompt = build_system_prompt()

        assert (
            "query the manager that returns the row type the user asked for" in prompt
        )
        assert "Do not repeat unavailable manager names" in prompt
        assert "Do not write user-provided tokens ending in Manager" in prompt

    def test_build_system_prompt_includes_no_memory_answer_recovery_rule(self) -> None:
        prompt = build_system_prompt()

        assert (
            "If a user asks for application data and no query tool has run in this "
            "turn, call tools instead of answering from memory."
        ) in prompt
        assert (
            "The tool result JSON is the source of truth even when it conflicts "
            "with general knowledge or previous assumptions."
        ) in prompt

    def test_build_system_prompt_summarizes_large_manager_registry(self) -> None:
        for index in range(205):
            manager_name = f"BulkManager{index:03d}"

            class BulkType(graphene.ObjectType):
                """Bulk manager used to exercise prompt scaling."""

                name = graphene.String()

            BulkType.__name__ = f"BulkType{index:03d}"
            GraphQL.graphql_type_registry[manager_name] = BulkType
            GraphQL.manager_registry[manager_name] = GraphQL.manager_registry[
                "PartManager"
            ]
        clear_schema_index_cache()

        prompt = build_system_prompt()

        assert "207 exposed managers available" in prompt
        assert "Use search_managers to discover relevant managers by name" in prompt
        assert (
            "BulkManager000: Bulk manager used to exercise prompt scaling."
            not in prompt
        )
        assert "Relationship graph omitted for large schemas" in prompt
