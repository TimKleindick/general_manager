# generalManager/src/rule/handlers.py

from __future__ import annotations
import ast
from typing import Dict, Optional, TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from general_manager.rule.rule import Rule


class BaseRuleHandler(ABC):
    """Schnittstelle für Rule-Handler."""

    function_name: str  # ClassVar, der Name, unter dem dieser Handler registriert wird

    @abstractmethod
    def handle(
        self,
        node: ast.AST,
        left: Optional[ast.expr],
        right: Optional[ast.expr],
        op: Optional[ast.cmpop],
        var_values: Dict[str, Optional[object]],
        rule: Rule,
    ) -> Dict[str, str]:
        """
        Handles an AST node to generate error messages for a comparison or function call.
        
        This abstract method should be implemented by subclasses to analyze the given AST node,
        extract relevant information, and return a dictionary mapping variable names to error
        messages based on the rule and variable values.
        """
        pass


class FunctionHandler(BaseRuleHandler, ABC):
    """
    Handler für Funktionsaufrufe wie len(), max(), min(), sum().
    """

    def handle(
        self,
        node: ast.AST,
        left: Optional[ast.expr],
        right: Optional[ast.expr],
        op: Optional[ast.cmpop],
        var_values: Dict[str, Optional[object]],
        rule: Rule,
    ) -> Dict[str, str]:
        """
        Handles AST comparison nodes involving a specific function call and delegates aggregation.
        
        Validates that the node represents a comparison where the left side is a function call
        (e.g., sum, max, min) with at least one argument, then calls the aggregate method to
        generate error messages based on the comparison.
        
        Raises:
            ValueError: If the left node is not a valid function call with arguments.
        
        Returns:
            A dictionary mapping variable names to error messages if the rule is violated;
            otherwise, an empty dictionary.
        """
        if not isinstance(node, ast.Compare):
            return {}
        compare_node = node

        left_node = compare_node.left
        right_node = compare_node.comparators[0]
        op_symbol = rule._get_op_symbol(op)

        if not (isinstance(left_node, ast.Call) and left_node.args):
            raise ValueError(f"Invalid left node for {self.function_name} function")
        arg_node = left_node.args[0]

        return self.aggregate(
            arg_node,
            right_node,
            op_symbol,
            var_values,
            rule,
        )

    @abstractmethod
    def aggregate(
        self,
        arg_node: ast.expr,
        right_node: ast.expr,
        op_symbol: str,
        var_values: Dict[str, Optional[object]],
        rule: Rule,
    ) -> Dict[str, str]:
        """
        Aggregates values from the argument node and generates an error message.
        
        This abstract method should be implemented by subclasses to compute an aggregate
        value (such as length, sum, maximum, or minimum) from the provided argument node,
        compare it to the threshold specified by the right node and operator, and return
        a dictionary mapping variable names to error messages if validation fails.
        """
        raise NotImplementedError("Subclasses should implement this method")

    @staticmethod
    def getThreshold(
        op_symbol: str,
        right_value: int | float,
    ) -> int | float:
        """
        Adjusts the threshold value based on the comparison operator.
        
        Args:
            op_symbol: The comparison operator as a string (e.g., '>', '>=', '<', '<=').
            right_value: The numeric value on the right side of the comparison.
        
        Returns:
            The adjusted threshold value according to the operator semantics.
        """
        if op_symbol == ">":
            return right_value + 1
        elif op_symbol == ">=":
            return right_value
        elif op_symbol == "<":
            return right_value - 1
        elif op_symbol == "<=":
            return right_value
        else:
            return right_value


class LenHandler(FunctionHandler):
    function_name = "len"

    def aggregate(
        self,
        arg_node: ast.expr,
        right_node: ast.expr,
        op_symbol: str,
        var_values: Dict[str, Optional[object]],
        rule: Rule,
    ) -> Dict[str, str]:

        """
        Generates an error message if the length of a variable does not meet the specified threshold.
        
        Args:
            arg_node: The AST node representing the argument to the len() function.
            right_node: The AST node representing the comparison threshold.
            op_symbol: The comparison operator as a string (e.g., '>', '<=', '==').
            var_values: A mapping of variable names to their current values.
            rule: The rule instance providing node evaluation and name extraction.
        
        Returns:
            A dictionary mapping the variable name to a descriptive error message if its length violates the rule.
        
        Raises:
            ValueError: If the comparison threshold is not a numeric value.
        """
        var_name = rule._get_node_name(arg_node)
        var_value = var_values.get(var_name)

        # --- Hier der Typ-Guard für right_value ---
        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise ValueError("Invalid arguments for len function")
        right_value: int | float = raw

        threshold = self.getThreshold(op_symbol, right_value)

        # Fehlermeldung formulieren
        if op_symbol in (">", ">="):
            msg = f"[{var_name}] ({var_value}) is too short (min length {threshold})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] ({var_value}) is too long (max length {threshold})!"
        else:
            msg = f"[{var_name}] ({var_value}) must have a length of {right_value}!"

        return {var_name: msg}


class SumHandler(FunctionHandler):
    function_name = "sum"

    def aggregate(
        self,
        arg_node: ast.expr,
        right_node: ast.expr,
        op_symbol: str,
        var_values: Dict[str, Optional[object]],
        rule: Rule,
    ) -> Dict[str, str]:

        # Name und Wert holen
        """
        Aggregates the sum of an iterable variable and generates an error message based on a comparison.
        
        Calculates the sum of the values in the variable referenced by `arg_node`, compares it to a threshold derived from `right_node` and `op_symbol`, and returns a formatted error message if the sum does not meet the specified condition.
        
        Raises:
            ValueError: If the variable is not an iterable of numbers or if the threshold is not numeric.
        
        Returns:
            A dictionary mapping the variable name to an error message describing the sum constraint violation.
        """
        var_name = rule._get_node_name(arg_node)
        raw_iter = var_values.get(var_name)
        if not isinstance(raw_iter, (list, tuple)):
            raise ValueError("sum expects an iterable of numbers")
        total = sum(raw_iter)

        # Schwellenwert aus dem rechten Knoten
        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise ValueError("Invalid arguments for sum function")
        right_value = raw

        threshold = self.getThreshold(op_symbol, right_value)

        # Message formulieren
        if op_symbol in (">", ">="):
            msg = f"[{var_name}] (sum={total}) is too small (min sum {threshold})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] (sum={total}) is too large (max sum {threshold})!"
        else:
            msg = f"[{var_name}] (sum={total}) must be {right_value}!"

        return {var_name: msg}


class MaxHandler(FunctionHandler):
    function_name = "max"

    def aggregate(
        self,
        arg_node: ast.expr,
        right_node: ast.expr,
        op_symbol: str,
        var_values: Dict[str, Optional[object]],
        rule: Rule,
    ) -> Dict[str, str]:

        """
        Aggregates the maximum value of an iterable variable and generates an error message if it does not satisfy the specified comparison.
        
        Args:
            arg_node: AST node representing the variable to aggregate.
            right_node: AST node representing the comparison threshold.
            op_symbol: Comparison operator as a string (e.g., '>', '<=', '==').
            var_values: Dictionary mapping variable names to their values.
            rule: Rule instance providing node evaluation and naming utilities.
        
        Returns:
            A dictionary mapping the variable name to an error message if the maximum value fails the comparison.
        
        Raises:
            ValueError: If the variable is not a non-empty iterable or the threshold is not numeric.
        """
        var_name = rule._get_node_name(arg_node)
        raw_iter = var_values.get(var_name)
        if not isinstance(raw_iter, (list, tuple)) or len(raw_iter) == 0:
            raise ValueError("max expects a non-empty iterable")
        current = max(raw_iter)

        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise ValueError("Invalid arguments for max function")
        right_value = raw

        threshold = self.getThreshold(op_symbol, right_value)

        if op_symbol in (">", ">="):
            msg = f"[{var_name}] (max={current}) is too small (min {threshold})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] (max={current}) is too large (max {threshold})!"
        else:
            msg = f"[{var_name}] (max={current}) must be {right_value}!"

        return {var_name: msg}


class MinHandler(FunctionHandler):
    function_name = "min"

    def aggregate(
        self,
        arg_node: ast.expr,
        right_node: ast.expr,
        op_symbol: str,
        var_values: Dict[str, Optional[object]],
        rule: Rule,
    ) -> Dict[str, str]:

        """
        Aggregates the minimum value of an iterable and generates an error message based on a comparison.
        
        Evaluates the minimum value of the variable referenced by `arg_node`, compares it to a threshold derived from `right_node` and `op_symbol`, and returns a formatted error message if the value does not meet the specified condition.
        
        Raises:
            ValueError: If the variable is not a non-empty iterable or if the threshold is not numeric.
        
        Returns:
            A dictionary mapping the variable name to an error message describing the minimum value constraint violation.
        """
        var_name = rule._get_node_name(arg_node)
        raw_iter = var_values.get(var_name)
        if not isinstance(raw_iter, (list, tuple)) or len(raw_iter) == 0:
            raise ValueError("min expects a non-empty iterable")
        current = min(raw_iter)

        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise ValueError("Invalid arguments for min function")
        right_value = raw

        threshold = self.getThreshold(op_symbol, right_value)

        if op_symbol in (">", ">="):
            msg = f"[{var_name}] (min={current}) is too small (min {threshold})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] (min={current}) is too large (max {threshold})!"
        else:
            msg = f"[{var_name}] (min={current}) must be {right_value}!"

        return {var_name: msg}
