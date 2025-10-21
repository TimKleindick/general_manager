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
        """
        Verifies that a Rule comparing a float field produces a failing evaluation and the correct error message.
        
        Creates a DummyObject with price 150.75, constructs a Rule checking price < 100.0, expects evaluate() to return False and getErrorMessage() to return {"price": "[price] (150.75) must be < 100.0!"}.
        """

        def func(item: DummyObject) -> bool:
            """
            Determines whether the item's price is less than 100.0.
            
            Parameters:
                item (DummyObject): Object with a numeric `price` attribute to evaluate.
            
            Returns:
                bool: `True` if the item's price is less than 100.0, `False` otherwise.
            """
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
            """
            Return whether the item's `is_active` attribute is True.
            
            Parameters:
                item (DummyObject): Object with an `is_active` attribute.
            
            Returns:
                True if `item.is_active` is True, False otherwise.
            """
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
            """
            Check whether the item's start_date is earlier than its end_date.
            
            Parameters:
                item (DummyObject): Object with comparable `start_date` and `end_date` attributes (e.g., date or datetime).
            
            Returns:
                bool: `true` if `item.start_date` is less than `item.end_date`, `false` otherwise.
            """
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
            """
            Determines whether the item's age is at least 18.
            
            Parameters:
                item (DummyObject): An object with an `age` attribute.
            
            Returns:
                bool: `True` if `item.age` is greater than or equal to 18, `False` otherwise.
            """
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
            """
            Determine whether the item's age is greater than or equal to 18.
            
            Parameters:
                item (DummyObject): Object with an `age` attribute to evaluate.
            
            Returns:
                True if `item.age` is greater than or equal to 18, False otherwise.
            """
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
            """
            Determines whether the item's username has at least 5 characters.
            
            Parameters:
                item (DummyObject): Object with a `username` attribute (string) to be checked.
            
            Returns:
                True if the username length is greater than or equal to 5, False otherwise.
            """
            return len(item.username) >= 5

        x = DummyObject(username="abc")
        rule = Rule(func)
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        expected_error = {"username": "[username] (abc) is too short (min length 5)!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_lists(self):
        """
        Verifies that a Rule based on list length marks an empty list as invalid and produces the expected error message.
        
        The test constructs a Rule that requires len(item.items) > 0, evaluates it against an object with an empty list, asserts the evaluation is False, and checks the returned error message matches the expected mapping for the `items` field.
        """

        def func(item: DummyObject) -> bool:
            """
            Checks whether the item's `items` list contains at least one element.
            
            Parameters:
                item (DummyObject): Object expected to have an `items` sequence attribute.
            
            Returns:
                bool: `True` if `item.items` has length greater than 0, `False` otherwise.
            """
            return len(item.items) > 0

        x = DummyObject(items=[])
        rule = Rule(func)
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.getErrorMessage()
        expected_error = {"items": "[items] ([]) is too short (min length 1)!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_custom_error_message(self):
        """
        Verifies that a Rule constructed with a custom error message template formats that template for each referenced variable when the rule fails.
        
        The test provides a template containing placeholders for `quantity` and `stock`, ensures the template is considered valid, evaluates the rule against an object where `quantity > stock`, and asserts that the resulting error mapping contains the formatted message for both `quantity` and `stock`.
        """

        def func(item: DummyObject) -> bool:
            """
            Checks whether the item's quantity is less than or equal to its stock.
            
            Parameters:
                item (DummyObject): Object with numeric `quantity` and `stock` attributes to compare.
            
            Returns:
                bool: `True` if `item.quantity` is less than or equal to `item.stock`, `False` otherwise.
            """
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
        """
        Checks that validateCustomErrorMessage raises a ValueError when a custom error message lacks required template variables.
        """

        def func(item: DummyObject) -> bool:
            """
            Determines whether the given item's height is at least 150.
            
            Parameters:
                item (DummyObject): Object with a numeric `height` attribute to evaluate.
            
            Returns:
                `true` if the item's height is greater than or equal to 150, `false` otherwise.
            """
            return item.height >= 150

        custom_message = "Height must be at least 150 cm."
        rule = Rule(func, custom_error_message=custom_message)
        with self.assertRaises(ValueError):
            rule.validateCustomErrorMessage()

    def test_rule_with_complex_condition(self):
        """
        Checks that a Rule with a compound condition (numeric comparison AND boolean flag) evaluates to False when the condition fails and produces an error mapping that attributes the same message to both involved variables, allowing either variable order in the message.
        """

        def func(item: DummyObject) -> bool:
            """
            Check that the given item's age is at least 18 and that the item has permission.
            
            Parameters:
                item (DummyObject): Object with integer `age` and boolean `has_permission` attributes.
            
            Returns:
                bool: `true` if `item.age` is greater than or equal to 18 and `item.has_permission` is truthy, `false` otherwise.
            """
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
            """
            Always evaluates to True.
            
            Parameters:
                _ (DummyObject): Input value that is ignored by the function.
            
            Returns:
                True: The function always returns True.
            """
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
            """
            Checks whether the item's `value` attribute equals 42.
            
            Parameters:
                item (DummyObject): Object expected to have a `value` attribute to compare.
            
            Returns:
                True if the item's `value` equals 42, False otherwise.
            """
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
        """
        Verifies a Rule correctly evaluates a predicate that uses a type hint cast.
        
        Creates a Rule whose predicate casts item.price to float and compares it to 100.0, asserts the evaluation is False for price 150.75, and asserts no error message is produced.
        """

        def func(item: DummyObject) -> bool:
            """
            Determines whether the item's price is less than 100.0.
            
            Parameters:
                item (DummyObject): Object with a numeric `price` attribute to evaluate.
            
            Returns:
                True if the item's price is less than 100.0, False otherwise.
            """
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
            """
            Determine whether the provided object's `optional_value` is greater than 2.
            
            Parameters:
                item (DummyObject): Object that exposes an `optional_value` attribute to compare.
            
            Returns:
                True if the object's `optional_value` is greater than 2, False otherwise.
            """
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