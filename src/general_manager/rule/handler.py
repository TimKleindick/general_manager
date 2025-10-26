"""Rule handler implementations that craft error messages from AST nodes."""

from __future__ import annotations
import ast
from typing import Dict, Optional, TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from general_manager.rule.rule import Rule


class InvalidFunctionNodeError(ValueError):
    """Raised when a rule handler receives an invalid AST node for its function."""

    def __init__(self, function_name: str) -> None:
        """
        Initialize the exception for an invalid left-hand AST node used with a function call.

        Parameters:
            function_name (str): Name of the function with the invalid left node; stored on the exception and used to form the message "Invalid left node for {function_name}() function."
        """
        self.function_name = function_name
        super().__init__(f"Invalid left node for {function_name}() function.")


class InvalidLenThresholdError(TypeError):
    """Raised when len() comparisons use a non-numeric threshold."""

    def __init__(self) -> None:
        """
        Exception raised when a len() threshold is not a numeric value.

        Initializes the exception with a default message indicating invalid arguments for the len function.
        """
        super().__init__("Invalid arguments for len function.")


class InvalidNumericThresholdError(TypeError):
    """Raised when aggregate handlers use a non-numeric threshold."""

    def __init__(self, function_name: str) -> None:
        """
        Create an InvalidFunctionNodeError with a formatted message for the given function name.

        Parameters:
            function_name (str): Name of the aggregate function (e.g., "sum", "max", "min") included in the message.
        """
        super().__init__(f"Invalid arguments for {function_name} function.")


class NonEmptyIterableError(ValueError):
    """Raised when an aggregate function expects a non-empty iterable."""

    def __init__(self, function_name: str) -> None:
        """
        Initialize the error indicating an aggregate function received an empty iterable.

        Parameters:
            function_name (str): Name of the aggregate function (e.g., 'sum', 'max', 'len') used to build the error message.
        """
        super().__init__(f"{function_name} expects a non-empty iterable.")


class NumericIterableError(TypeError):
    """Raised when an aggregate function expects numeric elements."""

    def __init__(self, function_name: str) -> None:
        """
        Initialize the exception indicating that a function expected an iterable of numeric values.

        Parameters:
            function_name (str): Name of the function included in the exception message (message: "<function_name> expects an iterable of numbers.").
        """
        super().__init__(f"{function_name} expects an iterable of numbers.")


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
        """
        Handle a comparison AST node whose left side is a function call and delegate analysis to the subclass aggregate method.

        Parameters:
            node (ast.AST): The AST node to inspect; processing only occurs if it is an `ast.Compare`.
            left (Optional[ast.expr]): Original left operand from the rule (may be unused by this handler).
            right (Optional[ast.expr]): Original right operand from the rule (may be unused by this handler).
            op (Optional[ast.cmpop]): Comparison operator from the rule; used to determine operator symbol.
            var_values (Dict[str, Optional[object]]): Mapping of variable names to their resolved values for message construction.
            rule (Rule): Rule instance used to obtain the textual operator symbol and any rule-specific context.

        Returns:
            Dict[str, str]: Mapping from variable name to generated error message; empty if `node` is not an `ast.Compare`.

        Raises:
            InvalidFunctionNodeError: If the compare left-hand side is not a function call with at least one argument.
        """
        if not isinstance(node, ast.Compare):
            return {}
        compare_node = node

        left_node = compare_node.left
        right_node = compare_node.comparators[0]
        op_symbol = rule._get_op_symbol(op)

        if not (isinstance(left_node, ast.Call) and left_node.args):
            raise InvalidFunctionNodeError(self.function_name)
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
        Produce an error message for a len() comparison against a numeric threshold.

        Parameters:
            arg_node (ast.expr): AST node representing the value passed to `len`.
            right_node (ast.expr): AST node representing the comparison threshold.
            op_symbol (str): Comparison operator symbol (e.g., ">", ">=", "<", "<=").
            var_values (Dict[str, Optional[object]]): Runtime values for variables keyed by name.
            rule (Rule): Rule helper used to resolve node names and evaluate nodes.

        Returns:
            Dict[str, str]: A single-entry mapping from the variable name to a human-readable violation message.

        Raises:
            InvalidLenThresholdError: If the comparison threshold cannot be interpreted as a number.
        """

        var_name = rule._get_node_name(arg_node)
        var_value = var_values.get(var_name)

        # --- Type guard for right_value ---
        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise InvalidLenThresholdError()
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

        # Formulate the error message
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
        Evaluate the sum of the iterable referenced by `arg_node` and produce a descriptive error message when the sum does not satisfy the comparison against the provided threshold.

        Parameters:
            arg_node (ast.expr): AST node identifying the iterable variable whose elements will be summed.
            right_node (ast.expr): AST node representing the threshold value to compare against.
            op_symbol (str): Comparison operator symbol (e.g., ">", "<=", "==") used to form the message.
            var_values (Dict[str, Optional[object]]): Mapping of variable names to their evaluated runtime values.
            rule (Rule): Rule helper used to resolve node names and evaluate AST nodes.

        Returns:
            Dict[str, str]: A mapping containing a single entry: the variable name mapped to a human-readable message
            describing how the computed sum relates to the threshold (too small, too large, or must equal).

        Raises:
            NonEmptyIterableError: If the referenced iterable is missing or empty.
            NumericIterableError: If the iterable contains non-numeric elements.
            InvalidNumericThresholdError: If the threshold evaluated from `right_node` is not numeric.
        """

        # Name und Wert holen
        var_name = rule._get_node_name(arg_node)
        raw_iter = var_values.get(var_name)
        if not isinstance(raw_iter, (list, tuple)) or len(raw_iter) == 0:
            raise NonEmptyIterableError("sum")
        if not all(isinstance(x, (int, float)) for x in raw_iter):
            raise NumericIterableError("sum")
        total = sum(raw_iter)

        # Schwellenwert aus dem rechten Knoten
        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise InvalidNumericThresholdError("sum")
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
        Compare the maximum element of an iterable variable against a numeric threshold and produce an error message when the comparison fails.

        Parameters:
            arg_node (ast.expr): AST node identifying the iterable variable passed to `max`.
            right_node (ast.expr): AST node that evaluates to the numeric threshold to compare against.
            op_symbol (str): Comparison operator symbol (e.g., ">", ">=", "<", "<=", "==") used to shape the message.
            var_values (Dict[str, Optional[object]]): Mapping of variable names to their evaluated runtime values.
            rule (Rule): Rule helper used to resolve node names and evaluate `right_node`.

        Returns:
            Dict[str, str]: Single-item mapping from the variable name to a human-readable message describing the max value and the threshold comparison.

        Raises:
            NonEmptyIterableError: If the resolved iterable is not a non-empty list or tuple.
            NumericIterableError: If any element of the iterable is not an int or float.
            InvalidNumericThresholdError: If the evaluated threshold is not an int or float.
        """

        var_name = rule._get_node_name(arg_node)
        raw_iter = var_values.get(var_name)
        if not isinstance(raw_iter, (list, tuple)) or len(raw_iter) == 0:
            raise NonEmptyIterableError("max")
        if not all(isinstance(x, (int, float)) for x in raw_iter):
            raise NumericIterableError("max")
        current = max(raw_iter)

        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise InvalidNumericThresholdError("max")
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
        Compare the minimum element of an iterable against a numeric threshold and produce an error message describing the violation.

        Parameters:
            arg_node (ast.expr): AST node for the iterable argument passed to `min`.
            right_node (ast.expr): AST node for the threshold value to compare against.
            op_symbol (str): Comparison operator symbol (e.g., ">", ">=", "<", "<=", "==").
            var_values (Dict[str, Optional[object]]): Mapping of variable names to their evaluated values.
            rule (Rule): Rule instance used to evaluate AST nodes and obtain variable names.

        Returns:
            dict[str, str]: Mapping with the variable name as key and the generated error message as value.

        Raises:
            NonEmptyIterableError: If the iterable is not a non-empty list/tuple.
            NumericIterableError: If the iterable contains non-numeric elements.
            InvalidNumericThresholdError: If the evaluated threshold is not numeric.
        """

        var_name = rule._get_node_name(arg_node)
        raw_iter = var_values.get(var_name)
        if not isinstance(raw_iter, (list, tuple)) or len(raw_iter) == 0:
            raise NonEmptyIterableError("min")
        if not all(isinstance(x, (int, float)) for x in raw_iter):
            raise NumericIterableError("min")
        current = min(raw_iter)

        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise InvalidNumericThresholdError("min")
        right_value = raw

        if op_symbol in (">", ">="):
            msg = f"[{var_name}] (min={current}) is too small ({op_symbol} {right_value})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] (min={current}) is too large ({op_symbol} {right_value})!"
        else:
            msg = f"[{var_name}] (min={current}) must be {right_value}!"

        return {var_name: msg}
