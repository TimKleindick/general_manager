from __future__ import annotations

from types import SimpleNamespace

import graphene
from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.system_prompt import build_system_prompt
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.path_mapping import PathMap
from tests.utils.simple_manager_interface import BaseTestInterface


class ChatSystemPromptTests(SimpleTestCase):
    def setUp(self) -> None:
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

        class MaterialInterface(BaseTestInterface):
            pass

        class MaterialManager(GeneralManager):
            Interface = MaterialInterface

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
