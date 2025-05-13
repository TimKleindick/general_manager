from django.test import TestCase
from general_manager.auxiliary.filterParser import (
    parse_filters,
    create_filter_function,
    apply_lookup,
)
from unittest.mock import MagicMock, patch
from general_manager.manager.input import Input


class TestFilterParser(TestCase):
    def test_parse_filters_simple(self):
        possible_values = {
            "name": Input(str),
            "age": Input(int),
        }
        filters = parse_filters({"name__exact": "John"}, possible_values)
        self.assertEqual(len(filters), 1)
        self.assertEqual(filters["name"]["filter_funcs"][0]("John"), True)
        self.assertEqual(filters["name"]["filter_funcs"][0]("Doe"), False)

    def test_parse_filters_with_list(self):
        possible_values = {
            "name": Input(str),
            "age": Input(int),
        }
        filters = parse_filters({"name__in": ["John", "Doe"]}, possible_values)
        self.assertEqual(len(filters), 1)
        self.assertEqual(filters["name"]["filter_funcs"][0]("John"), True)
        self.assertEqual(filters["name"]["filter_funcs"][0]("Doe"), True)
        self.assertEqual(filters["name"]["filter_funcs"][0]("Jane"), False)

    def test_parse_filters_with_multiple_conditions(self):
        possible_values = {
            "name": Input(str),
            "age": Input(int),
        }
        filters = parse_filters({"name__exact": "John", "age__gt": 30}, possible_values)
        self.assertEqual(len(filters), 2)
        self.assertEqual(filters["name"]["filter_funcs"][0]("John"), True)
        self.assertEqual(filters["age"]["filter_funcs"][0](35), True)
        self.assertEqual(filters["age"]["filter_funcs"][0](25), False)

    def test_parse_filters_without_operators(self):
        possible_values = {
            "name": Input(str),
            "age": Input(int),
        }
        filters = parse_filters({"name": "John"}, possible_values)
        self.assertEqual(len(filters), 1)
        self.assertEqual(filters["name"]["filter_funcs"][0]("John"), True)
        self.assertEqual(filters["name"]["filter_funcs"][0]("Doe"), False)

    def test_parse_filters_with_general_manager(self):
        possible_values = {
            "manager": Input(MagicMock),
            "age": Input(int),
        }
        mock_manager = MagicMock()
        mock_manager.id = 1
        with patch(
            "general_manager.auxiliary.filterParser.issubclass", return_value=True
        ):

            filters = parse_filters({"manager__exact": mock_manager}, possible_values)
            self.assertEqual(len(filters), 1)
            self.assertEqual(
                filters["manager"]["filter_kwargs"],
                {"exact": mock_manager},
            )

            filters = parse_filters({"manager": mock_manager}, possible_values)
            self.assertEqual(len(filters), 1)
            self.assertEqual(
                filters["manager"]["filter_kwargs"],
                {"id": mock_manager.id},
            )

    def test_parse_filters_invalid_field(self):
        possible_values = {
            "name": Input(str),
            "age": Input(int),
        }
        with self.assertRaises(ValueError):
            parse_filters({"unknown_field__exact": "value"}, possible_values)

    def test_filter_function_with_deep_lookup(self):
        mock_manager = MagicMock()
        mock_manager.address.city = "New York"

        filter_function = create_filter_function("address__city", "New York")
        self.assertTrue(filter_function(mock_manager))

        mock_manager.address.city = "Los Angeles"
        self.assertFalse(filter_function(mock_manager))

        class Foo:
            def bar(self):
                pass

        m = MagicMock(spec=Foo)
        self.assertFalse(filter_function(m))

    def test_create_filter_function(self):
        filter_func = create_filter_function("exact", "value")
        self.assertTrue(filter_func("value"))
        self.assertFalse(filter_func("other_value"))

        filter_func = create_filter_function("in", ["value1", "value2"])
        self.assertTrue(filter_func("value1"))
        self.assertTrue(filter_func("value2"))
        self.assertFalse(filter_func("other_value"))

    def test_apply_lookup(self):
        self.assertTrue(apply_lookup("value", "exact", "value"))
        self.assertFalse(apply_lookup("value", "exact", "other_value"))
        self.assertTrue(apply_lookup("value1", "in", ["value1", "value2"]))
        self.assertFalse(apply_lookup("other_value", "in", ["value1", "value2"]))
        self.assertTrue(apply_lookup("value", "contains", "val"))
        self.assertFalse(apply_lookup("value", "contains", "other"))
        self.assertTrue(apply_lookup("value", "startswith", "val"))
        self.assertFalse(apply_lookup("value", "startswith", "other"))
        self.assertTrue(apply_lookup("value", "endswith", "ue"))
        self.assertFalse(apply_lookup("value", "endswith", "other"))
        self.assertTrue(apply_lookup("value", "lt", "z"))
        self.assertFalse(apply_lookup("value", "lt", "a"))
        self.assertTrue(apply_lookup("value", "lte", "value"))
        self.assertFalse(apply_lookup("value", "lte", "other_value"))
        self.assertTrue(apply_lookup("value", "gt", "a"))
        self.assertFalse(apply_lookup("value", "gt", "z"))
        self.assertTrue(apply_lookup(10, "gte", 8))
        self.assertFalse(apply_lookup(1, "gte", 10))

    def test_apply_lookup_invalid(self):
        self.assertFalse(apply_lookup("value", "invalid_lookup", "value"))
        self.assertFalse(apply_lookup("value", "in", "not_a_list"))
        self.assertFalse(apply_lookup("value", "exact", 123))
        self.assertFalse(apply_lookup("value", "in", 123))
        self.assertFalse(apply_lookup("value", "exact", None))
        self.assertFalse(apply_lookup("value", "in", None))
