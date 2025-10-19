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
        """
        Return a mapping of GraphQL property names to placeholder descriptor objects used in tests.
        
        Returns:
            dict[str, object]: Mapping where keys are property names ("propA", "propB", "propC") and values are placeholder descriptor objects.
        """
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
        """
        Prepare test class-level fixtures: define a temporary Employee GeneralManager subclass with a DatabaseInterface
        that has a CharField `name`, store it as `cls.Employee`, and register it in `cls.general_manager_classes`.
        """
        class Employee(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=120)

        cls.general_manager_classes = [Employee]
        cls.Employee = Employee

    def setUp(self) -> None:
        """
        Prepare test environment by creating and logging in a test user and enabling Django async operations.
        
        Creates a user named "alice" and forces the test client to authenticate as that user. Saves the original value of the `DJANGO_ALLOW_ASYNC_UNSAFE` environment variable and sets it to "true" so asynchronous operations are allowed during the test.
        """
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user(username="alice", password="secret")
        self.client.force_login(self.user)
        self._async_env_original = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

    def tearDown(self) -> None:
        """
        Restore the DJANGO_ALLOW_ASYNC_UNSAFE environment variable to its original value and run superclass teardown.
        
        If the original value was not set, the environment variable is removed; otherwise it is reset to the saved value. Then delegates to the superclass's tearDown method.
        """
        if self._async_env_original is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = self._async_env_original
        super().tearDown()

    def _build_schema(self) -> graphene.Schema:
        """
        Builds a graphene.Schema using the module's registered query class and, if present, its mutation and subscription classes.
        
        Returns:
            schema (graphene.Schema): A GraphQL schema constructed with GraphQL._query_class and optionally GraphQL._mutation_class and GraphQL._subscription_class.
        """
        schema_kwargs: dict[str, object] = {"query": GraphQL._query_class}
        if GraphQL._mutation_class is not None:
            schema_kwargs["mutation"] = GraphQL._mutation_class
        if GraphQL._subscription_class is not None:
            schema_kwargs["subscription"] = GraphQL._subscription_class
        return graphene.Schema(**schema_kwargs)

    def test_database_subscription_emits_updates(self) -> None:
        """
        Verifies that a GraphQL subscription for an Employee emits an initial snapshot and then an update when the underlying model changes.
        
        Subscribes to onEmployeeChange for a created Employee, confirms the first event is a snapshot containing the initial name "Alice", performs an update to change the name to "Bob", and confirms the subsequent event is an update containing the new name "Bob".
        """
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
            """
            Run the GraphQL subscription for the test employee and capture the initial snapshot event followed by the update event.
            
            Returns:
                tuple[first_event, second_event] (tuple[object, object]): The first event emitted for the subscription (initial snapshot) and the second event emitted after the employee record is updated.
            """
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
        """
        Builds a lightweight info object containing top-level field selections and named fragments from a GraphQL query string.
        
        Parameters:
            query (str): GraphQL document (query, mutation, or subscription) to parse.
        
        Returns:
            SimpleNamespace: An object with two attributes:
                - field_nodes (list): List of top-level selection nodes from the operation definitions.
                - fragments (dict): Mapping from fragment name (str) to its FragmentDefinitionNode.
        """
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
        """
        Verify that _subscription_property_names extracts a directly selected field name from a subscription selection.
        
        Builds GraphQL selection info for a subscription selecting item.propA and asserts the extracted property names equal {"propA"}.
        """
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

    def test_manager_without_interface_returns_empty_set(self) -> None:
        class NoInterfaceManager:
            pass

        info = self._build_info(
            """
            subscription {
                onDummyChange(id: "1") {
                    action
                }
            }
            """
        )
        property_names = GraphQL._subscription_property_names(info, NoInterfaceManager)  # type: ignore[arg-type]
        self.assertEqual(property_names, set())

    def test_manager_without_graphql_properties_returns_empty_set(self) -> None:
        """
        Verifies that _subscription_property_names yields an empty set when a manager's Interface exposes no GraphQL properties.
        
        Constructs a subscription selection that requests a field from the item, provides a manager whose Interface.getGraphQLProperties returns an empty dict, and asserts the extracted property name set is empty.
        """
        class EmptyInterface:
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                return {}

        class EmptyManager:
            Interface = EmptyInterface

        info = self._build_info(
            """
            subscription {
                onEmptyChange(id: "1") {
                    item {
                        anything
                    }
                }
            }
            """
        )
        property_names = GraphQL._subscription_property_names(info, EmptyManager)  # type: ignore[arg-type]
        self.assertEqual(property_names, set())


class GraphQLPrimeHelpersTests(unittest.TestCase):
    def setUp(self) -> None:
        """
        Prepare test fixtures by creating a PrimeTestManager class with a GraphQL-facing Interface and an access log for property access tracking.
        
        Defines a PrimeInterface whose `getGraphQLProperties` exposes "alpha" and "beta", and a PrimeTestManager that:
        - provides an `Interface` subclassing PrimeInterface,
        - maintains `access_log` (list of property names accessed),
        - exposes `alpha` and `beta` properties that record their access and return 1 and 2 respectively.
        
        Assigns the PrimeTestManager class to `self.manager_cls`.
        """
        class PrimeInterface:
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                return {
                    "alpha": PrimeTestManager.alpha,
                    "beta": PrimeTestManager.beta,
                }

        class PrimeTestManager:
            access_log: list[str] = []

            class Interface(PrimeInterface):
                pass

            @property
            def alpha(self) -> int:
                """
                Record access to the 'alpha' property by appending "alpha" to the class access_log and return its integer value.
                
                Returns:
                    int: The value 1.
                """
                type(self).access_log.append("alpha")
                return 1

            @property
            def beta(self) -> int:
                """
                Provide the integer value for the "beta" property and record its access.
                
                Appends the string "beta" to type(self).access_log to indicate the property was accessed.
                
                Returns:
                    int: The integer 2.
                """
                type(self).access_log.append("beta")
                return 2

        self.manager_cls = PrimeTestManager

    def test_prime_all_properties_when_names_not_specified(self) -> None:
        instance = self.manager_cls()
        GraphQL._prime_graphql_properties(instance)
        self.assertEqual(
            self.manager_cls.access_log,
            ["alpha", "beta"],
        )

    def test_prime_only_requested_properties(self) -> None:
        self.manager_cls.access_log = []
        instance = self.manager_cls()
        GraphQL._prime_graphql_properties(instance, ["beta", "missing"])
        self.assertEqual(self.manager_cls.access_log, ["beta"])


class GraphQLDependencyExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        """
        Prepare a test-specific GraphQL manager registry and preserve the original for restoration.
        
        Saves a copy of the current GraphQL.manager_registry, replaces it with a registry containing a temporary DepManager whose Interface exposes no GraphQL properties, and stores that DepManager class on the test instance as `dep_manager_cls` for use by tests.
        """
        self._original_registry = GraphQL.manager_registry.copy()

        class DepManager:
            class Interface:
                @classmethod
                def getGraphQLProperties(cls) -> dict[str, object]:
                    """
                    Provide a mapping of GraphQL-exposed property names to their descriptor objects for this interface class.
                    
                    Returns:
                        dict[str, object]: Mapping from property name to property descriptor; may be empty by default and is intended to be overridden by subclasses.
                    """
                    return {}

        GraphQL.manager_registry = {"DepManager": DepManager}
        self.dep_manager_cls = DepManager

    def tearDown(self) -> None:
        """
        Restore the GraphQL manager registry to its previously saved state.
        
        Resets GraphQL.manager_registry to the value stored in self._original_registry during test setup.
        """
        GraphQL.manager_registry = self._original_registry

    def test_dependencies_from_tracker_filters_invalid_entries(self) -> None:
        records = [
            ("DepManager", "identification", "{'id': 1}"),
            ("DepManager", "identification", "not a dict"),
            ("DepManager", "filter", "{'id': 2}"),
            ("Unknown", "identification", "{'id': 3}"),
        ]

        extracted = GraphQL._dependencies_from_tracker(records)
        self.assertEqual(len(extracted), 1)
        manager_cls, identification = extracted[0]
        self.assertIs(manager_cls, self.dep_manager_cls)
        self.assertEqual(identification, {"id": 1})