# type: ignore

import json
from decimal import Decimal
from datetime import date, datetime
import graphene
from graphene.types.structures import NonNull
from django.test import TestCase
from unittest.mock import MagicMock, patch
from django.contrib.auth.models import AnonymousUser
from typing import ClassVar

from general_manager.api.graphql import (
    MeasurementType,
    GraphQL,
    get_read_permission_filter,
)
from general_manager.measurement.measurement import Measurement
from general_manager.manager.general_manager import GeneralManager, GeneralManagerMeta
from general_manager.manager.input import Input
from general_manager.api.property import GraphQLProperty
from general_manager.interface.base_interface import InterfaceBase
from graphql import GraphQLError


class GraphQLPropertyTests(TestCase):
    def test_graphql_property_initialization(self):
        def mock_getter():
            return "test"

        with self.assertRaises(
            TypeError,
            msg="GraphQLProperty requires a return type hint for the property function.",
        ):
            GraphQLProperty(mock_getter)

    def test_graphql_property_with_type_hint(self):
        def mock_getter() -> str:
            return "test"

        prop = GraphQLProperty(mock_getter)
        self.assertEqual(prop.graphql_type_hint, str)


class MeasurementTypeTests(TestCase):
    def test_measurement_type_fields(self):
        for field in ["value", "unit"]:
            self.assertTrue(hasattr(MeasurementType, field))


class GraphQLTests(TestCase):
    def setUp(self):
        self.general_manager_class = MagicMock(spec=GeneralManagerMeta)
        self.general_manager_class.__name__ = "TestManager"
        self.info = MagicMock()
        self.info.context.user = AnonymousUser()

    @patch("general_manager.interface.base_interface.InterfaceBase")
    def test_create_graphql_interface_no_interface(self, _mock_interface):
        self.general_manager_class.Interface = None
        result = GraphQL.create_graphql_interface(self.general_manager_class)
        self.assertIsNone(result)

    @patch("general_manager.interface.base_interface.InterfaceBase")
    def test_create_graphql_interface_with_interface(self, mock_interface):
        mock_interface.get_attribute_types.return_value = {"test_field": {"type": str}}
        self.general_manager_class.Interface = mock_interface
        with patch("general_manager.api.graphql.issubclass", return_value=True):
            GraphQL.create_graphql_interface(self.general_manager_class)
            self.assertIn("TestManager", GraphQL.graphql_type_registry)

    def test_map_field_to_graphene(self):
        # Base types
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(str, "name"), graphene.String
        )
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(int, "age"), graphene.Int
        )
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(float, "value"), graphene.Float
        )
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(Decimal, "decimal"), graphene.Float
        )
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(bool, "active"), graphene.Boolean
        )
        self.assertIsInstance(
            GraphQL._map_field_to_graphene_read(date, "birth_date"), graphene.Date
        )
        field = GraphQL._map_field_to_graphene_read(Measurement, "measurement")
        self.assertIsInstance(field, graphene.Field)

    def test_create_resolver_normal_case(self):
        mock_instance = MagicMock()
        mock_instance.some_field = "expected_value"
        resolver = GraphQL._create_resolver("some_field", str)
        self.assertEqual(resolver(mock_instance, self.info), "expected_value")

    def test_create_resolver_measurement_case(self):
        mock_instance = MagicMock()
        mock_measurement = Measurement(100, "cm")
        mock_instance.measurement_field = mock_measurement

        resolver = GraphQL._create_resolver("measurement_field", Measurement)
        result = resolver(mock_instance, self.info, target_unit="cm")
        self.assertEqual(result, {"value": Decimal(100), "unit": "centimeter"})

    def test_create_resolver_list_case(self):
        mock_instance = MagicMock()
        mock_queryset = MagicMock()
        mock_filtered = MagicMock()
        mock_queryset.filter.return_value = mock_filtered
        mock_filtered.exclude.return_value = mock_filtered
        # Assign the queryset directly
        mock_instance.abc_list = mock_queryset

        resolver = GraphQL._create_resolver("abc_list", GeneralManager)
        with patch("json.loads", side_effect=json.loads):
            resolver(
                mock_instance,
                self.info,
                filter=json.dumps({"field": "value"}),
                exclude=json.dumps({"other_field": "value"}),
            )
            mock_queryset.filter.assert_called_with(field="value")
            mock_filtered.exclude.assert_called_with(other_field="value")

    @patch("general_manager.interface.base_interface.InterfaceBase")
    def test_create_graphql_interface_graphql_property(self, mock_interface):
        """
        Test that a GraphQL interface is created and registered when a manager class defines a GraphQLProperty attribute.
        """

        class TestManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {"test_field": {"type": str}}

            @classmethod
            def all(cls):
                return []

        def prop_func() -> int:
            return 42

        mock_interface.get_attribute_types.return_value = {"test_field": {"type": str}}
        with patch("general_manager.api.graphql.issubclass", return_value=True):
            TestManager.test_prop = GraphQLProperty(prop_func)
            GraphQL.create_graphql_interface(TestManager)
            self.assertIn("TestManager", GraphQL.graphql_type_registry)

    def test_list_resolver_with_invalid_filter_exclude(self):
        """
        Test that the list resolver returns the original queryset when filter or exclude arguments are invalid JSON.

        If JSON decoding fails for the filter or exclude parameters, ensures the resolver returns the unfiltered queryset under the "items" key.
        """
        mock_instance = MagicMock()
        mock_qs = MagicMock()
        mock_instance.abc_list = mock_qs
        resolver = GraphQL._create_resolver("abc_list", GeneralManager)
        with patch("json.loads", side_effect=ValueError):
            result = resolver(mock_instance, self.info, filter="bad", exclude="bad")
            self.assertEqual(result["items"], mock_qs)

    def test_create_filter_options_measurement_fields(self):
        """
        Tests that filter options are generated for numeric, string, and measurement fields, and that fields of type GeneralManager are excluded from the filter options.
        """

        class DummyManager:
            __name__ = "DummyManager"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {
                        "num_field": {"type": int},
                        "str_field": {"type": str},
                        "measurement_field": {"type": Measurement},
                        "gm_field": {"type": GeneralManager},
                    }

        GraphQL.graphql_filter_type_registry.clear()
        filter_cls = GraphQL._create_filter_options(DummyManager)
        fields = filter_cls._meta.fields
        self.assertNotIn("gm_field", fields)
        for key in [
            "num_field",
            *[f"num_field__{opt}" for opt in ["exact", "gt", "gte", "lt", "lte"]],
        ]:
            self.assertIn(key, fields)
        for key in [
            "str_field",
            *[
                f"str_field__{opt}"
                for opt in [
                    "exact",
                    "icontains",
                    "contains",
                    "in",
                    "startswith",
                    "endswith",
                ]
            ],
        ]:
            self.assertIn(key, fields)

    def test_create_filter_options_registry_cache(self):
        """
        Test that repeated calls to `_create_filter_options` with the same manager class and name return the same cached filter input type instance.

        Ensures the filter options registry caches and reuses filter input types for identical manager class and name combinations.
        """

        class DummyManager2:
            __name__ = "DummyManager2"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def get_attribute_types():
                    return {"num_field": {"type": int}}

        GraphQL.graphql_filter_type_registry.clear()
        first = GraphQL._create_filter_options(DummyManager2)
        second = GraphQL._create_filter_options(DummyManager2)
        self.assertIs(first, second)

    def test_build_identification_arguments_respects_optional_inputs(self):
        class DependencyManager(GeneralManager):
            pass

        class DummyManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {
                    "id": Input(int, required=True),
                    "as_of": Input(date, required=False),
                    "dependency": Input(DependencyManager, required=False),
                }

                @staticmethod
                def get_attribute_types():
                    return {}

        arguments = GraphQL._build_identification_arguments(DummyManager)

        self.assertIsInstance(arguments["id"].type, NonNull)
        self.assertNotIsInstance(arguments["as_of"].type, NonNull)
        self.assertNotIsInstance(arguments["dependency_id"].type, NonNull)


class TestGetReadPermissionFilter(TestCase):
    def test_get_read_permission_filter(self):
        """
        Verify that get_read_permission_filter extracts and returns filter and exclude tuples from a manager's permission class.
        """

        class DummyManager:
            __name__ = "DummyManager"

            class Permission:
                def __init__(self, *args, **_kwargs):
                    self.args = args

                def get_permission_filter(self):
                    return [{"filter": {"num_field__exact": 42}, "exclude": {}}]

        info = MagicMock()
        info.context.user = AnonymousUser()
        result = get_read_permission_filter(DummyManager, info)
        expected = [({"num_field__exact": 42}, {})]
        self.assertEqual(result, expected)


class TestGrapQlMutation(TestCase):
    def setUp(self) -> None:
        """
        Set up dummy manager classes and reset the GraphQL mutation registry for mutation-related tests.

        Defines mock manager classes with various interface methods to simulate different mutation scenarios, assigns them to instance attributes, and clears the GraphQL mutation registry to ensure test isolation.
        """

        class DummyManager:
            class Interface:
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_capabilities(cls):
                    """
                    Return the set of capabilities supported by the interface.

                    Returns:
                        frozenset: A frozenset containing "create", "update", and "delete".
                    """
                    return frozenset({"create", "update", "delete"})

                @classmethod
                def create(cls, *_args, **kwargs):
                    """
                    Create a new instance of the class using the provided positional and keyword arguments.

                    Parameters:
                        *args: Positional arguments forwarded to the class constructor.
                        **kwargs: Keyword arguments forwarded to the class constructor.

                    Returns:
                        instance: A newly created instance of `cls`.
                    """
                    pass

                def update(self, *_args, **kwargs):
                    """
                    Apply provided attribute updates to this instance and return the modified instance.

                    Updates attributes on the instance using keyword arguments where keys are attribute names and values are the new values to set. Positional arguments are ignored.

                    Parameters:
                        **kwargs: Mapping of attribute names to values to assign on the instance.

                    Returns:
                        The same instance after applying the updates.
                    """
                    pass

                def delete(self, *_args, **kwargs):
                    """
                    Delete this manager instance and return it.

                    Returns:
                        self: The same manager instance after deletion.
                    """
                    pass

        class DummyManager2:
            class Interface(InterfaceBase):
                def get_data(self, search_date: datetime | None = None):
                    """
                    Raises NotImplementedError to indicate that data retrieval is not implemented.

                    Parameters:
                        search_date (datetime, optional): An optional date to specify the context for data retrieval.
                    """
                    raise NotImplementedError

                @classmethod
                def get_attribute_types(cls):
                    """
                    Raises a NotImplementedError to indicate that subclasses must implement this method to return attribute type information.
                    """
                    raise NotImplementedError

                @classmethod
                def get_attributes(cls):
                    """
                    Raises a NotImplementedError to indicate that subclasses must implement the get_attributes method.
                    """
                    raise NotImplementedError

                @classmethod
                def filter(cls, **kwargs) -> None:
                    """
                    Raises a NotImplementedError to indicate that the filter method must be implemented by subclasses.
                    """
                    raise NotImplementedError

                @classmethod
                def exclude(cls, **kwargs) -> None:
                    """
                    Raises a NotImplementedError to indicate that the exclude operation is not implemented for this class.
                    """
                    raise NotImplementedError

                @classmethod
                def handle_interface(
                    cls,
                ) -> None:
                    """
                    Initializes or registers interface-related components for the class.

                    Intended to be called on a class to perform setup required for its interface functionality.
                    """
                    pass

                @classmethod
                def get_field_type(cls, field_name: str) -> None:
                    """
                    Get the declared type of the named attribute on the class.

                    Parameters:
                        field_name (str): The attribute name whose declared type should be retrieved.

                    Returns:
                        The attribute's type if available, otherwise None.
                    """
                    pass

                @classmethod
                def get_capabilities(cls):
                    """
                    Provide the set of capability names supported by this interface.

                    Returns:
                        frozenset: A frozenset of capability name strings (e.g., "create", "update", "delete"). Empty frozenset if no capabilities are supported.
                    """
                    return frozenset()

        self.manager = DummyManager
        self.manager2 = DummyManager2
        GraphQL._mutations = {}

    @patch("general_manager.api.graphql.GraphQL.generate_create_mutation_class")
    @patch("general_manager.api.graphql.GraphQL.generate_update_mutation_class")
    @patch("general_manager.api.graphql.GraphQL.generate_delete_mutation_class")
    def test_create_graphql_mutation(
        self, mock_delete: MagicMock, mock_update: MagicMock, mock_create: MagicMock
    ):
        """
        Tests that GraphQL.create_graphql_mutation generates and registers create, update, and delete mutation classes for a manager with the corresponding methods, and that the mutation generation methods are called exactly once.
        """
        GraphQL.create_graphql_mutation(self.manager)
        mock_create.assert_called_once()
        mock_update.assert_called_once()
        mock_delete.assert_called_once()
        self.assertEqual(
            list(GraphQL._mutations.keys()),
            ["createDummyManager", "updateDummyManager", "deleteDummyManager"],
        )

    @patch("general_manager.api.graphql.GraphQL.generate_create_mutation_class")
    @patch("general_manager.api.graphql.GraphQL.generate_update_mutation_class")
    @patch("general_manager.api.graphql.GraphQL.generate_delete_mutation_class")
    def test_create_graphql_mutation_with_undefined_create_update_delete(
        self, mock_delete: MagicMock, mock_update: MagicMock, mock_create: MagicMock
    ):
        """
        Test that no mutation classes are generated if the manager lacks create, update, and delete methods.

        Ensures that the mutation generation functions for create, update, and delete are not called when the manager does not define these methods.
        """
        GraphQL.create_graphql_mutation(self.manager2)
        mock_create.assert_not_called()
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    def test_create_write_fields(self):
        """
        Tests that `GraphQL.create_write_fields` returns input fields only for editable, non-derived attributes, mapping their types correctly and excluding derived fields.
        """

        class DummyInterface:
            @staticmethod
            def get_attribute_types():
                """
                Return metadata for each attribute, including type, requirement, derivation, default value, and editability.

                Returns:
                    dict: Maps attribute names to metadata describing their type, whether they are required or derived, their default value, and if they are editable.
                """
                return {
                    "field1": {
                        "type": str,
                        "is_required": True,
                        "is_derived": False,
                        "default": "default_value",
                        "is_editable": True,
                    },
                    "field2": {
                        "type": int,
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": False,
                    },
                    "created_at": {
                        "type": datetime,
                        "is_required": False,
                        "is_derived": True,
                        "default": None,
                        "is_editable": False,
                    },
                    "derived_field": {
                        "type": str,
                        "is_required": False,
                        "is_derived": True,
                        "default": None,
                        "is_editable": False,
                    },
                }

        fields = GraphQL.create_write_fields(DummyInterface)
        self.assertIn("field1", fields)
        self.assertIn("field2", fields)
        self.assertIsInstance(fields["field1"], graphene.String)
        self.assertIsInstance(fields["field2"], graphene.Int)
        self.assertNotIn("created_at", fields)
        self.assertNotIn("derived_field", fields)

    def test_create_write_fields_with_manager(self):
        """
        Test that `GraphQL.create_write_fields` generates correct input fields for attributes of type `GeneralManager`, mapping single instances to `graphene.ID` and lists to `graphene.List`.
        """

        class DummyInterface:
            @staticmethod
            def get_attribute_types():
                """
                Return a dictionary describing attribute metadata for 'manager' and 'manager_list', including type, requirement, derivation, default value, and editability.
                """
                return {
                    "manager": {
                        "type": GeneralManager,
                        "is_required": True,
                        "is_derived": False,
                        "default": "default_value",
                        "is_editable": True,
                    },
                    "manager_list": {
                        "type": GeneralManager,
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": False,
                    },
                }

        fields = GraphQL.create_write_fields(DummyInterface)
        self.assertIn("manager", fields)
        self.assertIn("manager_list", fields)
        self.assertIsInstance(fields["manager"], graphene.ID)
        self.assertIsInstance(fields["manager_list"], graphene.List)

    def test_generate_create_mutation_class(self):
        """
        Test that the generated create mutation class defines correct arguments, applies default values, and enforces mutation behavior.

        This test verifies that the mutation class generated for creating an instance:
        - Inherits from `graphene.Mutation`.
        - Defines required arguments with correct types and default values.
        - Returns a success flag and the created instance when invoked with valid input and context.
        - Raises a `GraphQLError` if the mutation context (`info`) is missing.
        """

        class DummyManager:
            def __init__(self, *_, **kwargs):
                """
                Initialize the instance and set the value of `field1` from keyword arguments if provided.
                """
                self.field1 = kwargs.get("field1")

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {
                        "field1": {
                            "type": str,
                            "is_required": True,
                            "is_editable": True,
                            "is_derived": False,
                            "default": "test123",
                        }
                    }

            @classmethod
            def create(cls, *_args, **kwargs):
                return DummyManager(**kwargs)

        default_return_values = {
            "success": graphene.Boolean(),
            "instance": graphene.Field(DummyManager),
        }
        mutation_class = GraphQL.generate_create_mutation_class(
            DummyManager, default_return_values
        )
        self.assertTrue(issubclass(mutation_class, graphene.Mutation))
        self.assertIn("field1", mutation_class._meta.arguments)
        self.assertIsInstance(mutation_class._meta.arguments["field1"], graphene.String)
        self.assertEqual(
            mutation_class._meta.arguments["field1"].kwargs["default_value"],
            "test123",
        )
        self.assertIn("success", mutation_class._meta.fields)
        self.assertIn("instance", mutation_class._meta.fields)

        info = MagicMock()
        info.context.user = AnonymousUser()

        mutation_result: dict = mutation_class.mutate(None, info, field1="test_value")
        self.assertTrue(mutation_result["success"])
        self.assertIsInstance(mutation_result["DummyManager"], DummyManager)
        self.assertEqual(mutation_result["DummyManager"].field1, "test_value")

        info = None
        with self.assertRaises(GraphQLError):
            mutation_result = mutation_class.mutate(None, info, field1="test_value")

    def test_generate_update_mutation_class(self):
        """
        Test that the generated update mutation class defines correct arguments, applies default values, and enforces mutation behavior.

        This test verifies that the update mutation class produced by `GraphQL.generate_update_mutation_class`:
        - Inherits from `graphene.Mutation`.
        - Defines arguments and fields with appropriate types and default values.
        - Returns a success flag and updated instance when invoked with valid input and context.
        - Raises a `GraphQLError` if the mutation context (`info`) is missing.
        """

        class DummyManager:
            def __init__(self, *_, **kwargs):
                """
                Initialize the instance and set the value of `field1` from keyword arguments if provided.
                """
                self.field1 = kwargs.get("field1")

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @classmethod
                def get_attribute_types(cls):
                    return {
                        "field1": {
                            "type": str,
                            "is_required": True,
                            "is_editable": True,
                            "is_derived": False,
                            "default": "test123",
                        }
                    }

            @classmethod
            def update(cls, *_args, **kwargs):
                return DummyManager(**kwargs)

        default_return_values = {
            "success": graphene.Boolean(),
            "instance": graphene.Field(DummyManager),
        }
        mutation_class = GraphQL.generate_update_mutation_class(
            DummyManager, default_return_values
        )
        self.assertTrue(issubclass(mutation_class, graphene.Mutation))
        self.assertIn("field1", mutation_class._meta.arguments)
        self.assertIsInstance(mutation_class._meta.arguments["field1"], graphene.String)
        self.assertEqual(
            mutation_class._meta.arguments["field1"].kwargs["default_value"],
            "test123",
        )
        self.assertIn("success", mutation_class._meta.fields)
        self.assertIn("instance", mutation_class._meta.fields)

        info = MagicMock()
        info.context.user = AnonymousUser()

        mutation_result: dict = mutation_class.mutate(
            None, info, field1="test_value", id=1
        )
        self.assertTrue(mutation_result["success"])
        self.assertIsInstance(mutation_result["DummyManager"], DummyManager)
        self.assertEqual(mutation_result["DummyManager"].field1, "test_value")

        info = None
        with self.assertRaises(GraphQLError):
            mutation_result = mutation_class.mutate(None, info, field1="test_value")

    def test_generate_delete_mutation_class(self):
        """
        Test that the delete mutation class generated by GraphQL has the correct fields and behavior.

        Verifies that the generated mutation class:
        - Inherits from `graphene.Mutation`.
        - Defines a `success` field.
        - Calls the manager's `delete` method and returns a success flag.
        - Raises a `GraphQLError` if the mutation context (`info`) is missing.
        """

        class DummyManager:
            def __init__(self, *_, **kwargs):
                """
                Initialize the instance and set the `field1` attribute from kwargs if provided.

                Parameters:
                    field1: Value to assign to `self.field1` if present in keyword arguments.
                """
                self.field1 = kwargs.get("field1")

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {"id": None}

                @classmethod
                def get_attribute_types(cls):
                    """
                    Provide a mapping of attribute names to their type and metadata for the interface.

                    Each mapping value is a dictionary with the following keys:
                    - `type`: the Python type of the attribute (e.g., `int`, `str`).
                    - `is_required`: `True` if the attribute must be provided.
                    - `is_editable`: `True` if the attribute can be written via mutations.
                    - `is_derived`: `True` if the attribute is computed/derived (and should be excluded from write inputs).
                    - `default`: the default value for the attribute when not provided.

                    Returns:
                        dict: A mapping from attribute name to its metadata dictionary.
                    """
                    return {
                        "id": {
                            "type": int,
                            "is_required": True,
                            "is_editable": True,
                            "is_derived": False,
                            "default": "test123",
                        }
                    }

            def delete(self, *_args, **_kwargs):
                """
                Perform the manager's delete operation and return the instance.

                Parameters:
                    *_args: Additional positional arguments accepted by the delete API; ignored by this implementation.
                    **_kwargs: Additional keyword arguments accepted by the delete API; ignored by this implementation.

                Returns:
                    The same instance after the delete operation.
                """
                return self

        default_return_values = {
            "success": graphene.Boolean(),
        }
        mutation_class = GraphQL.generate_delete_mutation_class(
            DummyManager, default_return_values
        )
        self.assertTrue(issubclass(mutation_class, graphene.Mutation))
        self.assertIn("success", mutation_class._meta.fields)

        info = MagicMock()
        info.context.user = AnonymousUser()

        mutation_result: dict = mutation_class.mutate(None, info, id=1)
        self.assertTrue(mutation_result["success"])

        info = None
        with self.assertRaises(GraphQLError):
            mutation_result = mutation_class.mutate(None, info)


class GraphQLPropertyTypeHintTests(TestCase):
    def test_graphql_property_stores_return_type(self):
        def getter() -> int:
            return 1

        prop = GraphQLProperty(getter)
        self.assertEqual(prop.graphql_type_hint, int)

    def test_graphql_property_non_callable_raises_typeerror(self):
        with self.assertRaises(TypeError):
            GraphQLProperty(123)  # type: ignore[arg-type]
