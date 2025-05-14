from django.test import TestCase
import ast
from general_manager.rule.handler import LenHandler, MinHandler, MaxHandler, SumHandler
from typing import Optional, Dict, Any
import pytest


class DummyRule:
    def __init__(self, op_symbol: str):
        self._op_symbol = op_symbol

    def _get_op_symbol(self, op: Optional[ast.cmpop]) -> str:
        return self._op_symbol

    def _get_node_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        raise ValueError("Unexpected node type")

    def _eval_node(self, node: ast.AST) -> Any:
        # Im rechten Literal-Fall: ast.Constant
        if isinstance(node, ast.Constant):
            return node.value
        # Oder: Name-Knoten für var_values lookup
        if isinstance(node, ast.Name):
            return None
        raise ValueError("Cannot eval node")


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
        node, node.left, node.comparators[0], node.ops[0], var_values, rule
    )
    assert result == expected


def test_non_compare_node_returns_empty():
    # z. B. ein Call-Knoten statt Compare
    node = ast.parse("print('hallo')", mode="eval").body
    rule = DummyRule(">")
    assert len_handler.handle(node, None, None, None, {}, rule) == {}


@pytest.mark.parametrize(
    "bad_expr, op",
    [
        ("x + 1 > 2", ">"),  # left kein Call
        ("len(x) > 'z'", ">"),  # right kein Zahl-Literal
    ],
)
def test_len_handler_invalid_raises(bad_expr, op):
    node = ast.parse(bad_expr, mode="eval").body
    rule = DummyRule(op)
    with pytest.raises(ValueError):
        len_handler.handle(
            node,
            node.left,
            getattr(node, "comparators", [None])[0],
            node.ops[0] if hasattr(node, "ops") else None,
            {"x": "hi"},
            rule,
        )


@pytest.mark.parametrize(
    "expr, op_symbol, var_values, expected",
    [
        (
            "sum(x) > 3",
            ">",
            {"x": [1, 1]},
            {"x": "[x] (sum=2) is too small (min sum 4)!"},
        ),
        (
            "sum(y) >= 4",
            ">=",
            {"y": [1, 2]},
            {"y": "[y] (sum=3) is too small (min sum 4)!"},
        ),
        (
            "sum(z) < 5",
            "<",
            {"z": [2, 6]},
            {"z": "[z] (sum=8) is too large (max sum 4)!"},
        ),
        (
            "sum(w) <= 3",
            "<=",
            {"w": [1, 4]},
            {"w": "[w] (sum=5) is too large (max sum 3)!"},
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
        node, node.left, node.comparators[0], node.ops[0], var_values, rule
    )
    assert result == expected


# Fehler-Fälle für SumHandler
def test_sum_handler_non_compare_returns_empty():
    node = ast.parse("print(1)", mode="eval").body
    rule = DummyRule(">")
    assert sum_handler.handle(node, None, None, None, {}, rule) == {}


@pytest.mark.parametrize(
    "expr, var_values, error_msg",
    [
        ("x + 1 > 2", {"x": [1]}, "Invalid left node for sum() function"),
        ("sum(x) > 3", {"x": None}, "sum expects an iterable of numbers"),
        ("sum(x) > 'a'", {"x": [1, 2]}, "Invalid arguments for sum function"),
    ],
)
def test_sum_handler_invalid(expr, var_values, error_msg):
    node = ast.parse(expr, mode="eval").body
    rule = DummyRule(">")
    with pytest.raises(ValueError) as excinfo:
        sum_handler.handle(
            node,
            node.left,
            getattr(node, "comparators", [None])[0],
            node.ops[0],
            var_values,
            rule,
        )
    assert error_msg in str(excinfo.value)


@pytest.mark.parametrize(
    "expr, op_symbol, var_values, expected",
    [
        (
            "max(x) > 3",
            ">",
            {"x": [1, 2]},
            {"x": "[x] (max=2) is too small (min 4)!"},
        ),
        (
            "max(y) >= 2",
            ">=",
            {"y": [1, 1]},
            {"y": "[y] (max=1) is too small (min 2)!"},
        ),
        (
            "max(z) < 5",
            "<",
            {"z": [6, 7]},
            {"z": "[z] (max=7) is too large (max 4)!"},
        ),
        (
            "max(w) <= 3",
            "<=",
            {"w": [1, 4]},
            {"w": "[w] (max=4) is too large (max 3)!"},
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
        node, node.left, node.comparators[0], node.ops[0], var_values, rule
    )
    assert result == expected


def test_max_handler_non_compare_returns_empty():
    node = ast.parse("42", mode="eval").body
    rule = DummyRule("<")
    assert max_handler.handle(node, None, None, None, {}, rule) == {}


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
    with pytest.raises(ValueError) as excinfo:
        max_handler.handle(
            node,
            node.left,
            getattr(node, "comparators", [None])[0],
            node.ops[0],
            var_values,
            rule,
        )
    assert error_msg in str(excinfo.value)


@pytest.mark.parametrize(
    "expr, op_symbol, var_values, expected",
    [
        (
            "min(x) > 3",
            ">",
            {"x": [0.1234]},
            {"x": "[x] (min=0.1234) is too small (min 4)!"},
        ),
        (
            "min(y) >= 2",
            ">=",
            {"y": [1, 3]},
            {"y": "[y] (min=1) is too small (min 2)!"},
        ),
        (
            "min(z) < 5",
            "<",
            {"z": [6, 7, 8]},
            {"z": "[z] (min=6) is too large (max 4)!"},
        ),
        (
            "min(w) <= 2",
            "<=",
            {"w": [3]},
            {"w": "[w] (min=3) is too large (max 2)!"},
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
        node, node.left, node.comparators[0], node.ops[0], var_values, rule
    )
    assert result == expected


def test_min_handler_non_compare_returns_empty():
    node = ast.parse("'foo'", mode="eval").body
    rule = DummyRule(">=")
    assert min_handler.handle(node, None, None, None, {}, rule) == {}


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
    with pytest.raises(ValueError) as excinfo:
        min_handler.handle(
            node,
            node.left,
            getattr(node, "comparators", [None])[0],
            node.ops[0],
            var_values,
            rule,
        )
    assert error_msg in str(excinfo.value)
