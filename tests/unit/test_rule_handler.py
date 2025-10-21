import ast
from typing import Any, Optional

import pytest

from general_manager.rule.handler import LenHandler, MaxHandler, MinHandler, SumHandler


class UnexpectedNodeTypeError(ValueError):
    """Raised when a rule handler encounters an unexpected AST node type."""

    def __init__(self) -> None:
        """
        Initialize the exception indicating an unexpected AST node type was encountered.
        The exception is constructed with the message "Unexpected node type."
        """
        super().__init__("Unexpected node type.")


class UnsupportedNodeEvaluationError(ValueError):
    """Raised when a rule handler cannot evaluate a particular AST node."""

    def __init__(self, node_type: str) -> None:
        """
        Initialize the exception indicating a rule handler cannot evaluate an AST node of the given type.
        Parameters:
            node_type (str): The name of the AST node type that cannot be evaluated; this value is included in the exception message.
        """
        super().__init__(f"Cannot eval node of type {node_type}.")


class DummyRule:
    def __init__(self, op_symbol: str):
        """
        Initialize the DummyRule with a fixed operator symbol.
        Parameters:
            op_symbol (str): Operator symbol to store and return for this rule instance.
        """
        self._op_symbol = op_symbol

    def _get_op_symbol(self, op: Optional[ast.cmpop]) -> str:
        return self._op_symbol

    def _get_node_name(self, node: ast.AST) -> str:
        """
        Get the identifier of an AST Name node.

        Parameters:
            node (ast.AST): The AST node expected to be an `ast.Name`.

        Returns:
            str: The `id` (identifier) of the `ast.Name` node.

        Raises:
            UnexpectedNodeTypeError: If `node` is not an `ast.Name`.
        """
        if isinstance(node, ast.Name):
            return node.id
        raise UnexpectedNodeTypeError()

    def _eval_node(self, node: ast.AST) -> Any:
        # 1) Direktes Literal
        """
        Evaluate a simple AST node and return its corresponding Python value.

        Supports literal constants, negative numeric unary operations, and name nodes.
        Parameters:
            node (ast.AST): The AST node to evaluate.

        Returns:
            The evaluated value: the literal for `ast.Constant`, the negated number for a numeric `ast.UnaryOp` with `ast.USub`, or `None` for `ast.Name`.

        Raises:
            UnsupportedNodeEvaluationError: If the node type is not supported.
        """
        if isinstance(node, ast.Constant):
            return node.value
        # 2) Negativer Literal-Fall: -2, -3.5, …
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            val = self._eval_node(node.operand)
            if isinstance(val, (int, float)):
                return -val
        # 3) Name-Knoten für var_values lookup
        if isinstance(node, ast.Name):
            return None
        raise UnsupportedNodeEvaluationError(type(node).__name__)


# Handler-Instanz
len_handler = LenHandler()
sum_handler = SumHandler()
max_handler = MaxHandler()
min_handler = MinHandler()


@pytest.mark.parametrize(
    "expr, op_symbol, var_values, expected",
    [
        # Fall: len(x) > 3  und x="abc"  → len("abc")==3 → 3 ist nicht >3 → Meldung "too short"
        (
            "len(x) > 3",
            ">",
            {"x": "abc"},
            {"x": "[x] (abc) is too short (min length 4)!"},
        ),
        # len(y) >= 2  und y="ab" → 2 >=2 → Meldung "too short" mit threshold==2
        (
            "len(y) >= 2",
            ">=",
            {"y": "ab"},
            {"y": "[y] (ab) is too short (min length 2)!"},
        ),
        # len(z) < 5   und z="abcdef" (len=6) → 6<5 ist False → "too long"
        (
            "len(z) < 5",
            "<",
            {"z": "abcdef"},
            {"z": "[z] (abcdef) is too long (max length 4)!"},
        ),
        # len(w) <= 1  und w="" (len=0) → 0<=1 → ok, aber Meldung
        ("len(w) <= 1", "<=", {"w": ""}, {"w": "[w] () is too long (max length 1)!"}),
        # len(a) == 4  und a="abcd" → 4==4 → ok, aber Meldung
        (
            "len(a) == 4",
            "==",
            {"a": "abc"},
            {"a": "[a] (abc) must have a length of 4!"},
        ),
    ],
)
def test_len_handler_success(expr, op_symbol, var_values, expected):
    # AST erzeugen
    node = ast.parse(expr, mode="eval").body
    # DummyRule mit vorgegebener Op-Symbol-Antwort
    rule = DummyRule(op_symbol)
    result = len_handler.handle(
        node,
        node.left,
        node.comparators[0],
        node.ops[0],
        var_values,
        rule,  # type: ignore
    )
    assert result == expected


def test_non_compare_node_returns_empty():
    # z. B. ein Call-Knoten statt Compare
    node = ast.parse("print('hallo')", mode="eval").body
    rule = DummyRule(">")
    assert len_handler.handle(node, None, None, None, {}, rule) == {}  # type: ignore


@pytest.mark.parametrize(
    "bad_expr, op",
    [
        ("len(x) > 'z'", ">"),  # right kein Zahl-Literal
        ("x + 1 > 2", ">"),  # left kein Call
    ],
)
def test_len_handler_invalid_raises1(bad_expr, op):
    node = ast.parse(bad_expr, mode="eval").body
    rule = DummyRule(op)
    with pytest.raises((TypeError, ValueError)):
        len_handler.handle(
            node,
            node.left,  # type: ignore
            getattr(node, "comparators", [None])[0],
            node.ops[0] if hasattr(node, "ops") else None,  # type: ignore
            {"x": "hi"},
            rule,  # type: ignore
        )


@pytest.mark.parametrize(
    "expr, op_symbol, var_values, expected",
    [
        (
            "sum(x) > 3",
            ">",
            {"x": [1, 1]},
            {"x": "[x] (sum=2) is too small (> 3)!"},
        ),
        (
            "sum(y) >= 4",
            ">=",
            {"y": [1, 2]},
            {"y": "[y] (sum=3) is too small (>= 4)!"},
        ),
        (
            "sum(z) < 5",
            "<",
            {"z": [2, 6]},
            {"z": "[z] (sum=8) is too large (< 5)!"},
        ),
        (
            "sum(w) <= 3",
            "<=",
            {"w": [1, 4]},
            {"w": "[w] (sum=5) is too large (<= 3)!"},
        ),
        (
            "sum(a) == 4",
            "==",
            {"a": [1, 2, 2]},
            {"a": "[a] (sum=5) must be 4!"},
        ),
    ],
)
def test_sum_handler_success(expr, op_symbol, var_values, expected):
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule(op_symbol)
    result = sum_handler.handle(
        node,
        node.left,
        node.comparators[0],
        node.ops[0],
        var_values,
        rule,  # type: ignore
    )
    assert result == expected


# Fehler-Fälle für SumHandler
def test_sum_handler_non_compare_returns_empty():
    node = ast.parse("print(1)", mode="eval").body
    rule = DummyRule(">")
    assert sum_handler.handle(node, None, None, None, {}, rule) == {}  # type: ignore


@pytest.mark.parametrize(
    "expr, var_values, error_msg",
    [
        ("x + 1 > 2", {"x": [1]}, "Invalid left node for sum() function"),
        ("sum(x) > 3", {"x": None}, "sum expects a non-empty iterable"),
        ("sum(x) > 'a'", {"x": [1, 2]}, "Invalid arguments for sum function"),
    ],
)
def test_sum_handler_invalid(expr, var_values, error_msg):
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule(">")
    with pytest.raises((ValueError, TypeError)) as excinfo:
        sum_handler.handle(
            node,
            node.left,  # type: ignore
            getattr(node, "comparators", [None])[0],
            node.ops[0],  # type: ignore
            var_values,
            rule,  # type: ignore
        )
    assert error_msg in str(excinfo.value)


@pytest.mark.parametrize(
    "expr, op_symbol, var_values, expected",
    [
        (
            "max(x) > 3",
            ">",
            {"x": [1, 2]},
            {"x": "[x] (max=2) is too small (> 3)!"},
        ),
        (
            "max(y) >= 2",
            ">=",
            {"y": [1, 1]},
            {"y": "[y] (max=1) is too small (>= 2)!"},
        ),
        (
            "max(z) < 5",
            "<",
            {"z": [6, 7]},
            {"z": "[z] (max=7) is too large (< 5)!"},
        ),
        (
            "max(w) <= 3",
            "<=",
            {"w": [1, 4]},
            {"w": "[w] (max=4) is too large (<= 3)!"},
        ),
        (
            "max(a) == 4",
            "==",
            {"a": [1, 2, 1]},
            {"a": "[a] (max=2) must be 4!"},
        ),
    ],
)
def test_max_handler_success(expr, op_symbol, var_values, expected):
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule(op_symbol)
    result = max_handler.handle(
        node,
        node.left,
        node.comparators[0],
        node.ops[0],
        var_values,
        rule,  # type: ignore
    )
    assert result == expected


def test_max_handler_non_compare_returns_empty():
    node = ast.parse("42", mode="eval").body
    rule = DummyRule("<")
    assert max_handler.handle(node, None, None, None, {}, rule) == {}  # type: ignore


@pytest.mark.parametrize(
    "expr, var_values, error_msg",
    [
        ("x + 1 < 5", {"x": [1]}, "Invalid left node for max() function"),
        ("max(x) > 3", {"x": []}, "max expects a non-empty iterable"),
        ("max(x) > 'a'", {"x": [1, 2]}, "Invalid arguments for max function"),
    ],
)
def test_max_handler_invalid(expr, var_values, error_msg):
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule(">")
    with pytest.raises((TypeError, ValueError)) as excinfo:
        max_handler.handle(
            node,
            node.left,  # type: ignore
            getattr(node, "comparators", [None])[0],
            node.ops[0],  # type: ignore
            var_values,
            rule,  # type: ignore
        )
    assert error_msg in str(excinfo.value)


@pytest.mark.parametrize(
    "expr, op_symbol, var_values, expected",
    [
        (
            "min(x) > 3",
            ">",
            {"x": [0.1234]},
            {"x": "[x] (min=0.1234) is too small (> 3)!"},
        ),
        (
            "min(y) >= 2",
            ">=",
            {"y": [1, 3]},
            {"y": "[y] (min=1) is too small (>= 2)!"},
        ),
        (
            "min(z) < 5",
            "<",
            {"z": [6, 7, 8]},
            {"z": "[z] (min=6) is too large (< 5)!"},
        ),
        (
            "min(w) <= 2",
            "<=",
            {"w": [3]},
            {"w": "[w] (min=3) is too large (<= 2)!"},
        ),
        (
            "min(a) == 4",
            "==",
            {"a": [1, 2, 1]},
            {"a": "[a] (min=1) must be 4!"},
        ),
    ],
)
def test_min_handler_success(expr, op_symbol, var_values, expected):
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule(op_symbol)
    result = min_handler.handle(
        node,
        node.left,
        node.comparators[0],
        node.ops[0],
        var_values,
        rule,  # type: ignore
    )
    assert result == expected


def test_min_handler_non_compare_returns_empty():
    node = ast.parse("'foo'", mode="eval").body
    rule = DummyRule(">=")
    assert min_handler.handle(node, None, None, None, {}, rule) == {}  # type: ignore


@pytest.mark.parametrize(
    "expr, var_values, error_msg",
    [
        ("1 + x >= 2", {"x": [1]}, "Invalid left node for min() function"),
        ("min(x) < 5", {"x": []}, "min expects a non-empty iterable"),
        ("min(x) > 'a'", {"x": [1]}, "Invalid arguments for min function"),
    ],
)
def test_min_handler_invalid(expr, var_values, error_msg):
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule("<")
    with pytest.raises((ValueError, TypeError)) as excinfo:
        min_handler.handle(
            node,
            node.left,  # type: ignore
            getattr(node, "comparators", [None])[0],
            node.ops[0],  # type: ignore
            var_values,
            rule,  # type: ignore
        )
    assert error_msg in str(excinfo.value)


@pytest.mark.parametrize(
    "expr, op_symbol, var_values, expected",
    [
        (
            "sum(mixed) < 7",
            "<",
            {"mixed": [1, 2.5, 4]},
            {"mixed": "[mixed] (sum=7.5) is too large (< 7)!"},
        ),
        (
            "max(mixed) > 2.5",
            ">",
            {"mixed": [1, 2.5, 3]},
            {"mixed": "[mixed] (max=3) is too small (> 2.5)!"},
        ),
        (
            "min(mixed) <= -2",
            "<=",
            {"mixed": [-1.5, 2, 3]},
            {"mixed": "[mixed] (min=-1.5) is too large (<= -2)!"},
        ),
    ],
)
def test_mixed_numeric_types(expr, op_symbol, var_values, expected):
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule(op_symbol)
    # dispatch auf den richtigen Handler
    if expr.startswith("sum"):
        result = sum_handler.handle(
            node,
            node.left,
            node.comparators[0],
            node.ops[0],
            var_values,
            rule,  # type: ignore
        )
    elif expr.startswith("max"):
        result = max_handler.handle(
            node,
            node.left,
            node.comparators[0],
            node.ops[0],
            var_values,
            rule,  # type: ignore
        )
    else:
        result = min_handler.handle(
            node,
            node.left,
            node.comparators[0],
            node.ops[0],
            var_values,
            rule,  # type: ignore
        )
    assert result == expected


# --- Very large collections (funktionale Korrektheit, kein Benchmark) ---
def test_sum_handler_large_collection():
    n = 100_000
    large = [1] * n
    expr = f"sum(large) >= {n}"
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule(">=")
    result = sum_handler.handle(
        node,
        node.left,
        node.comparators[0],
        node.ops[0],
        {"large": large},
        rule,  # type: ignore
    )
    assert result == {"large": f"[large] (sum={n}) is too small (>= {n})!"}


def test_max_handler_large_collection():
    n = 100_000
    large = list(range(n))
    expr = f"max(large) == {n - 1}"
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule("==")
    result = max_handler.handle(
        node,
        node.left,
        node.comparators[0],
        node.ops[0],
        {"large": large},
        rule,  # type: ignore
    )
    assert result == {"large": f"[large] (max={n - 1}) must be {n - 1}!"}


def test_min_handler_large_collection():
    n = 100_000
    large = list(range(n))
    expr = "min(large) == 0"
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule("==")
    result = min_handler.handle(
        node,
        node.left,
        node.comparators[0],
        node.ops[0],
        {"large": large},
        rule,  # type: ignore
    )
    assert result == {"large": "[large] (min=0) must be 0!"}


# --- Edge case: var_values liefert None ---
@pytest.mark.parametrize(
    "handler, expr, error_msg",
    [
        (sum_handler, "sum(x) > 0", "sum expects a non-empty iterable"),
        (max_handler, "max(x) > 0", "max expects a non-empty iterable"),
        (min_handler, "min(x) > 0", "min expects a non-empty iterable"),
    ],
)
def test_handler_none_value_raises(handler, expr, error_msg):
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule(">")
    with pytest.raises(ValueError) as excinfo:
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": None},
            rule,
        )
    assert error_msg in str(excinfo.value)


# --- Tests for new custom exception classes ---
def test_invalid_function_node_error():
    """Test that InvalidFunctionNodeError is raised for invalid AST nodes."""
    from general_manager.rule.handler import InvalidFunctionNodeError, LenHandler

    handler = LenHandler()
    rule = DummyRule(">")

    # Create a node that is not a Call with args
    node = ast.parse("x > 5", mode="eval").body
    invalid_node = ast.parse("5", mode="eval").body  # Not a Call node

    with pytest.raises(InvalidFunctionNodeError) as excinfo:
        handler.handle(
            node,
            invalid_node,
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {},
            rule,
        )
    assert "Invalid left node for len() function" in str(excinfo.value)


def test_invalid_len_threshold_error():
    """Test that InvalidLenThresholdError is raised for non-numeric thresholds."""
    from general_manager.rule.handler import InvalidLenThresholdError

    handler = LenHandler()
    rule = DummyRule(">")

    # Create a comparison where right side is not numeric
    node = ast.parse("len(x) > 'invalid'", mode="eval").body

    with pytest.raises(InvalidLenThresholdError) as excinfo:
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": [1, 2, 3]},
            rule,
        )
    assert "Invalid arguments for len function" in str(excinfo.value)


def test_invalid_numeric_threshold_error_sum():
    """Test that InvalidNumericThresholdError is raised for sum with non-numeric threshold."""
    from general_manager.rule.handler import InvalidNumericThresholdError

    handler = SumHandler()
    rule = DummyRule(">")

    node = ast.parse("sum(x) > 'invalid'", mode="eval").body

    with pytest.raises(InvalidNumericThresholdError) as excinfo:
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": [1, 2, 3]},
            rule,
        )
    assert "Invalid arguments for sum function" in str(excinfo.value)


def test_invalid_numeric_threshold_error_max():
    """Test that InvalidNumericThresholdError is raised for max with non-numeric threshold."""
    from general_manager.rule.handler import InvalidNumericThresholdError

    handler = MaxHandler()
    rule = DummyRule(">")

    node = ast.parse("max(x) > 'invalid'", mode="eval").body

    with pytest.raises(InvalidNumericThresholdError) as excinfo:
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": [1, 2, 3]},
            rule,
        )
    assert "Invalid arguments for max function" in str(excinfo.value)


def test_invalid_numeric_threshold_error_min():
    """Test that InvalidNumericThresholdError is raised for min with non-numeric threshold."""
    from general_manager.rule.handler import InvalidNumericThresholdError

    handler = MinHandler()
    rule = DummyRule(">")

    node = ast.parse("min(x) > 'invalid'", mode="eval").body

    with pytest.raises(InvalidNumericThresholdError) as excinfo:
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": [1, 2, 3]},
            rule,
        )
    assert "Invalid arguments for min function" in str(excinfo.value)


def test_non_empty_iterable_error_sum_empty_list():
    """Test that NonEmptyIterableError is raised when sum receives empty list."""
    from general_manager.rule.handler import NonEmptyIterableError

    handler = SumHandler()
    rule = DummyRule(">")

    node = ast.parse("sum(x) > 0", mode="eval").body

    with pytest.raises(NonEmptyIterableError) as excinfo:
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": []},
            rule,
        )
    assert "sum expects a non-empty iterable" in str(excinfo.value)


def test_non_empty_iterable_error_max_empty_list():
    """Test that NonEmptyIterableError is raised when max receives empty list."""
    from general_manager.rule.handler import NonEmptyIterableError

    handler = MaxHandler()
    rule = DummyRule(">")

    node = ast.parse("max(x) > 0", mode="eval").body

    with pytest.raises(NonEmptyIterableError) as excinfo:
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": []},
            rule,
        )
    assert "max expects a non-empty iterable" in str(excinfo.value)


def test_non_empty_iterable_error_min_empty_list():
    """Test that NonEmptyIterableError is raised when min receives empty list."""
    from general_manager.rule.handler import NonEmptyIterableError

    handler = MinHandler()
    rule = DummyRule(">")

    node = ast.parse("min(x) > 0", mode="eval").body

    with pytest.raises(NonEmptyIterableError) as excinfo:
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": []},
            rule,
        )
    assert "min expects a non-empty iterable" in str(excinfo.value)


def test_numeric_iterable_error_sum_non_numeric():
    """Test that NumericIterableError is raised when sum receives non-numeric values."""
    from general_manager.rule.handler import NumericIterableError

    handler = SumHandler()
    rule = DummyRule(">")

    node = ast.parse("sum(x) > 0", mode="eval").body

    with pytest.raises(NumericIterableError) as excinfo:
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": ["a", "b", "c"]},
            rule,
        )
    assert "sum expects an iterable of numbers" in str(excinfo.value)


def test_numeric_iterable_error_max_non_numeric():
    """Test that NumericIterableError is raised when max receives non-numeric values."""
    from general_manager.rule.handler import NumericIterableError

    handler = MaxHandler()
    rule = DummyRule(">")

    node = ast.parse("max(x) > 0", mode="eval").body

    with pytest.raises(NumericIterableError) as excinfo:
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": ["a", "b", "c"]},
            rule,
        )
    assert "max expects an iterable of numbers" in str(excinfo.value)


def test_numeric_iterable_error_min_non_numeric():
    """Test that NumericIterableError is raised when min receives non-numeric values."""
    from general_manager.rule.handler import NumericIterableError

    handler = MinHandler()
    rule = DummyRule(">")

    node = ast.parse("min(x) > 0", mode="eval").body

    with pytest.raises(NumericIterableError) as excinfo:
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": ["a", "b", "c"]},
            rule,
        )
    assert "min expects an iterable of numbers" in str(excinfo.value)


def test_sum_handler_with_mixed_types():
    """Test sum handler rejects mixed numeric and non-numeric types."""
    from general_manager.rule.handler import NumericIterableError

    handler = SumHandler()
    rule = DummyRule(">")

    node = ast.parse("sum(x) > 10", mode="eval").body

    with pytest.raises(NumericIterableError):
        handler.handle(
            node,
            node.left,  # type: ignore
            node.comparators[0],  # type: ignore
            node.ops[0],  # type: ignore
            {"x": [1, 2, "three", 4]},
            rule,
        )
