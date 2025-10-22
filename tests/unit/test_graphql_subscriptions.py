# type: ignore[file-ignores]


import asyncio
import os
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import graphene
from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.utils.crypto import get_random_string
from graphql import parse
from graphql.language.ast import FragmentDefinitionNode, OperationDefinitionNode
import unittest

from general_manager.api.graphql import GraphQL
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.manager.generalManager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from general_manager.interface.baseInterface import InterfaceBase
from tests.utils.simple_manager_interface import BaseTestInterface


class _DummyInterface(BaseTestInterface):
    @classmethod
    def getGraphQLProperties(cls) -> dict[str, object]:
        """
        Provide a mapping of GraphQL property names to placeholder descriptor objects used in tests.

        Returns:
            dict[str, object]: Mapping with keys "propA", "propB", and "propC" whose values are placeholder descriptor objects.
        """
        return {
            "propA": object(),
            "propB": object(),
            "propC": object(),
        }


class _DummyManager:
    Interface = _DummyInterface


_DummyInterface._parent_class = _DummyManager


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
        Create a test user, authenticate the test client as that user, and enable Django async operations.

        Creates a user named "alice" with a random password, forces the test client to log in as that user, stores the original value of the DJANGO_ALLOW_ASYNC_UNSAFE environment variable on self._async_env_original, and sets that environment variable to "true".
        """
        super().setUp()
        User = get_user_model()
        password = get_random_string(12)
        self.user = User.objects.create_user(username="alice", password=password)
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
            Subscribe to the schema for the test employee and capture the initial snapshot event followed by the update event.

            Returns:
                tuple[first_event, second_event] (tuple[object, object]): `first_event` is the initial snapshot emitted by the subscription; `second_event` is the event emitted after the employee record is updated.
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
        property_names = GraphQL._subscription_property_names(info, _DummyManager)
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
        property_names = GraphQL._subscription_property_names(info, _DummyManager)
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
        property_names = GraphQL._subscription_property_names(info, _DummyManager)
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

        class EmptyInterface(BaseTestInterface):
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                """
                Provide GraphQL-exposed property descriptors for this Interface.

                Returns:
                    dict[str, object]: Mapping from property name to its property descriptor object. An empty dict indicates no GraphQL properties are exposed.
                """
                return {}

        class EmptyManager:
            Interface = EmptyInterface

        EmptyInterface._parent_class = EmptyManager

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

        class PrimeInterface(BaseTestInterface):
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                """
                Return the mapping of GraphQL-exposed property names to their corresponding manager property descriptors.

                Parameters:
                    cls: The interface class requesting its GraphQL properties.

                Returns:
                    dict[str, object]: A dictionary mapping property names (e.g., "alpha", "beta") to the property descriptors used by the manager.
                """
                return {
                    "alpha": PrimeTestManager.alpha,
                    "beta": PrimeTestManager.beta,
                }

        class PrimeTestManager:
            access_log: ClassVar[list[str]] = []

            class Interface(PrimeInterface):
                pass

            @property
            def alpha(self) -> int:
                """
                Record access to the "alpha" property by appending "alpha" to the class access_log.

                Returns:
                    int: The value 1.
                """
                type(self).access_log.append("alpha")
                return 1

            @property
            def beta(self) -> int:
                """
                Record access to the "beta" property and return its integer value.

                Appends "beta" to type(self).access_log to indicate the property was accessed.

                Returns:
                    int: The integer 2.
                """
                type(self).access_log.append("beta")
                return 2

        PrimeTestManager.Interface._parent_class = PrimeTestManager
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
            class Interface(BaseTestInterface):
                @classmethod
                def getGraphQLProperties(cls) -> dict[str, object]:
                    """
                    Return a mapping of GraphQL-exposed property names to their descriptor objects for this interface class.

                    Returns:
                        dict[str, object]: Mapping from property name to property descriptor; empty by default and intended to be overridden by subclasses.
                    """
                    return {}

        DepManager.Interface._parent_class = DepManager
        GraphQL.manager_registry = {"DepManager": DepManager}
        self.dep_manager_cls = DepManager

    def tearDown(self) -> None:
        """
        Restore the GraphQL manager registry to the saved registry captured during test setup.
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


class GraphQLChannelLayerTests(unittest.TestCase):
    """Test channel layer helper methods."""

    def test_get_channel_layer_returns_none_when_not_configured(self) -> None:
        """Verify _get_channel_layer returns None when no channel layer is configured."""
        with patch("general_manager.api.graphql.get_channel_layer", return_value=None):
            layer = GraphQL._get_channel_layer(strict=False)
            self.assertIsNone(layer)

    def test_get_channel_layer_raises_when_strict_and_not_configured(self) -> None:
        """Verify _get_channel_layer raises RuntimeError in strict mode when no channel layer exists."""
        with patch("general_manager.api.graphql.get_channel_layer", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                GraphQL._get_channel_layer(strict=True)
            self.assertIn("No channel layer configured", str(ctx.exception))

    def test_get_channel_layer_returns_layer_when_available(self) -> None:
        """Verify _get_channel_layer returns the configured channel layer."""
        mock_layer = object()
        with patch(
            "general_manager.api.graphql.get_channel_layer", return_value=mock_layer
        ):
            layer = GraphQL._get_channel_layer()
            self.assertIs(layer, mock_layer)


class GraphQLGroupNameTests(unittest.TestCase):
    """Test subscription group name generation."""

    def test_group_name_is_deterministic(self) -> None:
        """Verify _group_name produces consistent output for identical inputs."""

        class TestManager(GeneralManager):
            pass

        identification = {"id": 123, "name": "test"}
        name1 = GraphQL._group_name(TestManager, identification)
        name2 = GraphQL._group_name(TestManager, identification)
        self.assertEqual(name1, name2)

    def test_group_name_differs_for_different_managers(self) -> None:
        """Verify _group_name produces different names for different manager classes."""

        class ManagerA(GeneralManager):
            pass

        class ManagerB(GeneralManager):
            pass

        identification = {"id": 1}
        name_a = GraphQL._group_name(ManagerA, identification)
        name_b = GraphQL._group_name(ManagerB, identification)
        self.assertNotEqual(name_a, name_b)

    def test_group_name_differs_for_different_identifications(self) -> None:
        """Verify _group_name produces different names for different identifications."""

        class TestManager(GeneralManager):
            pass

        name1 = GraphQL._group_name(TestManager, {"id": 1})
        name2 = GraphQL._group_name(TestManager, {"id": 2})
        self.assertNotEqual(name1, name2)

    def test_group_name_handles_complex_identifications(self) -> None:
        """Verify _group_name handles nested and complex identification dictionaries."""

        class TestManager(GeneralManager):
            pass

        identification = {
            "id": 123,
            "nested": {"key": "value"},
            "list": [1, 2, 3],
        }
        name = GraphQL._group_name(TestManager, identification)
        self.assertIsInstance(name, str)
        self.assertTrue(name.startswith("gm_subscriptions."))


class GraphQLPrimePropertiesEdgeCaseTests(unittest.TestCase):
    """Test edge cases in property priming."""

    def test_prime_with_no_interface(self) -> None:
        """Verify _prime_graphql_properties handles managers without an Interface gracefully."""

        class NoInterfaceManager:
            pass

        instance = NoInterfaceManager()
        # Should not raise
        GraphQL._prime_graphql_properties(instance)  # type: ignore[arg-type]

    def test_prime_with_property_raising_exception(self) -> None:
        """Verify _prime_graphql_properties propagates property getter exceptions."""

        class ExceptionInterface(BaseTestInterface):
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                """
                Return a mapping of GraphQL-exposed property names to their property descriptors for the interface class.

                Returns:
                    dict[str, object]: Mapping where keys are GraphQL property names and values are the corresponding descriptor objects (e.g., manager property descriptors).
                """
                return {"bad_prop": ExceptionManager.bad_prop}

        class ExceptionManager:
            class Interface(ExceptionInterface):
                pass

            @property
            def bad_prop(self) -> int:
                raise ValueError() from None

        ExceptionManager.Interface._parent_class = ExceptionManager

        instance = ExceptionManager()
        with self.assertRaises(ValueError):
            GraphQL._prime_graphql_properties(instance)

    def test_prime_with_empty_property_names_list(self) -> None:
        """Verify _prime_graphql_properties with an empty property list accesses no properties."""
        accessed = []

        class TestInterface(BaseTestInterface):
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                return {"prop": TestManager.prop}

        class TestManager:
            class Interface(TestInterface):
                pass

            @property
            def prop(self) -> int:
                accessed.append("prop")
                return 1

        TestManager.Interface._parent_class = TestManager

        instance = TestManager()
        GraphQL._prime_graphql_properties(instance, [])
        self.assertEqual(accessed, [])


class GraphQLDependencyTrackerEdgeCaseTests(unittest.TestCase):
    """Test edge cases in dependency extraction from tracker."""

    def setUp(self) -> None:
        """Save and replace GraphQL manager registry for test isolation."""
        self._original_registry = GraphQL.manager_registry.copy()

        class TestManager(GeneralManager):
            class Interface(BaseTestInterface):
                @classmethod
                def getGraphQLProperties(cls) -> dict[str, object]:
                    return {}

        GraphQL.manager_registry = {"TestManager": TestManager}
        self.test_manager_cls = TestManager

    def tearDown(self) -> None:
        """Restore original GraphQL manager registry."""
        GraphQL.manager_registry = self._original_registry

    def test_dependencies_with_empty_records(self) -> None:
        """Verify _dependencies_from_tracker returns empty list for no records."""
        extracted = GraphQL._dependencies_from_tracker([])
        self.assertEqual(extracted, [])

    def test_dependencies_filters_non_identification_operations(self) -> None:
        """Verify _dependencies_from_tracker ignores records with non-identification operations."""
        records = [
            ("TestManager", "filter", "{'id': 1}"),
            ("TestManager", "create", "{'id': 2}"),
            ("TestManager", "update", "{'id': 3}"),
        ]
        extracted = GraphQL._dependencies_from_tracker(records)
        self.assertEqual(len(extracted), 0)

    def test_dependencies_filters_unregistered_managers(self) -> None:
        """Verify _dependencies_from_tracker ignores managers not in the registry."""
        records = [
            ("UnknownManager", "identification", "{'id': 1}"),
        ]
        extracted = GraphQL._dependencies_from_tracker(records)
        self.assertEqual(len(extracted), 0)

    def test_dependencies_filters_malformed_identifiers(self) -> None:
        """Verify _dependencies_from_tracker ignores records with unparseable identifiers."""
        records = [
            ("TestManager", "identification", "not valid python"),
            ("TestManager", "identification", "{'unclosed': "),
            ("TestManager", "identification", "random text"),
        ]
        extracted = GraphQL._dependencies_from_tracker(records)
        self.assertEqual(len(extracted), 0)

    def test_dependencies_filters_non_dict_identifiers(self) -> None:
        """Verify _dependencies_from_tracker ignores identifiers that parse to non-dict values."""
        records = [
            ("TestManager", "identification", "123"),
            ("TestManager", "identification", "'string'"),
            ("TestManager", "identification", "[1, 2, 3]"),
            ("TestManager", "identification", "None"),
        ]
        extracted = GraphQL._dependencies_from_tracker(records)
        self.assertEqual(len(extracted), 0)

    def test_dependencies_extracts_valid_records(self) -> None:
        """Verify _dependencies_from_tracker successfully extracts valid dependency records."""
        records = [
            ("TestManager", "identification", "{'id': 1, 'name': 'test'}"),
            ("TestManager", "identification", "{'id': 2}"),
        ]
        extracted = GraphQL._dependencies_from_tracker(records)
        self.assertEqual(len(extracted), 2)
        self.assertIs(extracted[0][0], self.test_manager_cls)
        self.assertEqual(extracted[0][1], {"id": 1, "name": "test"})
        self.assertIs(extracted[1][0], self.test_manager_cls)
        self.assertEqual(extracted[1][1], {"id": 2})


class GraphQLSubscriptionPropertyNamesEdgeCaseTests(unittest.TestCase):
    """Test edge cases in subscription property name extraction."""

    @staticmethod
    def _build_info(query: str) -> SimpleNamespace:
        """Build minimal GraphQL info object from query string."""
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

    def test_multiple_properties_selected(self) -> None:
        """Verify _subscription_property_names extracts multiple selected properties."""
        info = self._build_info(
            """
            subscription {
                onDummyChange(id: "1") {
                    item {
                        propA
                        propB
                        propC
                    }
                }
            }
            """
        )
        property_names = GraphQL._subscription_property_names(info, _DummyManager)  # type: ignore[arg-type]
        self.assertEqual(property_names, {"propA", "propB", "propC"})

    def test_nested_fragments(self) -> None:
        """Verify _subscription_property_names handles fragments referencing other fragments."""
        info = self._build_info(
            """
            fragment Inner on DummyManagerType {
                propA
            }
            fragment Outer on DummyManagerType {
                ...Inner
                propB
            }
            subscription {
                onDummyChange(id: "1") {
                    item {
                        ...Outer
                    }
                }
            }
            """
        )
        property_names = GraphQL._subscription_property_names(info, _DummyManager)  # type: ignore[arg-type]
        self.assertEqual(property_names, {"propA", "propB"})

    def test_action_only_selection(self) -> None:
        """Verify _subscription_property_names returns empty set when only action is selected."""
        info = self._build_info(
            """
            subscription {
                onDummyChange(id: "1") {
                    action
                }
            }
            """
        )
        property_names = GraphQL._subscription_property_names(info, _DummyManager)  # type: ignore[arg-type]
        self.assertEqual(property_names, set())

    def test_non_property_fields_ignored(self) -> None:
        """Verify _subscription_property_names ignores fields not in GraphQLProperties."""
        info = self._build_info(
            """
            subscription {
                onDummyChange(id: "1") {
                    item {
                        propA
                        id
                        __typename
                        nonExistent
                    }
                }
            }
            """
        )
        property_names = GraphQL._subscription_property_names(info, _DummyManager)  # type: ignore[arg-type]
        self.assertEqual(property_names, {"propA"})


def allow_simple_interface_only(cls):
    """
    Temporarily patch `builtins.issubclass` so any type named `SimpleInterface` is treated as a subclass, and apply that patch as a decorator to `cls`.

    Parameters:
        cls: The class to be decorated with the patch.

    Returns:
        The class decorated with a patch that makes `issubclass(X, Y)` return `True` when `X.__name__ == "SimpleInterface"`, otherwise delegating to the original `issubclass`.
    """

    def fake_issubclass(a, b):
        if getattr(a, "__name__", None) == "SimpleInterface":
            return True
        return issubclass(a, b)

    return patch("builtins.issubclass", side_effect=fake_issubclass)(cls)


class GraphQLBuildIdentificationArgumentsTests(unittest.TestCase):
    """Test identification argument building for subscriptions."""

    def test_simple_id_field(self) -> None:
        """Verify _buildIdentificationArguments creates ID argument for 'id' input field."""

        class SimpleInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, object]] = {
                "id": SimpleNamespace(type=int),
            }

        class SimpleManager(GeneralManager):
            Interface = SimpleInterface

        args = GraphQL._buildIdentificationArguments(SimpleManager)
        self.assertIn("id", args)
        self.assertIsInstance(args["id"], graphene.Argument)

    def test_manager_reference_field(self) -> None:
        """Verify _buildIdentificationArguments creates <name>_id field for manager references."""

        class RelatedManager(GeneralManager):
            pass

        class TestInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, object]] = {
                "parent": SimpleNamespace(type=RelatedManager),
            }

        class TestManager(GeneralManager):
            Interface = TestInterface

        args = GraphQL._buildIdentificationArguments(TestManager)
        self.assertIn("parent_id", args)
        self.assertIsInstance(args["parent_id"], graphene.Argument)

    def test_multiple_input_fields(self) -> None:
        """Verify _buildIdentificationArguments handles multiple input fields correctly."""

        class RelatedManager(GeneralManager):
            pass

        class TestInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, object]] = {
                "id": SimpleNamespace(type=int),
                "name": SimpleNamespace(type=str),
                "parent": SimpleNamespace(type=RelatedManager),
            }

        class TestManager(GeneralManager):
            Interface = TestInterface

        args = GraphQL._buildIdentificationArguments(TestManager)
        self.assertIn("id", args)
        self.assertIn("name", args)
        self.assertIn("parent_id", args)
        self.assertEqual(len(args), 3)


class GraphQLHandleDataChangeTests(unittest.TestCase):
    """Test signal handler for subscription data changes."""

    def setUp(self) -> None:
        """Save original registry and mock channel layer."""
        self._original_registry = GraphQL.manager_registry.copy()

    def tearDown(self) -> None:
        """Restore original registry."""
        GraphQL.manager_registry = self._original_registry

    def test_handle_data_change_ignores_none_instance(self) -> None:
        """Verify _handle_data_change does nothing when instance is None."""
        with patch(
            "general_manager.api.graphql.GraphQL._get_channel_layer"
        ) as mock_get_layer:
            GraphQL._handle_data_change(
                sender=GeneralManager, instance=None, action="test"
            )
            mock_get_layer.assert_not_called()

    def test_handle_data_change_ignores_non_manager_instance(self) -> None:
        """Verify _handle_data_change does nothing for non-GeneralManager instances."""
        with patch(
            "general_manager.api.graphql.GraphQL._get_channel_layer"
        ) as mock_get_layer:
            GraphQL._handle_data_change(
                sender=GeneralManager, instance=object(), action="test"
            )  # type: ignore[arg-type]
            mock_get_layer.assert_not_called()

    def test_handle_data_change_ignores_unregistered_manager(self) -> None:
        """Verify _handle_data_change does nothing for managers not in registry."""

        class UnregisteredManager(GeneralManager):
            identification: ClassVar[dict[str, int]] = {"id": 1}

        instance = UnregisteredManager()
        with patch(
            "general_manager.api.graphql.GraphQL._get_channel_layer"
        ) as mock_get_layer:
            GraphQL._handle_data_change(
                sender=UnregisteredManager, instance=instance, action="test"
            )
            mock_get_layer.assert_not_called()

    def test_handle_data_change_ignores_when_no_channel_layer(self) -> None:
        """Verify _handle_data_change does nothing when no channel layer is configured."""

        class RegisteredManager(GeneralManager):
            identification: ClassVar[dict[str, int]] = {"id": 1}

        GraphQL.manager_registry = {"RegisteredManager": RegisteredManager}
        instance = RegisteredManager()

        with patch(
            "general_manager.api.graphql.GraphQL._get_channel_layer", return_value=None
        ):
            # Should not raise
            GraphQL._handle_data_change(
                sender=RegisteredManager, instance=instance, action="test"
            )

    def test_handle_data_change_sends_to_channel_group(self) -> None:
        """Verify _handle_data_change sends message to correct channel group."""

        class RegisteredManager(GeneralManager):
            identification: ClassVar[dict[str, int]] = {"id": 1}

        GraphQL.manager_registry = {"RegisteredManager": RegisteredManager}
        instance = RegisteredManager()

        mock_layer = MagicMock()
        with patch(
            "general_manager.api.graphql.GraphQL._get_channel_layer",
            return_value=mock_layer,
        ):
            with patch(
                "general_manager.api.graphql.async_to_sync"
            ) as mock_async_to_sync:
                mock_send = MagicMock()
                mock_async_to_sync.return_value = mock_send

                GraphQL._handle_data_change(
                    sender=RegisteredManager, instance=instance, action="update"
                )

                # Verify group_send was wrapped with async_to_sync
                mock_async_to_sync.assert_called_once_with(mock_layer.group_send)

                # Verify the send was called with correct arguments
                self.assertEqual(mock_send.call_count, 1)
                call_args = mock_send.call_args[0]
                group_name = call_args[0]
                message = call_args[1]

                self.assertIsInstance(group_name, str)
                self.assertTrue(group_name.startswith("gm_subscriptions."))
                self.assertEqual(message["type"], "gm.subscription.event")
                self.assertEqual(message["action"], "update")


class GraphQLInstantiateManagerTests(GeneralManagerTransactionTestCase):
    """Test manager instantiation with dependency tracking."""

    @classmethod
    def setUpClass(cls) -> None:
        """Set up test manager class."""
        from django.db.models import CharField

        class Product(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        cls.general_manager_classes = [Product]
        cls.Product = Product

    def test_instantiate_without_dependency_collection(self) -> None:
        """Verify _instantiate_manager creates instance without collecting dependencies."""
        identification = {"id": 1}

        with patch.object(self.Product, "__init__", return_value=None) as mock_init:
            _instance, dependencies = GraphQL._instantiate_manager(
                self.Product,
                identification,
                collect_dependencies=False,
            )
            mock_init.assert_called_once_with(id=1)
            self.assertEqual(dependencies, set())

    def test_instantiate_with_dependency_collection(self) -> None:
        """Verify _instantiate_manager collects dependencies when requested."""
        product = self.Product.create(name="TestProduct", ignore_permission=True)
        identification = {"id": product.id}

        instance, dependencies = GraphQL._instantiate_manager(
            self.Product,
            identification,
            collect_dependencies=True,
        )

        self.assertIsInstance(instance, self.Product)
        # Dependencies is a set (may be empty depending on tracker behavior)
        self.assertIsInstance(dependencies, set)

    def test_instantiate_primes_specified_properties(self) -> None:
        """Verify _instantiate_manager primes only specified properties."""
        accessed_properties = []

        class TrackedInterface(BaseTestInterface):
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                return {
                    "prop_a": TrackedManager.prop_a,
                    "prop_b": TrackedManager.prop_b,
                }

        class TrackedManager(GeneralManager):
            class Interface(TrackedInterface):
                pass

            @property
            def prop_a(self) -> int:
                accessed_properties.append("prop_a")
                return 1

            @property
            def prop_b(self) -> int:
                accessed_properties.append("prop_b")
                return 2

        with patch.object(TrackedManager, "__init__", return_value=None):
            _instance = TrackedManager()
            _instance, _ = GraphQL._instantiate_manager(
                TrackedManager,
                {},
                collect_dependencies=True,
                property_names=["prop_a"],
            )

        self.assertIn("prop_a", accessed_properties)
        self.assertNotIn("prop_b", accessed_properties)


class GraphQLResolveSubscriptionDependenciesTests(unittest.TestCase):
    """Test subscription dependency resolution."""

    def test_empty_dependencies_for_simple_manager(self) -> None:
        """Verify _resolve_subscription_dependencies returns empty for managers with no relations."""

        class SimpleInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, object]] = {}

        class SimpleManager(GeneralManager):
            Interface = SimpleInterface
            identification: ClassVar[dict[str, int]] = {"id": 1}
            _interface = SimpleNamespace(identification={"id": 1})

        instance = SimpleManager()
        dependencies = GraphQL._resolve_subscription_dependencies(
            SimpleManager,
            instance,
            None,
        )
        self.assertEqual(dependencies, [])

    def test_excludes_self_reference(self) -> None:
        """Verify _resolve_subscription_dependencies excludes the instance itself."""

        class TestInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, object]] = {}

        class TestManager(GeneralManager):
            Interface = TestInterface
            identification: ClassVar[dict[str, int]] = {"id": 1}

        GraphQL.manager_registry = {"TestManager": TestManager}
        instance = TestManager()
        instance._interface = SimpleNamespace(identification={"id": 1})

        dependency_records = [
            ("TestManager", "identification", "{'id': 1}"),
        ]

        dependencies = GraphQL._resolve_subscription_dependencies(
            TestManager,
            instance,
            dependency_records,
        )

        self.assertEqual(dependencies, [])

    def test_deduplicates_dependencies(self) -> None:
        """Verify _resolve_subscription_dependencies removes duplicate dependencies."""

        class RelatedInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, object]] = {}

        class RelatedManager(GeneralManager):
            Interface = RelatedInterface

        class TestInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, object]] = {}

        class TestManager(GeneralManager):
            Interface = TestInterface
            identification: ClassVar[dict[str, int]] = {"id": 1}

        GraphQL.manager_registry = {
            "TestManager": TestManager,
            "RelatedManager": RelatedManager,
        }
        instance = TestManager()
        instance._interface = SimpleNamespace(identification={"id": 1})

        dependency_records = [
            ("RelatedManager", "identification", "{'id': 2}"),
            ("RelatedManager", "identification", "{'id': 2}"),  # Duplicate
            ("RelatedManager", "identification", "{'id': 3}"),
        ]

        dependencies = GraphQL._resolve_subscription_dependencies(
            TestManager,
            instance,
            dependency_records,
        )

        self.assertEqual(len(dependencies), 2)
        ids = [dep[1]["id"] for dep in dependencies]
        self.assertIn(2, ids)
        self.assertIn(3, ids)


class GraphQLSubscriptionChannelListenerTests(unittest.TestCase):
    """Test channel listener coroutine."""

    def test_channel_listener_enqueues_actions(self) -> None:
        """Verify _channel_listener enqueues action strings from subscription events."""

        async def test_listener() -> list[str]:
            """
            Runs the GraphQL channel listener against a mock channel layer, cancels it, and returns the actions it enqueued.

            The mock layer yields a sequence of messages including some non-subscription messages and one message without an `action`; the listener should enqueue only the `action` values from messages with `type` equal to "gm.subscription.event" that include an `action` field. The listener is cancelled after processing the supplied messages.

            Returns:
                list[str]: The ordered list of enqueued action strings collected from the listener.
            """
            mock_layer = MagicMock()
            # Simulate receiving messages
            messages = [
                {"type": "gm.subscription.event", "action": "update"},
                {"type": "gm.subscription.event", "action": "delete"},
                {"type": "other.message", "action": "ignored"},
                {"type": "gm.subscription.event"},  # No action
            ]
            message_iter = iter(messages)

            async def mock_receive(_channel: str) -> dict[str, Any]:
                try:
                    await asyncio.sleep(0.001)  # Simulate async delay
                    return next(message_iter)
                except StopIteration as err:
                    # Simulate cancellation after messages exhausted
                    await asyncio.sleep(10)
                    raise AssertionError("Cancelled") from err

            mock_layer.receive = mock_receive

            queue: asyncio.Queue[str] = asyncio.Queue()
            listener_task = asyncio.create_task(
                GraphQL._channel_listener(mock_layer, "test_channel", queue)
            )

            # Allow listener to process messages
            await asyncio.sleep(0.01)

            # Cancel the listener
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass

            # Collect enqueued actions
            actions = []
            while not queue.empty():
                actions.append(await queue.get())

            return actions

        actions = asyncio.run(test_listener())
        self.assertEqual(actions, ["update", "delete"])

    def test_channel_listener_handles_cancellation(self) -> None:
        """Verify _channel_listener exits gracefully when cancelled."""

        async def test_cancellation() -> None:
            mock_layer = MagicMock()

            async def mock_receive(_channel: str) -> dict[str, Any]:
                # Simulate long-running receive
                await asyncio.sleep(10)
                return {}

            mock_layer.receive = mock_receive
            queue: asyncio.Queue[str] = asyncio.Queue()

            listener_task = asyncio.create_task(
                GraphQL._channel_listener(mock_layer, "test_channel", queue)
            )

            await asyncio.sleep(0.001)
            listener_task.cancel()

            # Should not raise, just exit
            try:
                await listener_task
            except asyncio.CancelledError:
                pass  # Expected

        # Should complete without hanging
        asyncio.run(test_cancellation())


class GraphQLSchemaAccessTests(unittest.TestCase):
    """Test schema getter method."""

    def test_get_schema_returns_none_when_not_set(self) -> None:
        """Verify get_schema returns None when no schema has been created."""
        original_schema = GraphQL._schema
        try:
            GraphQL._schema = None
            schema = GraphQL.get_schema()
            self.assertIsNone(schema)
        finally:
            GraphQL._schema = original_schema

    def test_get_schema_returns_configured_schema(self) -> None:
        """Verify get_schema returns the configured schema when available."""
        original_schema = GraphQL._schema
        try:
            mock_schema = object()
            GraphQL._schema = mock_schema  # type: ignore[assignment]
            schema = GraphQL.get_schema()
            self.assertIs(schema, mock_schema)
        finally:
            GraphQL._schema = original_schema
