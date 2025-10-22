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
            "age": "[age] (20) must be >= 18!",
            "has_permission": "[age], [has_permission] combination is not valid",
        }
        expected_error_b = {
            "age": "[age] (20) must be >= 18!",
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
        rule._last_result = (
            False  # Manually set last result to simulate failed evaluation
        )

        # Attempt to get error message without evaluating first
        with self.assertRaises(ErrorMessageGenerationError) as ctx:
            rule.getErrorMessage()
        self.assertIn("No input provided", str(ctx.exception))

    def test_rule_with_multiple_parameters(self):
        """Test that rules can work with multiple parameters."""

        def func(item: DummyObject) -> bool:
            return item.value > item.threshold

        x = DummyObject(value=50, threshold=None)
        rule = Rule(func)

        # Evaluate with item parameter
        result = rule.evaluate(x)
        # Without the threshold parameter, this should handle gracefully
        self.assertIsNone(result)

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

        self.assertTrue(Rule(func_is, ignore_if_none=False).evaluate(x))
        self.assertFalse(Rule(func_is, ignore_if_none=False).evaluate(y))

        self.assertFalse(Rule(func_is_not, ignore_if_none=False).evaluate(x))
        self.assertTrue(Rule(func_is_not, ignore_if_none=False).evaluate(y))

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
