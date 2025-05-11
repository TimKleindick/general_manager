import json
from decimal import Decimal
from datetime import date, datetime
import graphene
from django.test import TestCase
from unittest.mock import MagicMock, patch
from django.contrib.auth.models import AnonymousUser

from general_manager.api.graphql import (
    MeasurementType,
    GraphQL,
)
from general_manager.measurement.measurement import Measurement, ureg
from general_manager.manager.generalManager import GeneralManager, GeneralManagerMeta
from general_manager.api.property import GraphQLProperty
from general_manager.interface.baseInterface import InterfaceBase


class GraphQLPropertyTests(TestCase):
    def test_graphql_property_initialization(self):
        def mock_getter():
            return "test"

        prop = GraphQLProperty(mock_getter)
        self.assertTrue(prop.is_graphql_resolver)
        self.assertIsNone(prop.graphql_type_hint)

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
    def test_create_graphql_interface_no_interface(self, mock_interface):
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
        self.assertEqual(result, {"value": Decimal(100), "unit": ureg("cm")})

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
            result = resolver(
                mock_instance,
                self.info,
                filter=json.dumps({"field": "value"}),
                exclude=json.dumps({"other_field": "value"}),
            )
            mock_queryset.filter.assert_called_with(field="value")
            mock_filtered.exclude.assert_called_with(other_field="value")

    @patch("general_manager.interface.baseInterface.InterfaceBase")
    def test_create_graphql_interface_graphql_property(self, mock_interface):
        class TestManager:
            class Interface(InterfaceBase):
                input_fields = {}

                @staticmethod
                def getAttributeTypes():  # type: ignore
                    return {"test_field": {"type": str}}

            @classmethod
            def all(cls):
                return []

        mock_interface.getAttributeTypes.return_value = {"test_field": {"type": str}}
        with patch("general_manager.api.graphql.issubclass", return_value=True):
            setattr(TestManager, "test_prop", GraphQLProperty(lambda: 42))
            GraphQL.createGraphqlInterface(TestManager)  # type: ignore
            self.assertIn("TestManager", GraphQL.graphql_type_registry)

    def test_list_resolver_with_invalid_filter_exclude(self):
        mock_instance = MagicMock()
        mock_qs = MagicMock()
        mock_instance.abc_list = mock_qs
        resolver = GraphQL._createResolver("abc_list", GeneralManager)
        with patch("json.loads", side_effect=ValueError):
            result = resolver(mock_instance, self.info, filter="bad", exclude="bad")
            self.assertEqual(result, mock_qs)

    def test_create_filter_options_measurement_fields(self):
        class DummyManager:
            __name__ = "DummyManager"

            class Interface(InterfaceBase):
                input_fields = {}

                @staticmethod
                def getAttributeTypes():  # type: ignore
                    return {
                        "num_field": {"type": int},
                        "str_field": {"type": str},
                        "measurement_field": {"type": Measurement},
                        "gm_field": {"type": GeneralManager},
                    }

        GraphQL.graphql_filter_type_registry.clear()
        filter_cls = GraphQL._createFilterOptions("dummy", DummyManager)  # type: ignore
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
        for key in (
            ["measurement_field_value", "measurement_field_unit"]
            + [
                f"measurement_field_value__{opt}"
                for opt in ["exact", "gt", "gte", "lt", "lte"]
            ]
            + [
                f"measurement_field_unit__{opt}"
                for opt in ["exact", "gt", "gte", "lt", "lte"]
            ]
        ):
            self.assertIn(key, fields)

    def test_create_filter_options_registry_cache(self):
        class DummyManager2:
            __name__ = "DummyManager2"

            class Interface(InterfaceBase):
                input_fields = {}

                @staticmethod
                def getAttributeTypes():  # type: ignore
                    return {"num_field": {"type": int}}

        GraphQL.graphql_filter_type_registry.clear()
        first = GraphQL._createFilterOptions("dummy2", DummyManager2)  # type: ignore
        second = GraphQL._createFilterOptions("dummy2", DummyManager2)  # type: ignore
        self.assertIs(first, second)
