# type: ignore[file-ignores]


import asyncio
import os
from types import SimpleNamespace

import graphene
from django.contrib.auth import get_user_model
from django.db.models import CharField
from graphql import parse
from graphql.language.ast import FragmentDefinitionNode, OperationDefinitionNode
import unittest

from general_manager.api.graphql import GraphQL
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.manager.generalManager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class _DummyInterface:
    @classmethod
    def getGraphQLProperties(cls) -> dict[str, object]:
        return {
            "propA": object(),
            "propB": object(),
            "propC": object(),
        }


class _DummyManager:
    Interface = _DummyInterface


class TestGraphQLDatabaseSubscriptions(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class Employee(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=120)

        cls.general_manager_classes = [Employee]
        cls.Employee = Employee

    def setUp(self) -> None:
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user(username="alice", password="secret")
        self.client.force_login(self.user)
        self._async_env_original = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

    def tearDown(self) -> None:
        if self._async_env_original is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = self._async_env_original
        super().tearDown()

    def _build_schema(self) -> graphene.Schema:
        schema_kwargs: dict[str, object] = {"query": GraphQL._query_class}
        if GraphQL._mutation_class is not None:
            schema_kwargs["mutation"] = GraphQL._mutation_class
        if GraphQL._subscription_class is not None:
            schema_kwargs["subscription"] = GraphQL._subscription_class
        return graphene.Schema(**schema_kwargs)

    def test_database_subscription_emits_updates(self) -> None:
        employee = self.Employee.create(name="Alice", creator_id=self.user.id)
        schema = self._build_schema()
        context = SimpleNamespace(user=self.user)
        subscription = """
            subscription ($id: ID!) {
                onEmployeeChange(id: $id) {
                    action
                    item {
                        id
                        name
                    }
                }
            }
        """

        async def run_subscription() -> tuple[object, object]:
            generator = await schema.subscribe(
                subscription,
                variable_values={"id": employee.id},
                context_value=context,
            )
            try:
                first = await generator.__anext__()
                await asyncio.to_thread(
                    lambda: employee.update(
                        name="Bob",
                        creator_id=self.user.id,
                    )
                )
                second = await generator.__anext__()
            finally:
                await generator.aclose()
            return first, second

        first_event, second_event = asyncio.run(run_subscription())

        self.assertIsNone(first_event.errors)
        snapshot = first_event.data["onEmployeeChange"]
        self.assertEqual(snapshot["action"], "snapshot")
        self.assertEqual(snapshot["item"]["name"], "Alice")

        self.assertIsNone(second_event.errors)
        update = second_event.data["onEmployeeChange"]
        self.assertEqual(update["action"], "update")
        self.assertEqual(update["item"]["name"], "Bob")


class GraphQLSubscriptionPropertySelectionTests(unittest.TestCase):
    @staticmethod
    def _build_info(query: str) -> SimpleNamespace:
        document = parse(query)
        field_nodes = []
        fragments: dict[str, FragmentDefinitionNode] = {}
        for definition in document.definitions:
            if isinstance(definition, FragmentDefinitionNode):
                fragments[definition.name.value] = definition
            elif isinstance(definition, OperationDefinitionNode):
                if definition.selection_set is not None:
                    field_nodes.extend(definition.selection_set.selections)
        return SimpleNamespace(field_nodes=field_nodes, fragments=fragments)

    def test_direct_property_selection(self) -> None:
        info = self._build_info(
            """
            subscription {
                onDummyChange(id: "1") {
                    item {
                        propA
                    }
                }
            }
            """
        )
        property_names = GraphQL._subscription_property_names(
            info, _DummyManager
        )
        self.assertEqual(property_names, {"propA"})

    def test_property_selection_via_inline_fragment_and_alias(self) -> None:
        info = self._build_info(
            """
            subscription {
                onDummyChange(id: "1") {
                    item {
                        ... on DummyManagerType {
                            aliasValue: propB
                        }
                    }
                }
            }
            """
        )
        property_names = GraphQL._subscription_property_names(
            info, _DummyManager
        )
        self.assertEqual(property_names, {"propB"})

    def test_property_selection_via_named_fragment(self) -> None:
        info = self._build_info(
            """
            fragment ExtraFields on DummyManagerType {
                propC
                nonProperty
            }
            subscription {
                onDummyChange(id: "1") {
                    item {
                        ...ExtraFields
                    }
                }
            }
            """
        )
        property_names = GraphQL._subscription_property_names(
            info, _DummyManager
        )
        self.assertEqual(property_names, {"propC"})
