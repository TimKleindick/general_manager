# type: ignore

import json
from decimal import Decimal
from datetime import date, datetime
import graphene
from django.test import TestCase
from unittest.mock import MagicMock, patch
from django.contrib.auth.models import AnonymousUser
from typing import ClassVar

from general_manager.api.graphql import (
    MeasurementType,
    GraphQL,
    getReadPermissionFilter,
)
from general_manager.measurement.measurement import Measurement, ureg
from general_manager.manager.generalManager import GeneralManager, GeneralManagerMeta
from general_manager.api.property import GraphQLProperty
from general_manager.interface.baseInterface import InterfaceBase
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

    @patch("general_manager.interface.baseInterface.InterfaceBase")
    def test_create_graphql_interface_no_interface(self, _mock_interface):
        self.general_manager_class.Interface = None
        result = GraphQL.createGraphqlInterface(self.general_manager_class)
        self.assertIsNone(result)

    @patch("general_manager.interface.baseInterface.InterfaceBase")
    def test_create_graphql_interface_with_interface(self, mock_interface):
        mock_interface.getAttributeTypes.return_value = {"test_field": {"type": str}}
        self.general_manager_class.Interface = mock_interface
        with patch("general_manager.api.graphql.issubclass", return_value=True):
            GraphQL.createGraphqlInterface(self.general_manager_class)
            self.assertIn("TestManager", GraphQL.graphql_type_registry)

    def test_map_field_to_graphene(self):
        # Base types
        self.assertIsInstance(
            GraphQL._mapFieldToGrapheneRead(str, "name"), graphene.String
        )
        self.assertIsInstance(GraphQL._mapFieldToGrapheneRead(int, "age"), graphene.Int)
        self.assertIsInstance(
            GraphQL._mapFieldToGrapheneRead(float, "value"), graphene.Float
        )
        self.assertIsInstance(
            GraphQL._mapFieldToGrapheneRead(Decimal, "decimal"), graphene.Float
        )
        self.assertIsInstance(
            GraphQL._mapFieldToGrapheneRead(bool, "active"), graphene.Boolean
        )
        self.assertIsInstance(
            GraphQL._mapFieldToGrapheneRead(date, "birth_date"), graphene.Date
        )
        field = GraphQL._mapFieldToGrapheneRead(Measurement, "measurement")
        self.assertIsInstance(field, graphene.Field)

    def test_create_resolver_normal_case(self):
        mock_instance = MagicMock()
        mock_instance.some_field = "expected_value"
        resolver = GraphQL._createResolver("some_field", str)
        self.assertEqual(resolver(mock_instance, self.info), "expected_value")

    def test_create_resolver_measurement_case(self):
        mock_instance = MagicMock()
        mock_measurement = Measurement(100, "cm")
        mock_instance.measurement_field = mock_measurement

        resolver = GraphQL._createResolver("measurement_field", Measurement)
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

        resolver = GraphQL._createResolver("abc_list", GeneralManager)
        with patch("json.loads", side_effect=json.loads):
            resolver(
                mock_instance,
                self.info,
                filter=json.dumps({"field": "value"}),
                exclude=json.dumps({"other_field": "value"}),
            )
            mock_queryset.filter.assert_called_with(field="value")
            mock_filtered.exclude.assert_called_with(other_field="value")

    @patch("general_manager.interface.baseInterface.InterfaceBase")
    def test_create_graphql_interface_graphql_property(self, mock_interface):
        """
        Test that a GraphQL interface is created and registered when a manager class defines a GraphQLProperty attribute.
        """

        class TestManager:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def getAttributeTypes():
                    return {"test_field": {"type": str}}

            @classmethod
            def all(cls):
                return []

        def prop_func() -> int:
            return 42

        mock_interface.getAttributeTypes.return_value = {"test_field": {"type": str}}
        with patch("general_manager.api.graphql.issubclass", return_value=True):
            TestManager.test_prop = GraphQLProperty(prop_func)
            GraphQL.createGraphqlInterface(TestManager)
            self.assertIn("TestManager", GraphQL.graphql_type_registry)

    def test_list_resolver_with_invalid_filter_exclude(self):
        """
        Test that the list resolver returns the original queryset when filter or exclude arguments are invalid JSON.

        If JSON decoding fails for the filter or exclude parameters, ensures the resolver returns the unfiltered queryset under the "items" key.
        """
        mock_instance = MagicMock()
        mock_qs = MagicMock()
        mock_instance.abc_list = mock_qs
        resolver = GraphQL._createResolver("abc_list", GeneralManager)
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
                def getAttributeTypes():
                    return {
                        "num_field": {"type": int},
                        "str_field": {"type": str},
                        "measurement_field": {"type": Measurement},
                        "gm_field": {"type": GeneralManager},
                    }

        GraphQL.graphql_filter_type_registry.clear()
        filter_cls = GraphQL._createFilterOptions(DummyManager)
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
        Test that repeated calls to `_createFilterOptions` with the same manager class and name return the same cached filter input type instance.

        Ensures the filter options registry caches and reuses filter input types for identical manager class and name combinations.
        """

        class DummyManager2:
            __name__ = "DummyManager2"

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}

                @staticmethod
                def getAttributeTypes():
                    return {"num_field": {"type": int}}

        GraphQL.graphql_filter_type_registry.clear()
        first = GraphQL._createFilterOptions(DummyManager2)
        second = GraphQL._createFilterOptions(DummyManager2)
        self.assertIs(first, second)


class TestGetReadPermissionFilter(TestCase):
    def test_get_read_permission_filter(self):
        """
        Verify that getReadPermissionFilter extracts and returns filter and exclude tuples from a manager's permission class.
        """

        class DummyManager:
            __name__ = "DummyManager"

            class Permission:
                def __init__(self, *args, **_kwargs):
                    self.args = args

                def getPermissionFilter(self):
                    return [{"filter": {"num_field__exact": 42}, "exclude": {}}]

        info = MagicMock()
        info.context.user = AnonymousUser()
        result = getReadPermissionFilter(DummyManager, info)
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
                def create(cls, *_, **__):
                    pass

                def update(self, *_, **__):
                    pass

                def deactivate(self, *_, **__):
                    pass

        class DummyManager2:
            class Interface(InterfaceBase):
                def getData(self, search_date: datetime | None = None):
                    """
                    Raises NotImplementedError to indicate that data retrieval is not implemented.

                    Parameters:
                        search_date (datetime, optional): An optional date to specify the context for data retrieval.
                    """
                    raise NotImplementedError

                @classmethod
                def getAttributeTypes(cls):
                    """
                    Raises a NotImplementedError to indicate that subclasses must implement this method to return attribute type information.
                    """
                    raise NotImplementedError

                @classmethod
                def getAttributes(cls):
                    """
                    Raises a NotImplementedError to indicate that subclasses must implement the getAttributes method.
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
                def handleInterface(
                    cls,
                ) -> None:
                    """
                    Initializes or registers interface-related components for the class.

                    Intended to be called on a class to perform setup required for its interface functionality.
                    """
                    pass

                @classmethod
                def getFieldType(cls, field_name: str) -> None:
                    """
                    Return the type of the specified field on the class.

                    Parameters:
                        field_name (str): The name of the field whose type is to be retrieved.

                    Returns:
                        The type of the specified field, or None if not implemented.
                    """
                    pass

        self.manager = DummyManager
        self.manager2 = DummyManager2
        GraphQL._mutations = {}

    @patch("general_manager.api.graphql.GraphQL.generateCreateMutationClass")
    @patch("general_manager.api.graphql.GraphQL.generateUpdateMutationClass")
    @patch("general_manager.api.graphql.GraphQL.generateDeleteMutationClass")
    def test_createGraphqlMutation(
        self, mock_delete: MagicMock, mock_update: MagicMock, mock_create: MagicMock
    ):
        """
        Tests that GraphQL.createGraphqlMutation generates and registers create, update, and delete mutation classes for a manager with the corresponding methods, and that the mutation generation methods are called exactly once.
        """
        GraphQL.createGraphqlMutation(self.manager)
        mock_create.assert_called_once()
        mock_update.assert_called_once()
        mock_delete.assert_called_once()
        self.assertEqual(
            list(GraphQL._mutations.keys()),
            ["createDummyManager", "updateDummyManager", "deleteDummyManager"],
        )

    @patch("general_manager.api.graphql.GraphQL.generateCreateMutationClass")
    @patch("general_manager.api.graphql.GraphQL.generateUpdateMutationClass")
    @patch("general_manager.api.graphql.GraphQL.generateDeleteMutationClass")
    def test_createGraphqlMutation_with_undefined_create_update_delete(
        self, mock_delete: MagicMock, mock_update: MagicMock, mock_create: MagicMock
    ):
        """
        Test that no mutation classes are generated if the manager lacks create, update, and delete methods.

        Ensures that the mutation generation functions for create, update, and delete are not called when the manager does not define these methods.
        """
        GraphQL.createGraphqlMutation(self.manager2)
        mock_create.assert_not_called()
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    def test_createWriteFields(self):
        """
        Tests that `GraphQL.createWriteFields` returns input fields only for editable, non-derived attributes, mapping their types correctly and excluding derived fields.
        """

        class DummyInterface:
            @staticmethod
            def getAttributeTypes():
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

        fields = GraphQL.createWriteFields(DummyInterface)
        self.assertIn("field1", fields)
        self.assertIn("field2", fields)
        self.assertIsInstance(fields["field1"], graphene.String)
        self.assertIsInstance(fields["field2"], graphene.Int)
        self.assertNotIn("created_at", fields)
        self.assertNotIn("derived_field", fields)

    def test_createWriteFields_with_manager(self):
        """
        Test that `GraphQL.createWriteFields` generates correct input fields for attributes of type `GeneralManager`, mapping single instances to `graphene.ID` and lists to `graphene.List`.
        """

        class DummyInterface:
            @staticmethod
            def getAttributeTypes():
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

        fields = GraphQL.createWriteFields(DummyInterface)
        self.assertIn("manager", fields)
        self.assertIn("manager_list", fields)
        self.assertIsInstance(fields["manager"], graphene.ID)
        self.assertIsInstance(fields["manager_list"], graphene.List)

    def test_generateCreateMutationClass(self):
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
                def getAttributeTypes(cls):
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
            def create(cls, *_, **kwargs):
                return DummyManager(**kwargs)

        default_return_values = {
            "success": graphene.Boolean(),
            "instance": graphene.Field(DummyManager),
        }
        mutation_class = GraphQL.generateCreateMutationClass(
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

    def test_generateUpdateMutationClass(self):
        """
        Test that the generated update mutation class defines correct arguments, applies default values, and enforces mutation behavior.

        This test verifies that the update mutation class produced by `GraphQL.generateUpdateMutationClass`:
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
                def getAttributeTypes(cls):
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
            def update(cls, *_, **kwargs):
                return DummyManager(**kwargs)

        default_return_values = {
            "success": graphene.Boolean(),
            "instance": graphene.Field(DummyManager),
        }
        mutation_class = GraphQL.generateUpdateMutationClass(
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

    def test_generateDeleteMutationClass(self):
        """
        Test that the delete mutation class generated by GraphQL has the correct fields and behavior.

        Verifies that the generated mutation class:
        - Inherits from `graphene.Mutation`.
        - Defines a `success` field.
        - Calls the manager's `deactivate` method and returns a success flag.
        - Raises a `GraphQLError` if the mutation context (`info`) is missing.
        """

        class DummyManager:
            def __init__(self, *_, **kwargs):
                """
                Initialize the instance and set the value of `field1` from keyword arguments if provided.
                """
                self.field1 = kwargs.get("field1")

            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {"id": None}

                @classmethod
                def getAttributeTypes(cls):
                    return {
                        "id": {
                            "type": int,
                            "is_required": True,
                            "is_editable": True,
                            "is_derived": False,
                            "default": "test123",
                        }
                    }

            @classmethod
            def deactivate(cls, *_, **__):
                return True

        default_return_values = {
            "success": graphene.Boolean(),
        }
        mutation_class = GraphQL.generateDeleteMutationClass(
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

class AdditionalGraphQLMappingTests(TestCase):
    def test_map_field_to_graphene_datetime(self):
        field = GraphQL._mapFieldToGrapheneRead(datetime, "ts")
        self.assertIsInstance(field, graphene.DateTime)

    def test_map_field_to_graphene_measurement_field_type(self):
        field = GraphQL._mapFieldToGrapheneRead(Measurement, "distance")
        # Expect a graphene.Field around MeasurementType
        self.assertIsInstance(field, graphene.Field)
        self.assertIs(field._type, MeasurementType)


class AdditionalResolverTests(TestCase):
    def setUp(self):
        self.info = MagicMock()
        self.info.context.user = AnonymousUser()

    def test_measurement_resolver_without_target_unit_returns_base(self):
        m = Measurement(2, "m")
        obj = MagicMock()
        obj.dist = m
        resolver = GraphQL._createResolver("dist", Measurement)
        result = resolver(obj, self.info)
        # Base unit for meter is "meter"
        self.assertEqual(result, {"value": Decimal(2), "unit": "meter"})

    def test_measurement_resolver_with_invalid_target_unit_graceful(self):
        m = Measurement(200, "cm")
        obj = MagicMock()
        obj.size = m
        resolver = GraphQL._createResolver("size", Measurement)
        # If invalid unit is provided, expect original value/unit (implementation should handle exceptions)
        res = resolver(obj, self.info, target_unit="invalid_unit")
        self.assertEqual(res, {"value": Decimal(200), "unit": "centimeter"})

    def test_list_resolver_valid_filter_invalid_exclude(self):
        qs = MagicMock()
        filtered = MagicMock()
        qs.filter.return_value = filtered
        filtered.exclude.return_value = filtered  # should be ignored due to invalid exclude
        obj = MagicMock()
        obj.items_list = qs
        resolver = GraphQL._createResolver("items_list", GeneralManager)
        with patch("json.loads", side_effect=[{"a": 1}, ValueError]):
            result = resolver(obj, self.info, filter=json.dumps({"a": 1}), exclude="bad")
            qs.filter.assert_called_once_with(a=1)
            # exclude should not be applied
            self.assertEqual(result["items"], filtered)

    def test_list_resolver_invalid_filter_valid_exclude(self):
        qs = MagicMock()
        filtered = MagicMock()
        qs.filter.return_value = filtered  # should be ignored due to invalid filter
        filtered.exclude.return_value = filtered
        obj = MagicMock()
        obj.objs = qs
        resolver = GraphQL._createResolver("objs", GeneralManager)
        with patch("json.loads", side_effect=[ValueError, {"x": 2}]):
            result = resolver(obj, self.info, filter="bad", exclude=json.dumps({"x": 2}))
            # filter should not be called; exclude should apply to original qs
            qs.filter.assert_not_called()
            qs.exclude.assert_called_once_with(x=2)
            self.assertEqual(result["items"], qs.exclude())

class AdditionalPermissionFilterTests(TestCase):
    def setUp(self):
        self.info = MagicMock()
        self.info.context.user = AnonymousUser()

    def test_get_read_permission_filter_no_permission_class(self):
        class NoPermManager:
            __name__ = "NoPermManager"
        out = getReadPermissionFilter(NoPermManager, self.info)
        self.assertEqual(out, [])

    def test_get_read_permission_filter_permission_without_method(self):
        class PermNoMethodManager:
            __name__ = "PermNoMethodManager"
            class Permission:
                pass
        out = getReadPermissionFilter(PermNoMethodManager, self.info)
        self.assertEqual(out, [])

    def test_get_read_permission_filter_multiple_sets(self):
        class MultiPermManager:
            __name__ = "MultiPermManager"
            class Permission:
                def getPermissionFilter(self):
                    return [
                        {"filter": {"a__gt": 1}},
                        {"exclude": {"b__in": [1,2,3]}},
                        {"filter": {"c": "x"}, "exclude": {"d__lte": 5}},
                    ]
        out = getReadPermissionFilter(MultiPermManager, self.info)
        self.assertEqual(out, [
            ({"a__gt": 1}, {}),
            ({}, {"b__in": [1,2,3]}),
            ({"c": "x"}, {"d__lte": 5}),
        ])


class AdditionalInterfaceCreationTests(TestCase):
    def tearDown(self):
        GraphQL.graphql_type_registry.pop("BadInterfaceManager", None)

    def test_create_graphql_interface_not_subclass_returns_none(self):
        class BadInterface:  # Not a subclass of InterfaceBase
            @staticmethod
            def getAttributeTypes():
                return {"x": {"type": str}}

        class BadInterfaceManager:
            Interface = BadInterface
        with patch("general_manager.api.graphql.issubclass", return_value=False):
            res = GraphQL.createGraphqlInterface(BadInterfaceManager)
            self.assertIsNone(res)
            self.assertNotIn("BadInterfaceManager", GraphQL.graphql_type_registry)


class AdditionalMutationMatrixTests(TestCase):
    def setUp(self):
        GraphQL._mutations = {}

    @patch("general_manager.api.graphql.GraphQL.generateCreateMutationClass")
    def test_only_create_mutation_generated(self, mock_create):
        class OnlyCreate:
            class Interface:
                input_fields: ClassVar[dict] = {}
            @classmethod
            def create(cls, *_, **__):
                return True
        GraphQL.createGraphqlMutation(OnlyCreate)
        mock_create.assert_called_once()
        self.assertIn("createOnlyCreate", GraphQL._mutations)
        self.assertNotIn("updateOnlyCreate", GraphQL._mutations)
        self.assertNotIn("deleteOnlyCreate", GraphQL._mutations)

    @patch("general_manager.api.graphql.GraphQL.generateUpdateMutationClass")
    def test_only_update_mutation_generated(self, mock_update):
        class OnlyUpdate:
            class Interface:
                input_fields: ClassVar[dict] = {}
            @classmethod
            def update(cls, *_, **__):
                return True
        GraphQL.createGraphqlMutation(OnlyUpdate)
        mock_update.assert_called_once()
        self.assertIn("updateOnlyUpdate", GraphQL._mutations)
        self.assertNotIn("createOnlyUpdate", GraphQL._mutations)
        self.assertNotIn("deleteOnlyUpdate", GraphQL._mutations)

    @patch("general_manager.api.graphql.GraphQL.generateDeleteMutationClass")
    def test_only_delete_mutation_generated(self, mock_del):
        class OnlyDelete:
            class Interface:
                input_fields: ClassVar[dict] = {}
            @classmethod
            def deactivate(cls, *_, **__):
                return True
        GraphQL.createGraphqlMutation(OnlyDelete)
        mock_del.assert_called_once()
        self.assertIn("deleteOnlyDelete", GraphQL._mutations)
        self.assertNotIn("createOnlyDelete", GraphQL._mutations)
        self.assertNotIn("updateOnlyDelete", GraphQL._mutations)


class AdditionalCreateWriteFieldsTests(TestCase):
    def test_createWriteFields_datetime_and_required_flags(self):
        class Iface:
            @staticmethod
            def getAttributeTypes():
                return {
                    "title": {"type": str, "is_required": True, "is_derived": False, "default": None, "is_editable": True},
                    "when": {"type": datetime, "is_required": False, "is_derived": False, "default": None, "is_editable": True},
                    "skip": {"type": int, "is_required": True, "is_derived": True, "default": None, "is_editable": True},
                }
        fields = GraphQL.createWriteFields(Iface)
        self.assertIn("title", fields)
        self.assertIn("when", fields)
        self.assertNotIn("skip", fields)
        self.assertIsInstance(fields["title"], graphene.String)
        self.assertIsInstance(fields["when"], graphene.DateTime)

    def test_createWriteFields_general_manager_list_uses_id_list(self):
        class Iface:
            @staticmethod
            def getAttributeTypes():
                return {
                    "owners": {
                        "type": GeneralManager,
                        "is_required": False,
                        "is_derived": False,
                        "default": None,
                        "is_editable": True,
                    }
                }
        fields = GraphQL.createWriteFields(Iface)
        self.assertIn("owners", fields)
        self.assertIsInstance(fields["owners"], graphene.List)


class AdditionalMutationBehaviorTests(TestCase):
    def setUp(self):
        self.info = MagicMock()
        self.info.context.user = AnonymousUser()

    def test_generateCreateMutationClass_uses_default_when_arg_missing(self):
        class Mgr:
            def __init__(self, **kwargs):
                self.name = kwargs.get("name")
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}
                @classmethod
                def getAttributeTypes(cls):
                    return {"name": {"type": str, "is_required": True, "is_editable": True, "is_derived": False, "default": "DEF"}}
            @classmethod
            def create(cls, **kwargs):
                return Mgr(**kwargs)

        defaults = {"success": graphene.Boolean(), "instance": graphene.Field(Mgr)}
        Mutation = GraphQL.generateCreateMutationClass(Mgr, defaults)
        out = Mutation.mutate(None, self.info)  # omit arg, should use default
        self.assertTrue(out["success"])
        self.assertEqual(out["Mgr"].name, "DEF")

    def test_generateUpdateMutationClass_uses_default_when_arg_missing(self):
        class Mgr:
            def __init__(self, **kwargs):
                self.q = kwargs.get("q")
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {}
                @classmethod
                def getAttributeTypes(cls):
                    return {"q": {"type": int, "is_required": False, "is_editable": True, "is_derived": False, "default": 7}}
            @classmethod
            def update(cls, **kwargs):
                return Mgr(**kwargs)

        defaults = {"success": graphene.Boolean(), "instance": graphene.Field(Mgr)}
        Mutation = GraphQL.generateUpdateMutationClass(Mgr, defaults)
        out = Mutation.mutate(None, self.info)  # omit arg
        self.assertTrue(out["success"])
        self.assertEqual(out["Mgr"].q, 7)

    def test_generateDeleteMutationClass_missing_required_arg_raises(self):
        class Mgr:
            class Interface(InterfaceBase):
                input_fields: ClassVar[dict] = {"id": None}
                @classmethod
                def getAttributeTypes(cls):
                    return {"id": {"type": int, "is_required": True, "is_editable": True, "is_derived": False, "default": None}}
            @classmethod
            def deactivate(cls, *_, **__):
                return True

        Mutation = GraphQL.generateDeleteMutationClass(Mgr, {"success": graphene.Boolean()})
        with self.assertRaises(GraphQLError):
            Mutation.mutate(None, self.info)  # no id provided