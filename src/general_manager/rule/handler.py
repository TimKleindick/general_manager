"""Rule handler implementations that craft error messages from AST nodes."""

from __future__ import annotations
import ast
from typing import ClassVar, Protocol, TypeAlias, TypeGuard
from abc import ABC, abstractmethod

NumericValue: TypeAlias = int | float
RuleValueMap: TypeAlias = dict[str, object | None]
RuleErrorMap: TypeAlias = dict[str, str]


class RuleHandlerContext(Protocol):
    """Methods from Rule required by handler implementations."""

    def _get_op_symbol(self, op: ast.cmpop | None) -> str: ...

    def _get_node_name(self, node: ast.AST) -> str: ...

    def _eval_node(self, node: ast.expr) -> object | None: ...


def _is_numeric_value(value: object) -> TypeGuard[NumericValue]:
    """Return True for supported rule-handler numeric values, excluding bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric_threshold(
    rule: RuleHandlerContext, node: ast.expr, function_name: str
) -> NumericValue:
    """Evaluate a threshold node and ensure it is a supported numeric value."""
    raw = rule._eval_node(node)
    if not _is_numeric_value(raw):
        if function_name == "len":
            raise InvalidLenThresholdError()
        raise InvalidNumericThresholdError(function_name)
    return raw


def _numeric_iterable(
    raw_iter: object,
    function_name: str,
) -> list[NumericValue]:
    """Validate and return a non-empty list of numeric aggregate values."""
    if not isinstance(raw_iter, (list, tuple)) or len(raw_iter) == 0:
        raise NonEmptyIterableError(function_name)
    values: list[NumericValue] = []
    for item in raw_iter:
        if not _is_numeric_value(item):
            raise NumericIterableError(function_name)
        values.append(item)
    return values


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
    """
    Define the protocol for generating rule-specific error messages.

    Rule handlers are used after a predicate has already failed. They explain a
    comparison by returning messages keyed by variable name; they do not decide
    whether a rule passes. Subclasses must set `function_name`, which is the
    dispatch key used by `Rule` and `RULE_HANDLERS` registration.
    """

    function_name: ClassVar[str]

    @abstractmethod
    def handle(
        self,
        node: ast.AST,
        left: ast.expr | None,
        right: ast.expr | None,
        op: ast.cmpop | None,
        var_values: RuleValueMap,
        rule: RuleHandlerContext,
    ) -> RuleErrorMap:
        """
        Produce error messages for a comparison or function call node.

        Parameters:
            node (ast.AST): AST node representing the expression being evaluated.
            left (ast.expr | None): Left operand when applicable.
            right (ast.expr | None): Right operand when applicable.
            op (ast.cmpop | None): Comparison operator node.
            var_values (dict[str, object | None]): Resolved variable values used during evaluation.
            rule (RuleHandlerContext): Rule-like object invoking the handler.

        Returns:
            dict[str, str]: Mapping of variable names to error messages. Return
                an empty mapping when the handler cannot explain the node.
        """
        pass


class FunctionHandler(BaseRuleHandler, ABC):
    """
    Base class for handlers that evaluate function-call expressions such as len(), max(), or sum().
    """

    def handle(
        self,
        node: ast.AST,
        left: ast.expr | None,
        right: ast.expr | None,
        op: ast.cmpop | None,
        var_values: RuleValueMap,
        rule: RuleHandlerContext,
    ) -> RuleErrorMap:
        """
        Handle a comparison AST node and delegate analysis to the subclass aggregate method.

        The caller supplies the comparison leg to explain through `left`, `right`,
        and `op`. For a chained comparison such as `1 < len(x) < 5`, pass
        `left=compare.comparators[0]`, `right=compare.comparators[1]`, and
        `op=compare.ops[1]` to explain the second leg. If `left` or `right` is
        omitted, this falls back to the comparison node's left side and first
        comparator. Function calls must be on the selected left side; callers
        that want to explain `5 > len(x)` must normalize the comparison before
        invoking this handler. Calls to another function return `{}` because the
        node is outside this handler's responsibility. A selected left side that
        is not a call is malformed for a function handler and raises
        `InvalidFunctionNodeError`. Matching function calls must have exactly
        one positional argument and no keyword arguments.
        Exceptions raised by `rule` helper methods propagate unchanged.

        Parameters:
            node (ast.AST): The AST node to inspect; processing only occurs if it is an `ast.Compare`.
            left (Optional[ast.expr]): Original left operand from the rule (may be unused by this handler).
            right (Optional[ast.expr]): Original right operand from the rule (may be unused by this handler).
            op (Optional[ast.cmpop]): Comparison operator from the rule; used to determine operator symbol.
            var_values (Dict[str, Optional[object]]): Mapping of variable names to their resolved values for message construction.
            rule (RuleHandlerContext): Rule-like helper used to obtain the textual operator symbol and any rule-specific context.

        Returns:
            Dict[str, str]: Mapping from variable name to generated error message; empty if `node` is not an `ast.Compare`.

        Raises:
            InvalidFunctionNodeError: If the selected left-hand side is not a
                call, or if a matching call is not exactly one positional
                argument with no keyword arguments.
        """
        if not isinstance(node, ast.Compare):
            return {}
        compare_node = node

        left_node = left if left is not None else compare_node.left
        right_node = right if right is not None else compare_node.comparators[0]
        op_symbol = rule._get_op_symbol(op)

        if not isinstance(left_node, ast.Call):
            raise InvalidFunctionNodeError(self.function_name)

        function_name = rule._get_node_name(left_node.func)
        if function_name != self.function_name:
            return {}

        if len(left_node.args) != 1 or left_node.keywords:
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
        var_values: RuleValueMap,
        rule: RuleHandlerContext,
    ) -> RuleErrorMap:
        """
        Analyse the call arguments and construct an error message payload.

        Parameters:
            arg_node (ast.expr): AST node representing the function argument.
            right_node (ast.expr): Node representing the comparison threshold.
            op_symbol (str): Symbolic representation of the comparison operator.
            var_values (dict[str, object | None]): Resolved values used during evaluation.
            rule (RuleHandlerContext): Rule-like helper requesting the aggregation.

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
        var_values: RuleValueMap,
        rule: RuleHandlerContext,
    ) -> RuleErrorMap:
        """
        Produce an error message for a len() comparison against a numeric threshold.

        This handler does not call `len()` on the runtime value; it uses the
        resolved value only for display. Thresholds must be `int` or `float` and
        `bool` is rejected even though it is an `int` subclass. Missing
        `var_values` entries and None values are displayed as None.

        Parameters:
            arg_node (ast.expr): AST node representing the value passed to `len`.
            right_node (ast.expr): AST node representing the comparison threshold.
            op_symbol (str): Comparison operator symbol (e.g., ">", ">=", "<", "<=").
            var_values (Dict[str, Optional[object]]): Runtime values for variables keyed by name.
            rule (Rule): Rule helper used to resolve node names and evaluate nodes.

        Returns:
            Dict[str, str]: A single-entry mapping from the variable name to a human-readable violation message.

        Raises:
            InvalidLenThresholdError: If the comparison threshold is not `int`
                or `float`, or is `bool`.
        """

        var_name = rule._get_node_name(arg_node)
        var_value = var_values.get(var_name)

        right_value = _numeric_threshold(rule, right_node, "len")

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
        elif op_symbol == "!=":
            msg = f"[{var_name}] ({var_value}) must not have a length of {right_value}!"
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
        var_values: RuleValueMap,
        rule: RuleHandlerContext,
    ) -> RuleErrorMap:
        """
        Evaluate the sum of the iterable referenced by `arg_node` and produce a descriptive error message when the sum does not satisfy the comparison against the provided threshold.

        The argument name comes from `rule._get_node_name(arg_node)` and the
        iterable is looked up directly in `var_values` under that name. The
        resolved value must be a non-empty `list` or `tuple` of `int` or
        `float` values. `bool`, strings, sets, generators, missing variables,
        and `None` are rejected. The threshold must also be `int` or `float`,
        excluding `bool`.

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
            NonEmptyIterableError: If the referenced value is missing, `None`,
                not a list/tuple, or empty.
            NumericIterableError: If the iterable contains non-numeric elements
                or bools.
            InvalidNumericThresholdError: If the threshold evaluated from `right_node` is not numeric or is bool.
        """

        var_name = rule._get_node_name(arg_node)
        values = _numeric_iterable(var_values.get(var_name), "sum")
        total = sum(values)

        right_value = _numeric_threshold(rule, right_node, "sum")

        if op_symbol in (">", ">="):
            msg = (
                f"[{var_name}] (sum={total}) is too small ({op_symbol} {right_value})!"
            )
        elif op_symbol in ("<", "<="):
            msg = (
                f"[{var_name}] (sum={total}) is too large ({op_symbol} {right_value})!"
            )
        elif op_symbol == "!=":
            msg = f"[{var_name}] (sum={total}) must not be {right_value}!"
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
        var_values: RuleValueMap,
        rule: RuleHandlerContext,
    ) -> RuleErrorMap:
        """
        Compare the maximum element of an iterable variable against a numeric threshold and produce an error message when the comparison fails.

        Accepted iterable and threshold types match `SumHandler`.

        Parameters:
            arg_node (ast.expr): AST node identifying the iterable variable passed to `max`.
            right_node (ast.expr): AST node that evaluates to the numeric threshold to compare against.
            op_symbol (str): Comparison operator symbol (e.g., ">", ">=", "<", "<=", "==") used to shape the message.
            var_values (Dict[str, Optional[object]]): Mapping of variable names to their evaluated runtime values.
            rule (Rule): Rule helper used to resolve node names and evaluate `right_node`.

        Returns:
            Dict[str, str]: Single-item mapping from the variable name to a human-readable message describing the max value and the threshold comparison.

        Raises:
            NonEmptyIterableError: If the referenced value is missing, `None`,
                not a list/tuple, or empty.
            NumericIterableError: If any element of the iterable is not an int or float, or is bool.
            InvalidNumericThresholdError: If the evaluated threshold is not an int or float, or is bool.
        """

        var_name = rule._get_node_name(arg_node)
        values = _numeric_iterable(var_values.get(var_name), "max")
        current = max(values)

        right_value = _numeric_threshold(rule, right_node, "max")

        if op_symbol in (">", ">="):
            msg = f"[{var_name}] (max={current}) is too small ({op_symbol} {right_value})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] (max={current}) is too large ({op_symbol} {right_value})!"
        elif op_symbol == "!=":
            msg = f"[{var_name}] (max={current}) must not be {right_value}!"
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
        var_values: RuleValueMap,
        rule: RuleHandlerContext,
    ) -> RuleErrorMap:
        """
        Compare the minimum element of an iterable against a numeric threshold and produce an error message describing the violation.

        Accepted iterable and threshold types match `SumHandler`.

        Parameters:
            arg_node (ast.expr): AST node for the iterable argument passed to `min`.
            right_node (ast.expr): AST node for the threshold value to compare against.
            op_symbol (str): Comparison operator symbol (e.g., ">", ">=", "<", "<=", "==").
            var_values (Dict[str, Optional[object]]): Mapping of variable names to their evaluated values.
            rule (Rule): Rule instance used to evaluate AST nodes and obtain variable names.

        Returns:
            dict[str, str]: Mapping with the variable name as key and the generated error message as value.

        Raises:
            NonEmptyIterableError: If the referenced value is missing, `None`,
                not a list/tuple, or empty.
            NumericIterableError: If the iterable contains non-numeric elements or bools.
            InvalidNumericThresholdError: If the evaluated threshold is not numeric or is bool.
        """

        var_name = rule._get_node_name(arg_node)
        values = _numeric_iterable(var_values.get(var_name), "min")
        current = min(values)

        right_value = _numeric_threshold(rule, right_node, "min")

        if op_symbol in (">", ">="):
            msg = f"[{var_name}] (min={current}) is too small ({op_symbol} {right_value})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] (min={current}) is too large ({op_symbol} {right_value})!"
        elif op_symbol == "!=":
            msg = f"[{var_name}] (min={current}) must not be {right_value}!"
        else:
            msg = f"[{var_name}] (min={current}) must be {right_value}!"

        return {var_name: msg}
