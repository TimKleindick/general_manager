"""Rule handler implementations that craft error messages from AST nodes."""

from __future__ import annotations
import ast
from typing import Dict, Optional, TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from general_manager.rule.rule import Rule


class BaseRuleHandler(ABC):
    """Define the protocol for generating rule-specific error messages."""

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
        Produce error messages for a comparison or function call node.

        Parameters:
            node (ast.AST): AST node representing the expression being evaluated.
            left (ast.expr | None): Left operand when applicable.
            right (ast.expr | None): Right operand when applicable.
            op (ast.cmpop | None): Comparison operator node.
            var_values (dict[str, object | None]): Resolved variable values used during evaluation.
            rule (Rule): Rule invoking the handler.

        Returns:
            dict[str, str]: Mapping of variable names to error messages.
        """
        pass


class FunctionHandler(BaseRuleHandler, ABC):
    """
    Base class for handlers that evaluate function-call expressions such as len(), max(), or sum().
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
        if not isinstance(node, ast.Compare):
            return {}
        compare_node = node

        left_node = compare_node.left
        right_node = compare_node.comparators[0]
        op_symbol = rule._get_op_symbol(op)

        if not (isinstance(left_node, ast.Call) and left_node.args):
            raise ValueError(f"Invalid left node for {self.function_name}() function")
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
        Analyse the call arguments and construct an error message payload.

        Parameters:
            arg_node (ast.expr): AST node representing the function argument.
            right_node (ast.expr): Node representing the comparison threshold.
            op_symbol (str): Symbolic representation of the comparison operator.
            var_values (dict[str, object | None]): Resolved values used during evaluation.
            rule (Rule): Rule requesting the aggregation.

        Returns:
            dict[str, str]: Mapping of variable names to error messages.
        """
        raise NotImplementedError("Subclasses should implement this method")


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
        Evaluate length-based limits and craft an error message when violated.

        Parameters:
            arg_node (ast.expr): AST node representing the iterable passed to `len`.
            right_node (ast.expr): Comparison threshold node.
            op_symbol (str): Operator symbol describing the comparison.
            var_values (dict[str, object | None]): Evaluated variable values.
            rule (Rule): Calling rule used for helper evaluations.

        Returns:
            dict[str, str]: Mapping containing a single error message keyed by variable name.

        Raises:
            ValueError: If the argument is invalid or the threshold is not numeric.
        """

        var_name = rule._get_node_name(arg_node)
        var_value = var_values.get(var_name)

        # --- Hier der Typ-Guard für right_value ---
        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise ValueError("Invalid arguments for len function")
        right_value: int | float = raw

        if op_symbol == ">":
            threshold = right_value + 1
        elif op_symbol == ">=":
            threshold = right_value
        elif op_symbol == "<":
            threshold = right_value - 1
        elif op_symbol == "<=":
            threshold = right_value
        else:
            threshold = right_value

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
        """
        Compute the sum of an iterable and compare it to the threshold.

        Parameters:
            arg_node (ast.expr): AST node representing the iterable passed to `sum`.
            right_node (ast.expr): Node describing the threshold value.
            op_symbol (str): Operator symbol describing the comparison.
            var_values (dict[str, object | None]): Evaluated variable values.
            rule (Rule): Calling rule used for helper evaluations.

        Returns:
            dict[str, str]: Mapping containing a single error message keyed by variable name.

        Raises:
            ValueError: If the argument is not a numeric iterable or the threshold is invalid.
        """

        # Name und Wert holen
        var_name = rule._get_node_name(arg_node)
        raw_iter = var_values.get(var_name)
        if not isinstance(raw_iter, (list, tuple)):
            raise ValueError("sum expects an iterable of numbers")
        if not all(isinstance(x, (int, float)) for x in raw_iter):
            raise ValueError("sum expects an iterable of numbers")
        total = sum(raw_iter)

        # Schwellenwert aus dem rechten Knoten
        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise ValueError("Invalid arguments for sum function")
        right_value = raw

        # Message formulieren
        if op_symbol in (">", ">="):
            msg = (
                f"[{var_name}] (sum={total}) is too small ({op_symbol} {right_value})!"
            )
        elif op_symbol in ("<", "<="):
            msg = (
                f"[{var_name}] (sum={total}) is too large ({op_symbol} {right_value})!"
            )
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
        Compare the maximum element of an iterable against the provided threshold.

        Parameters:
            arg_node (ast.expr): AST node representing the iterable passed to `max`.
            right_node (ast.expr): Node describing the threshold value.
            op_symbol (str): Operator symbol describing the comparison.
            var_values (dict[str, object | None]): Evaluated variable values.
            rule (Rule): Calling rule used for helper evaluations.

        Returns:
            dict[str, str]: Mapping containing a single error message keyed by variable name.

        Raises:
            ValueError: If the iterable is empty, non-numeric, or the threshold is invalid.
        """

        var_name = rule._get_node_name(arg_node)
        raw_iter = var_values.get(var_name)
        if not isinstance(raw_iter, (list, tuple)) or len(raw_iter) == 0:
            raise ValueError("max expects a non-empty iterable")
        if not all(isinstance(x, (int, float)) for x in raw_iter):
            raise ValueError("max expects an iterable of numbers")
        current = max(raw_iter)

        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise ValueError("Invalid arguments for max function")
        right_value = raw

        if op_symbol in (">", ">="):
            msg = f"[{var_name}] (max={current}) is too small ({op_symbol} {right_value})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] (max={current}) is too large ({op_symbol} {right_value})!"
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
        Compare the minimum element of an iterable against the provided threshold.

        Parameters:
            arg_node (ast.expr): AST node representing the iterable passed to `min`.
            right_node (ast.expr): Node describing the threshold value.
            op_symbol (str): Operator symbol describing the comparison.
            var_values (dict[str, object | None]): Evaluated variable values.
            rule (Rule): Calling rule used for helper evaluations.

        Returns:
            dict[str, str]: Mapping containing a single error message keyed by variable name.

        Raises:
            ValueError: If the iterable is empty, non-numeric, or the threshold is invalid.
        """

        var_name = rule._get_node_name(arg_node)
        raw_iter = var_values.get(var_name)
        if not isinstance(raw_iter, (list, tuple)) or len(raw_iter) == 0:
            raise ValueError("min expects a non-empty iterable")
        if not all(isinstance(x, (int, float)) for x in raw_iter):
            raise ValueError("min expects an iterable of numbers")
        current = min(raw_iter)

        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise ValueError("Invalid arguments for min function")
        right_value = raw

        if op_symbol in (">", ">="):
            msg = f"[{var_name}] (min={current}) is too small ({op_symbol} {right_value})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] (min={current}) is too large ({op_symbol} {right_value})!"
        else:
            msg = f"[{var_name}] (min={current}) must be {right_value}!"

        return {var_name: msg}
