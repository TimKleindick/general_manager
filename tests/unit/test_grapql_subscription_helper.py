# type: ignore[file-ignores]
"""Unit tests for GraphQL subscription helper methods and utilities."""

import asyncio
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch
import unittest

from graphql import parse
from graphql.language.ast import FragmentDefinitionNode, OperationDefinitionNode

from general_manager.api.graphql import GraphQL, SubscriptionEvent
from general_manager.manager.general_manager import GeneralManager
from tests.utils.simple_manager_interface import BaseTestInterface


class SubscriptionEventTests(unittest.TestCase):
    """Test SubscriptionEvent dataclass."""

    def test_subscription_event_creation(self) -> None:
        """Verify SubscriptionEvent can be created with item and action."""
        item = {"id": 1, "name": "test"}
        event = SubscriptionEvent(item=item, action="update")

        self.assertEqual(event.item, item)
        self.assertEqual(event.action, "update")

    def test_subscription_event_with_none_item(self) -> None:
        """Verify SubscriptionEvent allows None as item value."""
        event = SubscriptionEvent(item=None, action="delete")

        self.assertIsNone(event.item)
        self.assertEqual(event.action, "delete")


class GraphQLGroupNameDeterminismTests(unittest.TestCase):
    """Test group name generation determinism and uniqueness."""

    def test_group_name_json_serialization_order(self) -> None:
        """Verify _group_name produces same result regardless of dict key order."""

        class TestManager(GeneralManager):
            pass

        # Same keys, different insertion order
        id1 = {"a": 1, "b": 2, "c": 3}
        id2 = {"c": 3, "a": 1, "b": 2}

        name1 = GraphQL._group_name(TestManager, id1)
        name2 = GraphQL._group_name(TestManager, id2)

        self.assertEqual(name1, name2)

    def test_group_name_contains_manager_name(self) -> None:
        """Verify _group_name includes manager class name in the result."""

        class UniqueManager(GeneralManager):
            pass

        name = GraphQL._group_name(UniqueManager, {"id": 1})

        self.assertIn("UniqueManager", name)

    def test_group_name_hash_length(self) -> None:
        """Verify _group_name produces hash of expected length (32 chars)."""

        class TestManager(GeneralManager):
            pass

        name = GraphQL._group_name(TestManager, {"id": 1})

        # Format: "gm_subscriptions.TestManager.<32-char-hash>"
        parts = name.split(".")
        self.assertEqual(len(parts), 3)
        self.assertEqual(len(parts[2]), 32)

    def test_group_name_handles_special_characters(self) -> None:
        """Verify _group_name handles identification with special characters."""

        class TestManager(GeneralManager):
            pass

        identification = {
            "name": "test@example.com",
            "path": "/path/to/resource",
            "emoji": "🎉",
        }

        # Should not raise
        name = GraphQL._group_name(TestManager, identification)
        self.assertIsInstance(name, str)


class GraphQLChannelLayerStrictModeTests(unittest.TestCase):
    """Test strict mode behavior for channel layer retrieval."""

    def test_strict_mode_error_message(self) -> None:
        """Verify strict mode raises RuntimeError with helpful message."""
        with patch("general_manager.api.graphql.get_channel_layer", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                GraphQL._get_channel_layer(strict=True)

            error_message = str(ctx.exception)
            self.assertIn("channel layer", error_message.lower())
            self.assertIn("configured", error_message.lower())


class GraphQLPrimePropertiesWithExceptionsTests(unittest.TestCase):
    """Test property priming edge cases and exceptions."""

    def test_prime_properties_preserves_exception_type(self) -> None:
        """Verify _prime_graphql_properties preserves original exception types."""

        class CustomError(Exception):
            pass

        class ExceptionInterface(BaseTestInterface):
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                return {"bad": ExceptionManager.bad}

        class ExceptionManager:
            class Interface(ExceptionInterface):
                pass

            @property
            def bad(self) -> int:
                error_msg = "Custom error message"
                raise CustomError(error_msg)

        ExceptionManager.Interface._parent_class = ExceptionManager

        instance = ExceptionManager()

        with self.assertRaises(CustomError) as ctx:
            GraphQL._prime_graphql_properties(instance)

        self.assertEqual(str(ctx.exception), "Custom error message")

    def test_prime_properties_handles_attribute_error(self) -> None:
        """Verify _prime_graphql_properties handles properties that raise AttributeError."""

        class MissingAttrInterface(BaseTestInterface):
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                """
                Return a mapping of GraphQL property names to their property objects for the given manager class.

                Parameters:
                    cls (type): Manager class whose GraphQL properties should be collected.

                Returns:
                    dict[str, object]: A dictionary mapping property names to the corresponding property objects.
                """
                return {"prop": MissingAttrManager.prop}

        class MissingAttrManager:
            class Interface(MissingAttrInterface):
                pass

            @property
            def prop(self) -> int:
                error_msg = "Property not available"
                raise AttributeError(error_msg)

        MissingAttrManager.Interface._parent_class = MissingAttrManager

        instance = MissingAttrManager()

        with self.assertRaises(AttributeError):
            GraphQL._prime_graphql_properties(instance)


class GraphQLDependencyTrackerComplexCasesTests(unittest.TestCase):
    """Test complex scenarios in dependency extraction."""

    def setUp(self) -> None:
        """Set up test manager registry."""
        self._original_registry = GraphQL.manager_registry.copy()

        class ManagerA(GeneralManager):
            class Interface(BaseTestInterface):
                @classmethod
                def getGraphQLProperties(cls) -> dict[str, object]:
                    return {}

        class ManagerB(GeneralManager):
            class Interface(BaseTestInterface):
                @classmethod
                def getGraphQLProperties(cls) -> dict[str, object]:
                    return {}

        GraphQL.manager_registry = {
            "ManagerA": ManagerA,
            "ManagerB": ManagerB,
        }
        self.manager_a = ManagerA
        self.manager_b = ManagerB

    def tearDown(self) -> None:
        """Restore original registry."""
        GraphQL.manager_registry = self._original_registry

    def test_dependencies_with_nested_dicts(self) -> None:
        """Verify _dependencies_from_tracker handles nested dict identifications."""
        records = [
            ("ManagerA", "identification", "{'id': 1, 'meta': {'key': 'value'}}"),
        ]

        extracted = GraphQL._dependencies_from_tracker(records)

        self.assertEqual(len(extracted), 1)
        self.assertEqual(extracted[0][1], {"id": 1, "meta": {"key": "value"}})

    def test_dependencies_with_multiple_managers(self) -> None:
        """Verify _dependencies_from_tracker handles records from multiple managers."""
        records = [
            ("ManagerA", "identification", "{'id': 1}"),
            ("ManagerB", "identification", "{'id': 2}"),
            ("ManagerA", "identification", "{'id': 3}"),
        ]

        extracted = GraphQL._dependencies_from_tracker(records)

        self.assertEqual(len(extracted), 3)
        manager_names = [dep[0].__name__ for dep in extracted]
        self.assertEqual(manager_names.count("ManagerA"), 2)
        self.assertEqual(manager_names.count("ManagerB"), 1)

    def test_dependencies_with_special_python_literals(self) -> None:
        """Verify _dependencies_from_tracker handles various Python literal types."""
        records = [
            ("ManagerA", "identification", "{'active': True, 'count': 0, 'ref': None}"),
        ]

        extracted = GraphQL._dependencies_from_tracker(records)

        self.assertEqual(len(extracted), 1)
        identification = extracted[0][1]
        self.assertIs(identification["active"], True)
        self.assertEqual(identification["count"], 0)
        self.assertIsNone(identification["ref"])


class GraphQLSubscriptionPropertySelectionAdvancedTests(unittest.TestCase):
    """Test advanced property selection scenarios."""

    @staticmethod
    def _build_info(query: str) -> SimpleNamespace:
        """Build minimal GraphQL info object."""
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

    def test_deeply_nested_inline_fragments(self) -> None:
        """Verify _subscription_property_names handles deeply nested inline fragments."""

        class TestInterface(BaseTestInterface):
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                """
                Provide the default mapping of GraphQL-selectable property names for the class.

                Returns:
                    dict[str, object]: A dictionary mapping property names ("propA", "propB", "propC") to placeholder objects representing those GraphQL properties.
                """
                return {"propA": object(), "propB": object(), "propC": object()}

        class TestManager:
            Interface = TestInterface

        TestInterface._parent_class = TestManager

        info = self._build_info(
            """
            subscription {
                onTestChange(id: "1") {
                    item {
                        ... on TestManagerType {
                            propA
                            ... on TestManagerType {
                                propB
                                ... on TestManagerType {
                                    propC
                                }
                            }
                        }
                    }
                }
            }
            """
        )

        property_names = GraphQL._subscription_property_names(info, TestManager)  # type: ignore[arg-type]
        self.assertEqual(property_names, {"propA", "propB", "propC"})

    def test_property_selection_with_directives(self) -> None:
        """Verify _subscription_property_names handles fields with directives."""

        class TestInterface(BaseTestInterface):
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                """
                Return the mapping of GraphQL-exposed property names to their descriptor objects for the given class.

                Parameters:
                    cls: The class whose GraphQL properties are being described.

                Returns:
                    A dict mapping property name (str) to a property descriptor/object for that property.
                """
                return {"propA": object(), "propB": object()}

        class TestManager:
            Interface = TestInterface

        TestInterface._parent_class = TestManager

        info = self._build_info(
            """
            subscription {
                onTestChange(id: "1") {
                    item {
                        propA @include(if: true)
                        propB @skip(if: false)
                    }
                }
            }
            """
        )

        property_names = GraphQL._subscription_property_names(info, TestManager)  # type: ignore[arg-type]
        # Should extract both properties regardless of directives
        self.assertEqual(property_names, {"propA", "propB"})

    def test_empty_item_selection(self) -> None:
        """
        Ensure _subscription_property_names yields no property names when the selection for `item` contains only `__typename`.

        Builds a subscription info object whose `item` selection includes only `__typename` and asserts the extracted property name set is empty.
        """

        class TestInterface(BaseTestInterface):
            @classmethod
            def getGraphQLProperties(cls) -> dict[str, object]:
                """
                Map GraphQL property names to their descriptor objects (placeholder values).

                Returns:
                    A dictionary mapping GraphQL property names to descriptor objects; each value is a placeholder object.
                """
                return {"propA": object()}

        class TestManager:
            Interface = TestInterface

        TestInterface._parent_class = TestManager

        info = self._build_info(
            """
            subscription {
                onTestChange(id: "1") {
                    action
                    item {
                        __typename
                    }
                }
            }
            """
        )

        property_names = GraphQL._subscription_property_names(info, TestManager)  # type: ignore[arg-type]
        self.assertEqual(property_names, set())


class GraphQLBuildIdentificationArgumentsEdgeCasesTests(unittest.TestCase):
    """Test edge cases in identification argument building."""

    def test_empty_input_fields(self) -> None:
        """Verify _buildIdentificationArguments handles managers with no input fields."""

        class EmptyInterface(BaseTestInterface):
            input_fields: ClassVar = {}

        class EmptyManager(GeneralManager):
            Interface = EmptyInterface

        args = GraphQL._buildIdentificationArguments(EmptyManager)
        self.assertEqual(args, {})

    def test_mixed_field_types(self) -> None:
        """Verify _buildIdentificationArguments handles various field types correctly."""

        class RelatedManager(GeneralManager):
            pass

        class MixedInterface(BaseTestInterface):
            input_fields: ClassVar = {
                "id": SimpleNamespace(type=int),
                "name": SimpleNamespace(type=str),
                "active": SimpleNamespace(type=bool),
                "parent": SimpleNamespace(type=RelatedManager),
            }

        class MixedManager(GeneralManager):
            Interface = MixedInterface

        args = GraphQL._buildIdentificationArguments(MixedManager)

        self.assertIn("id", args)
        self.assertIn("name", args)
        self.assertIn("active", args)
        self.assertIn("parent_id", args)
        self.assertNotIn("parent", args)  # Should be parent_id instead


class GraphQLInstantiateManagerAdvancedTests(unittest.TestCase):
    """Test advanced manager instantiation scenarios."""

    def test_instantiate_with_complex_identification(self) -> None:
        """Verify _instantiate_manager handles complex identification dicts."""
        call_args = []

        class ComplexManager(GeneralManager):
            def __init__(self, **kwargs: Any) -> None:
                call_args.append(kwargs)

        identification = {
            "id": 123,
            "slug": "test-item",
            "category_id": 456,
        }

        _, _ = GraphQL._instantiate_manager(
            ComplexManager,
            identification,
            collect_dependencies=False,
        )

        self.assertEqual(len(call_args), 1)
        self.assertEqual(call_args[0], identification)

    def test_instantiate_raises_on_init_error(self) -> None:
        """Verify _instantiate_manager propagates exceptions from __init__."""
        error_msg = "Initialization failed"

        class FailingManager(GeneralManager):
            def __init__(self, **_kwargs: Any) -> None:
                raise ValueError(error_msg)

        with self.assertRaises(ValueError) as ctx:
            GraphQL._instantiate_manager(
                FailingManager,
                {"id": 1},
                collect_dependencies=False,
            )

        self.assertIn("Initialization failed", str(ctx.exception))


class GraphQLResolveSubscriptionDependenciesAdvancedTests(unittest.TestCase):
    """Test advanced dependency resolution scenarios."""

    def test_dependencies_with_list_values(self) -> None:
        """Verify _resolve_subscription_dependencies handles list-valued input fields."""

        class RelatedManager(GeneralManager):
            identification: ClassVar = {"id": 2}

        class TestInterface(BaseTestInterface):
            input_fields: ClassVar = {
                "tags": SimpleNamespace(type=RelatedManager),
            }

        class TestManager(GeneralManager):
            Interface = TestInterface
            identification: ClassVar = {"id": 1}

        instance = TestManager.__new__(TestManager)
        instance._interface = SimpleNamespace(
            identification={"tags": [{"id": 10}, {"id": 20}]}
        )

        dependencies = GraphQL._resolve_subscription_dependencies(
            TestManager,
            instance,
            None,
        )

        # Should extract both items from the list
        self.assertEqual(len(dependencies), 2)
        ids = {dep[1]["id"] for dep in dependencies}
        self.assertEqual(ids, {10, 20})

    def test_dependencies_with_manager_instances(self) -> None:
        """Verify _resolve_subscription_dependencies handles GeneralManager instances."""

        class RelatedManager(GeneralManager):
            identification: ClassVar = {"id": 99}

        class TestInterface(BaseTestInterface):
            input_fields: ClassVar = {
                "parent": SimpleNamespace(type=RelatedManager),
            }

        class TestManager(GeneralManager):
            Interface = TestInterface
            identification: ClassVar = {"id": 1}

        parent_instance = RelatedManager.__new__(RelatedManager)
        parent_instance.identification = {"id": 50}

        instance = TestManager.__new__(TestManager)
        instance._interface = SimpleNamespace(
            identification={"parent": parent_instance}
        )

        dependencies = GraphQL._resolve_subscription_dependencies(
            TestManager,
            instance,
            None,
        )

        self.assertEqual(len(dependencies), 1)
        self.assertEqual(dependencies[0][0], RelatedManager)
        self.assertEqual(dependencies[0][1]["id"], 50)

    def test_dependencies_ignores_none_values(self) -> None:
        """Verify _resolve_subscription_dependencies skips None input field values."""

        class RelatedManager(GeneralManager):
            pass

        class TestInterface(BaseTestInterface):
            input_fields: ClassVar = {
                "parent": SimpleNamespace(type=RelatedManager),
            }

        class TestManager(GeneralManager):
            Interface = TestInterface
            identification: ClassVar = {"id": 1}

        instance = TestManager.__new__(TestManager)
        instance._interface = SimpleNamespace(identification={"parent": None})

        dependencies = GraphQL._resolve_subscription_dependencies(
            TestManager,
            instance,
            None,
        )

        self.assertEqual(dependencies, [])


class GraphQLChannelListenerRobustnessTests(unittest.TestCase):
    """Test channel listener error handling and robustness."""

    def test_channel_listener_ignores_malformed_messages(self) -> None:
        """Verify _channel_listener ignores messages without required fields."""

        async def test_listener() -> list[str]:
            """
            Run a mocked channel listener and collect enqueued action strings.

            The test starts GraphQL._channel_listener with a mock channel layer that yields a sequence of messages
            (some malformed). It cancels the listener after a short time and gathers any action values that were
            put into the provided queue.

            Returns:
                actions (list[str]): FIFO-ordered list of action strings that were enqueued by the listener.
            """
            mock_layer = MagicMock()
            messages = [
                {"type": "gm.subscription.event", "action": "valid"},
                {"type": "gm.subscription.event"},  # No action
                {"action": "no_type"},  # No type
                {},  # Empty message
            ]
            message_iter = iter(messages)

            cancel_msg = "Should be cancelled"

            async def mock_receive(_channel: str) -> dict[str, Any]:
                try:
                    await asyncio.sleep(0.001)
                    return next(message_iter)
                except StopIteration:
                    await asyncio.sleep(10)
                    raise AssertionError(cancel_msg) from None

            mock_layer.receive = mock_receive
            queue: asyncio.Queue[str] = asyncio.Queue()

            listener_task = asyncio.create_task(
                GraphQL._channel_listener(mock_layer, "test", queue)
            )

            await asyncio.sleep(0.01)
            listener_task.cancel()

            try:
                await listener_task
            except asyncio.CancelledError:
                pass

            actions = []
            while not queue.empty():
                actions.append(await queue.get())

            return actions

        actions = asyncio.run(test_listener())
        # Should only capture the one valid action
        self.assertEqual(actions, ["valid"])


class GraphQLHandleDataChangeEdgeCasesTests(unittest.TestCase):
    """Test edge cases in data change signal handling."""

    def setUp(self) -> None:
        """Save original registry."""
        self._original_registry = GraphQL.manager_registry.copy()

    def tearDown(self) -> None:
        """Restore original registry."""
        GraphQL.manager_registry = self._original_registry

    def test_handle_data_change_with_instance_as_sender(self) -> None:
        """Verify _handle_data_change extracts manager class from instance sender."""

        class TestManager(GeneralManager):
            identification: ClassVar = {"id": 1}

        GraphQL.manager_registry = {"TestManager": TestManager}
        instance = TestManager()

        mock_layer = MagicMock()
        with patch(
            "general_manager.api.graphql.GraphQL._get_channel_layer",
            return_value=mock_layer,
        ):
            with patch("general_manager.api.graphql.async_to_sync") as mock_async:
                mock_send = MagicMock()
                mock_async.return_value = mock_send

                # Pass instance as both sender and instance
                GraphQL._handle_data_change(
                    sender=instance, instance=instance, action="test"
                )

                # Should still work correctly
                mock_send.assert_called_once()

    def test_handle_data_change_with_subclass(self) -> None:
        """Verify _handle_data_change works with GeneralManager subclasses."""

        class BaseManager(GeneralManager):
            identification: ClassVar = {"id": 1}

        class DerivedManager(BaseManager):
            pass

        GraphQL.manager_registry = {"DerivedManager": DerivedManager}
        instance = DerivedManager()

        mock_layer = MagicMock()
        with patch(
            "general_manager.api.graphql.GraphQL._get_channel_layer",
            return_value=mock_layer,
        ):
            with patch("general_manager.api.graphql.async_to_sync") as mock_async:
                mock_send = MagicMock()
                mock_async.return_value = mock_send

                GraphQL._handle_data_change(
                    sender=DerivedManager, instance=instance, action="test"
                )

                mock_send.assert_called_once()
