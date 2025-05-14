# generalManager/src/rule/handlers.py

from __future__ import annotations
import ast
from typing import Dict, Optional, TYPE_CHECKING, cast
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    # Forward-Reference auf Rule mit beliebigem Generic-Parameter
    from general_manager.rule.rule import Rule
    from general_manager.manager import GeneralManager


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
        Erstelle Fehlermeldungen für den Vergleichs- oder Funktionsaufruf.
        """
        pass


class LenHandler(BaseRuleHandler):
    function_name = "len"

    def handle(
        self,
        node: ast.AST,
        left: Optional[ast.expr],
        right: Optional[ast.expr],
        op: Optional[ast.cmpop],
        var_values: Dict[str, Optional[object]],
        rule: Rule,
    ) -> Dict[str, str]:
        # Wir erwarten hier einen Compare-Knoten
        if not isinstance(node, ast.Compare):
            return {}
        compare_node = node

        left_node = compare_node.left
        right_node = compare_node.comparators[0]
        op_symbol = rule._get_op_symbol(op)

        # Argument von len(...)
        if not (isinstance(left_node, ast.Call) and left_node.args):
            raise ValueError("Invalid left node for len function")
        arg_node = left_node.args[0]

        var_name = rule._get_node_name(arg_node)
        var_value = var_values.get(var_name)

        # --- Hier der Typ-Guard für right_value ---
        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise ValueError("Invalid arguments for len function")
        right_value: int | float = raw

        # Schwellenwert je nach Operator
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


class SumHandler(BaseRuleHandler):
    function_name = "sum"

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

        # Call-Knoten checken
        if not (isinstance(left_node, ast.Call) and left_node.args):
            raise ValueError("Invalid left node for sum function")
        arg_node = left_node.args[0]

        # Name und Wert holen
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

        # Threshold je nach Operator
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

        # Message formulieren
        if op_symbol in (">", ">="):
            msg = f"[{var_name}] (sum={total}) is too small (min sum {threshold})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] (sum={total}) is too large (max sum {threshold})!"
        else:
            msg = f"[{var_name}] (sum={total}) must be {right_value}!"

        return {var_name: msg}


class MaxHandler(BaseRuleHandler):
    function_name = "max"

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
            raise ValueError("Invalid left node for max function")
        arg_node = left_node.args[0]

        var_name = rule._get_node_name(arg_node)
        raw_iter = var_values.get(var_name)
        if not isinstance(raw_iter, (list, tuple)) or len(raw_iter) == 0:
            raise ValueError("max expects a non-empty iterable")
        current = max(raw_iter)

        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise ValueError("Invalid arguments for max function")
        right_value = raw

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

        if op_symbol in (">", ">="):
            msg = f"[{var_name}] (max={current}) is too small (min {threshold})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] (max={current}) is too large (max {threshold})!"
        else:
            msg = f"[{var_name}] (max={current}) must be {right_value}!"

        return {var_name: msg}


class MinHandler(BaseRuleHandler):
    function_name = "min"

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
            raise ValueError("Invalid left node for min function")
        arg_node = left_node.args[0]

        var_name = rule._get_node_name(arg_node)
        raw_iter = var_values.get(var_name)
        if not isinstance(raw_iter, (list, tuple)) or len(raw_iter) == 0:
            raise ValueError("min expects a non-empty iterable")
        current = min(raw_iter)

        raw = rule._eval_node(right_node)
        if not isinstance(raw, (int, float)):
            raise ValueError("Invalid arguments for min function")
        right_value = raw

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

        if op_symbol in (">", ">="):
            msg = f"[{var_name}] (min={current}) is too small (min {threshold})!"
        elif op_symbol in ("<", "<="):
            msg = f"[{var_name}] (min={current}) is too large (max {threshold})!"
        else:
            msg = f"[{var_name}] (min={current}) must be {right_value}!"

        return {var_name: msg}
