# from django.test import TestCase
# from unittest.mock import MagicMock, patch
# import json
# from decimal import Decimal
# from datetime import date, datetime
# import graphene
# from general_manager.manager.generalManager import (
#     GeneralManager,
#     GeneralManagerMeta,
# )
# from general_manager.measurement.measurement import Measurement, ureg
# from django.contrib.auth.models import AnonymousUser

# from general_manager.api.graphql import (
#     MeasurementType,
#     GraphQL,
#     Measurement,
# )

# from general_manager.api.property import GraphQLProperty


# class GraphQLPropertyTests(TestCase):
#     def test_graphql_property_initialization(self):
#         def mock_getter():
#             """Mock getter method."""
#             return "test"

#         prop = GraphQLProperty(mock_getter)
#         self.assertTrue(prop.is_graphql_resolver)
#         self.assertEqual(prop.graphql_type_hint, None)

#     def test_graphql_property_with_type_hint(self):
#         def mock_getter() -> str:
#             return "test"

#         prop = GraphQLProperty(mock_getter)
#         self.assertEqual(prop.graphql_type_hint, str)


# class MeasurementTypeTests(TestCase):
#     def test_measurement_type_fields(self):
#         self.assertTrue(hasattr(MeasurementType, "value"))
#         self.assertTrue(hasattr(MeasurementType, "unit"))


# class GraphQLTests(TestCase):
#     def setUp(self):
#         # Setup mock general manager class
#         self.general_manager_class = MagicMock(spec=GeneralManagerMeta)
#         self.general_manager_class.__name__ = "TestManager"
#         self.info = MagicMock()
#         self.info.context.user = AnonymousUser()

#     @patch("interface.baseInterface.InterfaceBase")
#     def test_create_graphql_interface_no_interface(self, mock_interface):
#         # Test case where no Interface is present
#         self.general_manager_class.Interface = None
#         with patch("general_manager.api.graphql.issubclass", return_value=True):
#             result = GraphQL._createGraphQlInterface(self.general_manager_class)
#             self.assertIsNone(result)

#     @patch("interface.baseInterface.InterfaceBase")
#     def test_create_graphql_interface_with_interface(self, mock_interface):
#         # Test with an interface and checking registry population
#         mock_interface.getAttributeTypes.return_value = {
#             "test_field": str,
#             "int_field": int,
#         }
#         self.general_manager_class.Interface = mock_interface
#         with patch("general_manager.api.graphql.issubclass", return_value=True):
#             GraphQL._createGraphQlInterface(self.general_manager_class)
#             self.assertIn("TestManager", GraphQL.graphql_type_registry)

#     def test_map_field_to_graphene(self):
#         # Test type mappings
#         self.assertIsInstance(
#             GraphQL._GraphQL__map_field_to_graphene(str, "name"),  # type: ignore
#             graphene.String,
#         )
#         self.assertIsInstance(
#             GraphQL._GraphQL__map_field_to_graphene(int, "age"),  # type: ignore
#             graphene.Int,
#         )
#         self.assertIsInstance(
#             GraphQL._GraphQL__map_field_to_graphene(float, "value"),  # type: ignore
#             graphene.Float,
#         )
#         self.assertIsInstance(
#             GraphQL._GraphQL__map_field_to_graphene(Decimal, "decimal"),  # type: ignore
#             graphene.Float,
#         )
#         self.assertIsInstance(
#             GraphQL._GraphQL__map_field_to_graphene(bool, "active"),  # type: ignore
#             graphene.Boolean,
#         )
#         self.assertIsInstance(
#             GraphQL._GraphQL__map_field_to_graphene(date, "birth_date"),  # type: ignore
#             graphene.Date,
#         )
#         self.assertIsInstance(
#             GraphQL._GraphQL__map_field_to_graphene(Measurement, "measurement"),  # type: ignore
#             graphene.Field,
#         )

#     def test_create_resolver_normal_case(self):
#         # Test resolver for a normal field type
#         mock_instance = MagicMock()
#         mock_instance.some_field = "expected_value"

#         resolver = GraphQL._GraphQL__create_resolver("some_field", str)  # type: ignore
#         self.assertEqual(resolver(mock_instance, self.info), "expected_value")

#     def test_create_resolver_measurement_case(self):
#         # Test resolver for Measurement field type with unit conversion
#         mock_instance = MagicMock()
#         mock_measurement = Measurement(100, "cm")
#         mock_instance.measurement_field = mock_measurement

#         resolver = GraphQL._GraphQL__create_resolver("measurement_field", Measurement)  # type: ignore
#         result = resolver(mock_instance, self.info, target_unit="cm")
#         self.assertEqual(result, {"value": Decimal(100), "unit": ureg("cm")})

#     def test_create_resolver_list_case(self):
#         # Test resolver for a list field type with filtering
#         mock_instance = MagicMock()
#         mock_queryset = MagicMock()
#         mock_filtered_queryset = MagicMock()  # Return value of filter()
#         mock_queryset.filter.return_value = mock_filtered_queryset
#         mock_filtered_queryset.exclude.return_value = (
#             mock_filtered_queryset  # Chaining exclude on filtered queryset
#         )

#         mock_instance.abc_list.all.return_value = (
#             mock_queryset  # Return initial queryset from .all()
#         )

#         resolver = GraphQL._GraphQL__create_resolver("abc_list", GeneralManager)  # type: ignore

#         with patch("json.loads", side_effect=json.loads):  # Ensure correct JSON parsing
#             result = resolver(
#                 mock_instance,
#                 None,
#                 filter=json.dumps({"field": "value"}),
#                 exclude=json.dumps({"other_field": "value"}),
#             )

#             # Assert that filter and exclude are called on correct queryset
#             mock_queryset.filter.assert_called_with(field="value")
#             mock_filtered_queryset.exclude.assert_called_with(other_field="value")

#     def test_add_queries_to_schema(self):
#         # Test if queries are added to the schema properly
#         class TestGeneralManager:
#             class Interface:
#                 input_fields = {}

#                 @staticmethod
#                 def getAttributeTypes():
#                     return {"test_field": str}

#             @classmethod
#             def all(cls):
#                 return []

#         graphene_type = MagicMock()
#         with patch("general_manager.api.graphql.issubclass", return_value=True):
#             GraphQL._GraphQL__add_queries_to_schema(graphene_type, TestGeneralManager)  # type: ignore

#             self.assertIn("testgeneralmanager_list", GraphQL._query_fields)
#             self.assertIn("resolve_testgeneralmanager_list", GraphQL._query_fields)
#             self.assertIn("testgeneralmanager", GraphQL._query_fields)
#             self.assertIn("resolve_testgeneralmanager", GraphQL._query_fields)

#     @patch("interface.baseInterface.InterfaceBase")
#     def test_create_graphql_interface_graphql_property(self, mock_interface):
#         # Dummy-Interface definieren
#         class TestGeneralManager:
#             class Interface:
#                 input_fields = {}

#                 @staticmethod
#                 def getAttributeTypes():
#                     return {"test_field": str}

#             @classmethod
#             def all(cls):
#                 return []

#         with patch("general_manager.api.graphql.issubclass", return_value=True):
#             # Konfiguriere das Mock für InterfaceBase
#             mock_interface.getAttributeTypes.return_value = {"test_field": str}
#             self.general_manager_class.Interface = mock_interface

#             # Füge ein GraphQLProperty-Attribut hinzu
#             def graphql_property_func() -> int:
#                 return 42

#             setattr(
#                 TestGeneralManager,
#                 "test_prop",
#                 GraphQLProperty(graphql_property_func),
#             )

#             # Aufruf der zu testenden Methode
#             GraphQL._createGraphQlInterface(self.general_manager_class)

#             # Prüfe, ob der erwartete GraphQL-Typ registriert wurde
#             self.assertIn("TestManager", GraphQL.graphql_type_registry)

#     def test_map_field_to_graphene_general_manager(self):
#         class TestGeneralManager:
#             class Interface:
#                 input_fields = {}

#                 @staticmethod
#                 def getAttributeTypes():
#                     return {"test_field": str}

#             @classmethod
#             def all(cls):
#                 return []

#             @property
#             def test_list(self):
#                 return ["item1", "item2"]

#         def custom_side_effect(cls, base):
#             # Beispiel: Wenn nach einer bestimmten Basisklasse gefragt wird, gib True zurück
#             if base == GeneralManager:
#                 return True
#             return False

#         # Test field mapping for a GeneralManager type with list suffix
#         with patch(
#             "api.graphql.issubclass",
#             side_effect=custom_side_effect,
#         ):
#             self.assertIsInstance(
#                 GraphQL._GraphQL__map_field_to_graphene(TestGeneralManager, "test_list"),  # type: ignore
#                 graphene.List,
#             )

#     def test_list_resolver_with_invalid_filter_exclude(self):
#         # Test handling of invalid JSON in filter/exclude parameters
#         mock_instance = MagicMock()
#         mock_queryset = MagicMock()
#         mock_instance.abc_list.all.return_value = mock_queryset

#         resolver = GraphQL._GraphQL__create_resolver("abc_list", GeneralManager)  # type: ignore

#         # Modify resolver to handle ValueError
#         with patch("json.loads", side_effect=ValueError):
#             try:
#                 result = resolver(
#                     mock_instance, None, filter="invalid", exclude="invalid"
#                 )
#                 self.assertEqual(result, mock_queryset)
#             except ValueError:
#                 self.fail("Resolver should handle invalid JSON gracefully.")

#     def test_resolve_list_with_no_filter_exclude(self):
#         # Test list resolver without filter/exclude
#         class TestGeneralManager:
#             class Interface:
#                 input_fields = {}

#                 @staticmethod
#                 def getAttributeTypes():
#                     return {"test_field": str}

#             @classmethod
#             def all(cls):
#                 return ["item1", "item2"]

#         graphene_type = MagicMock()
#         with patch("general_manager.api.graphql.issubclass", return_value=True):
#             GraphQL._GraphQL__add_queries_to_schema(graphene_type, TestGeneralManager)  # type: ignore

#             resolve_list_func = GraphQL._query_fields["resolve_testgeneralmanager_list"]
#             result = resolve_list_func(self, None)
#             self.assertEqual(result, ["item1", "item2"])

#     def test_create_filter_options_measurement_fields(self):
#         # Dummy-Manager definieren, dessen Interface verschiedene Feldtypen zurückgibt
#         class DummyManager:
#             __name__ = "DummyManager"

#             class Interface:
#                 input_fields = {}

#                 @staticmethod
#                 def getAttributeTypes():
#                     from general_manager.measurement.measurement import Measurement
#                     from general_manager.manager.generalManager import (
#                         GeneralManager,
#                     )

#                     return {
#                         "num_field": int,
#                         "str_field": str,
#                         "measurement_field": Measurement,
#                         "gm_field": GeneralManager,  # sollte übersprungen werden
#                     }

#         # Sicherstellen, dass das Filter-Registry-Cache leer ist
#         GraphQL.graphql_filter_type_registry = {}
#         # Aufruf der neuen Funktion
#         filter_class = GraphQL._createFilterOptions("dummy", DummyManager)  # type: ignore
#         # Zugriff auf die Felder der erzeugten InputObjectType
#         fields = filter_class._meta.fields

#         # Überprüfen, dass gm_field übersprungen wird
#         self.assertNotIn("gm_field", fields)

#         # Teste num_field: Es sollte das Basisfeld sowie die number_options-Felder geben.
#         self.assertIn("num_field", fields)
#         for option in ["exact", "gt", "gte", "lt", "lte"]:
#             self.assertIn(f"num_field__{option}", fields)

#         # Teste str_field: Basisfeld plus string_options-Felder.
#         self.assertIn("str_field", fields)
#         for option in [
#             "exact",
#             "icontains",
#             "contains",
#             "in",
#             "startswith",
#             "endswith",
#         ]:
#             self.assertIn(f"str_field__{option}", fields)

#         # Teste measurement_field: Es sollten eigene Felder für den Wert und die Einheit
#         # sowie entsprechende number_options-Felder existieren.
#         self.assertIn("measurement_field_value", fields)
#         self.assertIn("measurement_field_unit", fields)
#         for option in ["exact", "gt", "gte", "lt", "lte"]:
#             self.assertIn(f"measurement_field_value__{option}", fields)
#             self.assertIn(f"measurement_field_unit__{option}", fields)

#     def test_create_filter_options_registry_cache(self):
#         # Überprüfe, dass _createFilterOptions den Filtertyp cached.
#         class DummyManager:
#             __name__ = "DummyManager"

#             class Interface:
#                 input_fields = {}

#                 @staticmethod
#                 def getAttributeTypes():
#                     return {"num_field": int}

#         # Leere das Registry
#         GraphQL.graphql_filter_type_registry = {}
#         filter_class_first = GraphQL._createFilterOptions("dummy", DummyManager)  # type: ignore
#         filter_class_second = GraphQL._createFilterOptions("dummy", DummyManager)  # type: ignore
#         # Beide Aufrufe sollten denselben Typ zurückgeben
#         self.assertEqual(filter_class_first, filter_class_second)
