from django.test import TestCase
from datetime import datetime
from general_manager.rule.rule import (
    Rule,
)
from typing import cast


class DummyObject:
    """Ein generisches Objekt zum Testen mit beliebigen Attributen."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class RuleTests(TestCase):
    def test_rule_with_floats(self):
        """Testet die Rule-Klasse mit Gleitkommazahlen."""

        def func(item: DummyObject) -> bool:
            return item.price < 100.0

        x = DummyObject(price=150.75)
        rule = Rule(func)
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        expected_error = {"price": "[price] (150.75) must be < 100.0!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_booleans(self):
        """Testet die Rule-Klasse mit booleschen Werten."""

        def func(item: DummyObject) -> bool:
            return item.is_active is True

        x = DummyObject(is_active=False)
        rule = Rule(func)
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        expected_error = {"is_active": "[is_active] (False) must be is True!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_dates(self):
        """Testet die Rule-Klasse mit Datumswerten."""

        def func(item: DummyObject) -> bool:
            return item.start_date < item.end_date

        x = DummyObject(
            start_date=datetime.strptime("2022-01-20", "%Y-%m-%d").date(),
            end_date=datetime.strptime("2022-01-15", "%Y-%m-%d").date(),
        )
        rule = Rule(func)
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        expected_error = {
            "start_date": "[start_date] (2022-01-20) must be < [end_date] (2022-01-15)!",
            "end_date": "[start_date] (2022-01-20) must be < [end_date] (2022-01-15)!",
        }
        self.assertEqual(error_message, expected_error)

    def test_rule_with_integers(self):
        """Testet die Rule-Klasse mit Ganzzahlen."""

        def func(item: DummyObject) -> bool:
            return item.age >= 18

        x = DummyObject(age=16)
        rule = Rule(func)
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        expected_error = {"age": "[age] (16) must be >= 18!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_integers_reverse(self):
        """Testet die Rule-Klasse mit Ganzzahlen."""

        def func(item: DummyObject) -> bool:
            return 18 <= item.age

        x = DummyObject(age=16)
        rule = Rule(func)
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        expected_error = {"age": "18 must be <= [age] (16)!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_strings(self):
        """Testet die Rule-Klasse mit Zeichenketten."""

        def func(item: DummyObject) -> bool:
            return len(item.username) >= 5

        x = DummyObject(username="abc")
        rule = Rule(func)
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        expected_error = {"username": "[username] (abc) is too short (min length 5)!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_lists(self):
        """Testet die Rule-Klasse mit Listen."""

        def func(item: DummyObject) -> bool:
            return len(item.items) > 0

        x = DummyObject(items=[])
        rule = Rule(func)
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        expected_error = {"items": "[items] ([]) is too short (min length 1)!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_custom_error_message(self):
        """Testet die Rule-Klasse mit benutzerdefinierter Fehlermeldung."""

        def func(item: DummyObject) -> bool:
            return item.quantity <= item.stock

        custom_message = (
            "Ordered quantity ({quantity}) exceeds available stock ({stock})."
        )
        x = DummyObject(quantity=10, stock=5)
        rule = Rule(func, custom_error_message=custom_message)
        rule.validateCustomErrorMessage()
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        expected_error = {
            "quantity": "Ordered quantity (10) exceeds available stock (5).",
            "stock": "Ordered quantity (10) exceeds available stock (5).",
        }
        self.assertEqual(error_message, expected_error)

    def test_rule_with_missing_custom_error_variables(self):
        """Testet, ob ein Fehler ausgelöst wird, wenn Variablen in der benutzerdefinierten Fehlermeldung fehlen."""

        def func(item: DummyObject) -> bool:
            return item.height >= 150

        custom_message = "Height must be at least 150 cm."
        rule = Rule(func, custom_error_message=custom_message)
        with self.assertRaises(ValueError):
            rule.validateCustomErrorMessage()

    def test_rule_with_complex_condition(self):
        """Testet die Rule-Klasse mit einer komplexen Bedingung."""

        def func(item: DummyObject) -> bool:
            return item.age >= 18 and item.has_permission

        x = DummyObject(age=20, has_permission=False)
        rule = Rule(func)
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        expected_error_a = {
            "age": "[age], [has_permission] combination is not valid",
            "has_permission": "[age], [has_permission] combination is not valid",
        }
        expected_error_b = {
            "age": "[has_permission], [age] combination is not valid",
            "has_permission": "[has_permission], [age] combination is not valid",
        }
        self.assertIn(error_message, [expected_error_a, expected_error_b])

    def test_rule_with_no_variables(self):
        """Testet die Rule-Klasse mit einer Funktion ohne Variablen."""

        def func(_: DummyObject) -> bool:
            return True

        x = DummyObject()
        rule = Rule(func)
        result = rule.evaluate(x)
        self.assertTrue(result)
        error_message = rule.getErrorMessage()
        self.assertIsNone(error_message)

    def test_rule_with_exception_in_function(self):
        """Testet die Rule-Klasse, wenn die Funktion eine Ausnahme auslöst."""

        def func(x):
            return x.non_existent_attribute > 0

        x = DummyObject()
        rule = Rule(func)
        with self.assertRaises(AttributeError):
            rule.evaluate(x)

    def test_rule_property_access(self):
        """Testet den Zugriff auf die Eigenschaften der Rule-Klasse."""

        def func(item: DummyObject) -> bool:
            return item.value == 42

        rule = Rule(func)
        self.assertEqual(rule.func, func)
        self.assertIsNone(rule.customErrorMessage)
        self.assertEqual(rule.variables, ["value"])
        self.assertIsNone(rule.lastEvaluationResult)
        self.assertIsNone(rule.lastEvaluationInput)
        x = DummyObject(value=10)
        rule.evaluate(x)
        self.assertEqual(rule.lastEvaluationResult, False)
        self.assertEqual(rule.lastEvaluationInput, x)

    def test_rule_with_type_hint(self):
        """Testet die Rule-Klasse mit Typ-Hinweisen."""

        def func(item: DummyObject) -> bool:
            return cast(float, item.price) < 100.0

        x = DummyObject(price=150.75)
        rule = Rule[DummyObject](func)  # type: ignore
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        self.assertIsNone(error_message)

    def test_rule_with_none_value(self):
        """Testet die Rule-Klasse mit None-Werten."""

        def func(item: DummyObject) -> bool:
            return item.optional_value > 2

        x = DummyObject(optional_value=None)
        rule = Rule(func, ignore_if_none=True)
        result = rule.evaluate(x)
        self.assertIsNone(result)
        self.assertIsNone(rule.getErrorMessage())

        y = DummyObject(optional_value=3)
        rule = Rule(func, ignore_if_none=True)
        result = rule.evaluate(y)
        self.assertTrue(result)
        self.assertIsNone(rule.getErrorMessage())
