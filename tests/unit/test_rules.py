from django.test import TestCase, override_settings
from datetime import datetime
import ast
from general_manager.rule.rule import (
    InvalidRuleHandlerConfigurationError,
    Rule,
)
from typing import cast
from general_manager.rule.handler import BaseRuleHandler

NONE_VALUE = None


class DummyObject:
    """Generic helper object that accepts arbitrary attributes for tests."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class CustomLenHandler(BaseRuleHandler):
    """Test handler that replaces the built-in len handler."""

    function_name = "len"

    def handle(
        self,
        node: ast.AST,
        left: ast.expr | None,
        right: ast.expr | None,
        op: ast.cmpop | None,
        var_values: dict[str, object | None],
        rule: object,
    ) -> dict[str, str]:
        return {"custom": "custom len handler"}


class FirstCustomSumHandler(BaseRuleHandler):
    """First test handler for duplicate custom-handler registration order."""

    function_name = "sum"

    def handle(
        self,
        node: ast.AST,
        left: ast.expr | None,
        right: ast.expr | None,
        op: ast.cmpop | None,
        var_values: dict[str, object | None],
        rule: object,
    ) -> dict[str, str]:
        return {"custom": "first sum handler"}


class SecondCustomSumHandler(BaseRuleHandler):
    """Second test handler for duplicate custom-handler registration order."""

    function_name = "sum"

    def handle(
        self,
        node: ast.AST,
        left: ast.expr | None,
        right: ast.expr | None,
        op: ast.cmpop | None,
        var_values: dict[str, object | None],
        rule: object,
    ) -> dict[str, str]:
        return {"custom": "second sum handler"}


class MissingFunctionNameHandler(BaseRuleHandler):
    """Handler with no dispatch key."""

    def handle(
        self,
        node: ast.AST,
        left: ast.expr | None,
        right: ast.expr | None,
        op: ast.cmpop | None,
        var_values: dict[str, object | None],
        rule: object,
    ) -> dict[str, str]:
        return {}


class EmptyFunctionNameHandler(BaseRuleHandler):
    """Handler with an empty dispatch key."""

    function_name = ""

    def handle(
        self,
        node: ast.AST,
        left: ast.expr | None,
        right: ast.expr | None,
        op: ast.cmpop | None,
        var_values: dict[str, object | None],
        rule: object,
    ) -> dict[str, str]:
        return {}


class NoneFunctionNameHandler(BaseRuleHandler):
    """Handler with a non-string dispatch key."""

    function_name = None

    def handle(
        self,
        node: ast.AST,
        left: ast.expr | None,
        right: ast.expr | None,
        op: ast.cmpop | None,
        var_values: dict[str, object | None],
        rule: object,
    ) -> dict[str, str]:
        return {}


class NumericFunctionNameHandler(BaseRuleHandler):
    """Handler with a numeric dispatch key."""

    function_name = 123

    def handle(
        self,
        node: ast.AST,
        left: ast.expr | None,
        right: ast.expr | None,
        op: ast.cmpop | None,
        var_values: dict[str, object | None],
        rule: object,
    ) -> dict[str, str]:
        return {}


class NotARuleHandler:
    """Importable class without the BaseRuleHandler contract."""

    pass


class RuleTests(TestCase):
    @override_settings(
        GENERAL_MANAGER={
            "RULE_HANDLERS": ["tests.unit.test_rules.CustomLenHandler"],
        }
    )
    def test_custom_rule_handler_replaces_builtin_by_function_name(self):
        """Later custom handlers replace built-ins with the same function_name."""

        def func(item: DummyObject) -> bool:
            return len(item.name) > 3

        rule = Rule(func)
        result = rule.evaluate(DummyObject(name="abc"))

        self.assertFalse(result)
        self.assertEqual(rule.get_error_message(), {"custom": "custom len handler"})

    @override_settings(
        GENERAL_MANAGER={
            "RULE_HANDLERS": [
                "tests.unit.test_rules.FirstCustomSumHandler",
                "tests.unit.test_rules.SecondCustomSumHandler",
            ],
        }
    )
    def test_later_custom_rule_handler_replaces_earlier_custom_handler(self):
        """Custom handlers are registered in order, with later duplicates winning."""

        def func(item: DummyObject) -> bool:
            return sum(item.values) > 10

        rule = Rule(func)
        result = rule.evaluate(DummyObject(values=[1, 2]))

        self.assertFalse(result)
        self.assertEqual(rule.get_error_message(), {"custom": "second sum handler"})

    @override_settings(
        GENERAL_MANAGER={
            "RULE_HANDLERS": ["tests.unit.test_rules.NotARuleHandler"],
        }
    )
    def test_custom_rule_handler_must_subclass_base_handler(self):
        """Invalid custom handler classes fail during Rule construction."""

        def func(item: DummyObject) -> bool:
            return item.value > 1

        with self.assertRaises(InvalidRuleHandlerConfigurationError):
            Rule(func)

    @override_settings(
        GENERAL_MANAGER={
            "RULE_HANDLERS": ["tests.unit.test_rules.missing_handler"],
        }
    )
    def test_custom_rule_handler_invalid_path_propagates_import_error(self):
        """Invalid handler import paths surface Django import errors."""

        def func(item: DummyObject) -> bool:
            return item.value > 1

        with self.assertRaises(ImportError):
            Rule(func)

    def test_custom_rule_handler_function_name_must_be_non_empty_string(self):
        """Invalid handler dispatch keys fail deliberately during Rule construction."""

        invalid_paths = [
            "tests.unit.test_rules.MissingFunctionNameHandler",
            "tests.unit.test_rules.EmptyFunctionNameHandler",
            "tests.unit.test_rules.NoneFunctionNameHandler",
            "tests.unit.test_rules.NumericFunctionNameHandler",
        ]

        def func(item: DummyObject) -> bool:
            return item.value > 1

        for path in invalid_paths:
            with (
                self.subTest(path=path),
                override_settings(GENERAL_MANAGER={"RULE_HANDLERS": [path]}),
                self.assertRaises(InvalidRuleHandlerConfigurationError),
            ):
                Rule(func)

    def test_rule_handlers_setting_must_be_sequence_of_paths(self):
        """Invalid RULE_HANDLERS container shapes fail during Rule construction."""

        invalid_values = [
            "tests.unit.test_rules.CustomLenHandler",
            {"handler": "tests.unit.test_rules.CustomLenHandler"},
            123,
        ]

        def func(item: DummyObject) -> bool:
            return item.value > 1

        for value in invalid_values:
            with (
                self.subTest(value=value),
                override_settings(GENERAL_MANAGER={"RULE_HANDLERS": value}),
                self.assertRaises(InvalidRuleHandlerConfigurationError),
            ):
                Rule(func)

    def test_rule_handler_entries_must_be_non_empty_strings(self):
        """Invalid RULE_HANDLERS entries fail before import_string() is called."""

        invalid_entries = [
            None,
            123,
            CustomLenHandler,
            "",
        ]

        def func(item: DummyObject) -> bool:
            return item.value > 1

        for entry in invalid_entries:
            with (
                self.subTest(entry=entry),
                override_settings(GENERAL_MANAGER={"RULE_HANDLERS": [entry]}),
                self.assertRaises(InvalidRuleHandlerConfigurationError),
            ):
                Rule(func)

    def test_rule_with_floats(self):
        """
        Verifies that a Rule comparing a float field produces a failing evaluation and the correct error message.
        Creates a DummyObject with price 150.75, constructs a Rule checking price < 100.0, expects evaluate() to return False and get_error_message() to return {"price": "[price] (150.75) must be < 100.0!"}.
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
        error_message = rule.get_error_message()
        expected_error = {"price": "[price] (150.75) must be < 100.0!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_booleans(self):
        """Validate Rule behavior with boolean fields."""

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
        error_message = rule.get_error_message()
        expected_error = {"is_active": "[is_active] (False) must be is True!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_dates(self):
        """Validate Rule behavior with date comparisons."""

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
        error_message = rule.get_error_message()
        expected_error = {
            "start_date": "[start_date] (2022-01-20) must be < [end_date] (2022-01-15)!",
            "end_date": "[start_date] (2022-01-20) must be < [end_date] (2022-01-15)!",
        }
        self.assertEqual(error_message, expected_error)

    def test_rule_with_integers(self):
        """Validate Rule behavior with integer comparisons."""

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
        error_message = rule.get_error_message()
        expected_error = {"age": "[age] (16) must be >= 18!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_integers_reverse(self):
        """Validate Rule behavior with integer comparisons when operands are reversed."""

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
        error_message = rule.get_error_message()
        expected_error = {"age": "18 must be <= [age] (16)!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_strings(self):
        """Validate Rule behavior with string length checks."""

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
        error_message = rule.get_error_message()
        expected_error = {"username": "[username] (abc) is too short (min length 5)!"}
        self.assertEqual(error_message, expected_error)

    def test_rule_with_chained_len_comparison_reports_selected_leg(self):
        def func(item: DummyObject) -> bool:
            return 1 < len(item.username) < 5

        x = DummyObject(username="abcdef")
        rule = Rule(func)

        self.assertFalse(rule.evaluate(x))
        self.assertEqual(
            rule.get_error_message(),
            {"username": "[username] (abcdef) is too long (max length 4)!"},
        )

    def test_rule_with_chained_len_comparison_reports_lower_bound_from_right_call(self):
        def func(item: DummyObject) -> bool:
            return 1 < len(item.username) < 5

        x = DummyObject(username="a")
        rule = Rule(func)

        self.assertFalse(rule.evaluate(x))
        self.assertEqual(
            rule.get_error_message(),
            {"username": "[username] (a) is too short (min length 2)!"},
        )

    def test_rule_with_chained_standard_comparison_reports_failing_leg(self):
        def func(item: DummyObject) -> bool:
            return 1 < item.score < 5

        x = DummyObject(score=0)
        rule = Rule(func)

        self.assertFalse(rule.evaluate(x))
        self.assertEqual(
            rule.get_error_message(),
            {"score": "1 must be < [score] (0)!"},
        )

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
        error_message = rule.get_error_message()
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
        rule.validate_custom_error_message()
        result = rule.evaluate(x)
        self.assertFalse(result)
        error_message = rule.get_error_message()
        expected_error = {
            "quantity": "Ordered quantity (10) exceeds available stock (5).",
            "stock": "Ordered quantity (10) exceeds available stock (5).",
        }
        self.assertEqual(error_message, expected_error)

    def test_rule_with_missing_custom_error_variables(self):
        """
        Checks that validate_custom_error_message raises a ValueError when a custom error message lacks required template variables.
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
            rule.validate_custom_error_message()

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
        error_message = rule.get_error_message()
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
        """Ensure a Rule whose predicate uses no variables evaluates and reports correctly."""

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
        error_message = rule.get_error_message()
        self.assertIsNone(error_message)

    def test_rule_with_exception_in_function(self):
        """Verify the Rule class propagates exceptions raised by the predicate."""

        def func(x):
            return x.non_existent_attribute > 0

        x = DummyObject()
        rule = Rule(func)
        with self.assertRaises(AttributeError):
            rule.evaluate(x)

    def test_rule_property_access(self):
        """Ensure Rule exposes the expected property accessors."""

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
        self.assertIsNone(rule.custom_error_message)
        self.assertEqual(rule.variables, ["value"])
        self.assertIsNone(rule.last_evaluation_result)
        self.assertIsNone(rule.last_evaluation_input)
        x = DummyObject(value=10)
        rule.evaluate(x)
        self.assertEqual(rule.last_evaluation_result, False)
        self.assertEqual(rule.last_evaluation_input, x)

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
        error_message = rule.get_error_message()
        self.assertIsNone(error_message)

    def test_rule_with_none_value(self):
        """Ensure Rules handle None values correctly when ignore_if_none is enabled."""

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
        self.assertIsNone(rule.get_error_message())

        y = DummyObject(optional_value=3)
        rule = Rule(func, ignore_if_none=True)
        result = rule.evaluate(y)
        self.assertTrue(result)
        self.assertIsNone(rule.get_error_message())

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
            rule.get_error_message()
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
            rule.get_error_message()
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
        self.assertIsNone(rule.get_error_message())

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

        error = rule.get_error_message()
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

    def test_evaluate_comparison_leg_handles_none_safe_operations(self):
        """None-safe comparison operators evaluate before unknown-operand fallback."""

        def func(item: DummyObject) -> bool:
            return item.value is None

        x = DummyObject(value=None, values=[None])
        rule = Rule(func, ignore_if_none=False)
        rule.evaluate(x)

        cases = [
            ("item.value is None", True),
            ("item.value is not None", False),
            ("item.value == NONE_VALUE", True),
            ("item.value != NONE_VALUE", False),
            ("item.value in item.values", True),
            ("item.value not in item.values", False),
        ]

        for expression, expected in cases:
            node = ast.parse(expression, mode="eval").body
            assert isinstance(node, ast.Compare)
            with self.subTest(expression=expression):
                self.assertIs(
                    rule._evaluate_comparison_leg(
                        node.left,
                        node.comparators[0],
                        node.ops[0],
                    ),
                    expected,
                )

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

    def test_eval_known_call_only_handles_unqualified_builtins(self):
        """Qualified helper calls are not evaluated with built-in call semantics."""

        def func(item: DummyObject) -> bool:
            return item.value > 1

        x = DummyObject(value=0, values=[1, 2])
        rule = Rule(func)
        rule.evaluate(x)

        direct_call = ast.parse("sum(item.values)", mode="eval").body
        qualified_call = ast.parse("helpers.sum(item.values)", mode="eval").body
        assert isinstance(direct_call, ast.Call)
        assert isinstance(qualified_call, ast.Call)

        self.assertEqual(rule._eval_known_call(direct_call), 3)
        self.assertIsNone(rule._eval_known_call(qualified_call))
