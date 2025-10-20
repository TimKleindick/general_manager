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

    def test_missing_error_template_variable_error(self):
        """Test that MissingErrorTemplateVariableError is raised when custom error template is incomplete."""
        from general_manager.rule.rule import MissingErrorTemplateVariableError

        def func(item: DummyObject) -> bool:
            return item.price < 100.0 and item.quantity > 5

        x = DummyObject(price=150.75, quantity=3)
        rule = Rule(func, custom_error_message="Price is too high: {price}")

        result = rule.evaluate(x)
        self.assertFalse(result)

        # Should raise error because 'quantity' is missing from custom template
        with self.assertRaises(MissingErrorTemplateVariableError) as ctx:
            rule.getErrorMessage()
        self.assertIn("quantity", str(ctx.exception))
        self.assertIn("does not contain all used variables", str(ctx.exception))

    def test_error_message_generation_error(self):
        """Test that ErrorMessageGenerationError is raised when getting error before evaluation."""
        from general_manager.rule.rule import ErrorMessageGenerationError

        def func(item: DummyObject) -> bool:
            return item.price < 100.0

        rule = Rule(func)

        # Attempt to get error message without evaluating first
        with self.assertRaises(ErrorMessageGenerationError) as ctx:
            rule.getErrorMessage()
        self.assertIn("No input provided", str(ctx.exception))

    def test_rule_with_multiple_parameters(self):
        """Test that rules can work with multiple parameters."""

        def func(item: DummyObject, threshold: int) -> bool:
            return item.value > threshold

        x = DummyObject(value=50)
        rule = Rule(func)

        # Evaluate with item parameter
        result = rule.evaluate(x)
        # Without the threshold parameter, this should handle gracefully
        self.assertIsNotNone(result)

    def test_rule_extraction_with_nested_attributes(self):
        """Test variable extraction from nested attribute access."""

        def func(item: DummyObject) -> bool:
            return item.nested.value > 10

        class Nested:
            value = 15

        x = DummyObject(nested=Nested())
        rule = Rule(func)

        result = rule.evaluate(x)
        self.assertTrue(result)

    def test_rule_with_complex_boolean_logic(self):
        """Test rules with complex AND/OR boolean conditions."""

        def func(item: DummyObject) -> bool:
            return (item.a > 5 and item.b < 10) or item.c == 0

        x = DummyObject(a=3, b=8, c=1)
        rule = Rule(func)

        result = rule.evaluate(x)
        self.assertFalse(result)

        y = DummyObject(a=6, b=8, c=1)
        result = rule.evaluate(y)
        self.assertTrue(result)

        z = DummyObject(a=3, b=12, c=0)
        result = rule.evaluate(z)
        self.assertTrue(result)

    def test_rule_last_result_tracking(self):
        """Test that rules correctly track last result and last input."""

        def func(item: DummyObject) -> bool:
            return item.value > 10

        rule = Rule(func)

        # Before evaluation
        self.assertIsNone(rule._last_result)
        self.assertIsNone(rule._last_input)

        x = DummyObject(value=15)
        result = rule.evaluate(x)

        # After successful evaluation
        self.assertTrue(result)
        self.assertTrue(rule._last_result)
        self.assertEqual(rule._last_input, x)

        y = DummyObject(value=5)
        result = rule.evaluate(y)

        # After failed evaluation
        self.assertFalse(result)
        self.assertFalse(rule._last_result)
        self.assertEqual(rule._last_input, y)

    def test_rule_with_ignore_if_none_multiple_variables(self):
        """Test ignore_if_none with multiple variables where some are None."""

        def func(item: DummyObject) -> bool:
            return item.a > 5 and item.b < 10

        x = DummyObject(a=None, b=8)
        rule = Rule(func, ignore_if_none=True)

        result = rule.evaluate(x)
        self.assertIsNone(result)
        self.assertIsNone(rule.getErrorMessage())

        y = DummyObject(a=6, b=None)
        result = rule.evaluate(y)
        self.assertIsNone(result)

        z = DummyObject(a=6, b=8)
        result = rule.evaluate(z)
        self.assertTrue(result)

    def test_rule_custom_error_message_with_all_variables(self):
        """Test custom error message that includes all rule variables."""

        def func(item: DummyObject) -> bool:
            return item.price < item.max_price

        x = DummyObject(price=150.0, max_price=100.0)
        rule = Rule(
            func,
            custom_error_message="Price {price} exceeds maximum {max_price}",
        )

        result = rule.evaluate(x)
        self.assertFalse(result)

        error = rule.getErrorMessage()
        self.assertIsNotNone(error)
        self.assertIn("price", error)
        self.assertIn("150.0", error["price"])
        self.assertIn("100.0", error["price"])

    def test_rule_variable_extraction_from_attributes(self):
        """Test that rule correctly extracts variable names from attribute access."""

        def func(item: DummyObject) -> bool:
            return item.first_name != "" and item.last_name != ""

        rule = Rule(func)

        # Check extracted variables
        self.assertIn("first_name", rule._variables)
        self.assertIn("last_name", rule._variables)

    def test_rule_comparison_operators_coverage(self):
        """Test all comparison operators in rules."""

        # Less than
        def func_lt(item: DummyObject) -> bool:
            return item.value < 10

        # Less than or equal
        def func_lte(item: DummyObject) -> bool:
            return item.value <= 10

        # Greater than
        def func_gt(item: DummyObject) -> bool:
            return item.value > 10

        # Greater than or equal
        def func_gte(item: DummyObject) -> bool:
            return item.value >= 10

        # Equal
        def func_eq(item: DummyObject) -> bool:
            return item.value == 10

        # Not equal
        def func_ne(item: DummyObject) -> bool:
            return item.value != 10

        x = DummyObject(value=10)

        self.assertFalse(Rule(func_lt).evaluate(x))
        self.assertTrue(Rule(func_lte).evaluate(x))
        self.assertFalse(Rule(func_gt).evaluate(x))
        self.assertTrue(Rule(func_gte).evaluate(x))
        self.assertTrue(Rule(func_eq).evaluate(x))
        self.assertFalse(Rule(func_ne).evaluate(x))

    def test_rule_with_is_and_is_not_operators(self):
        """Test rules using 'is' and 'is not' identity operators."""

        def func_is(item: DummyObject) -> bool:
            return item.value is None

        def func_is_not(item: DummyObject) -> bool:
            return item.value is not None

        x = DummyObject(value=None)
        y = DummyObject(value=10)

        self.assertTrue(Rule(func_is).evaluate(x))
        self.assertFalse(Rule(func_is).evaluate(y))

        self.assertFalse(Rule(func_is_not).evaluate(x))
        self.assertTrue(Rule(func_is_not).evaluate(y))

    def test_rule_with_in_and_not_in_operators(self):
        """Test rules using 'in' and 'not in' membership operators."""

        def func_in(item: DummyObject) -> bool:
            return item.value in [1, 2, 3, 4, 5]

        def func_not_in(item: DummyObject) -> bool:
            return item.value not in [1, 2, 3, 4, 5]

        x = DummyObject(value=3)
        y = DummyObject(value=10)

        self.assertTrue(Rule(func_in).evaluate(x))
        self.assertFalse(Rule(func_in).evaluate(y))

        self.assertFalse(Rule(func_not_in).evaluate(x))
        self.assertTrue(Rule(func_not_in).evaluate(y))