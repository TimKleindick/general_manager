# generalManager/src/rule/handlers.py

from __future__ import annotations
import ast
from typing import Dict, Optional, TYPE_CHECKING, cast

if TYPE_CHECKING:
    # Forward-Reference auf Rule mit beliebigem Generic-Parameter
    from general_manager.rule.rule import Rule
    from general_manager.manager import GeneralManager


class BaseRuleHandler:
    """Schnittstelle für Rule-Handler."""

    function_name: str  # ClassVar, der Name, unter dem dieser Handler registriert wird

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
        raise NotImplementedError


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
            msg = f"[{var_name}] ({var_value}) must be {op_symbol} {right_value}!"

        return {var_name: msg}


class IntersectionCheckHandler(BaseRuleHandler):
    function_name = "intersectionCheck"

    def handle(
        self,
        node: ast.AST,
        left: Optional[ast.expr],
        right: Optional[ast.expr],
        op: Optional[ast.cmpop],
        var_values: Dict[str, Optional[object]],
        rule: Rule[GeneralManager],
    ) -> Dict[str, str]:
        # Der Aufruf steht in `left`, nicht in `node`
        call_node = cast(ast.Call, left)
        if not isinstance(call_node, ast.Call):
            return {"error": "Invalid arguments for intersectionCheck"}

        args = call_node.args
        if len(args) < 2:
            return {"error": "Invalid arguments for intersectionCheck"}

        start_node, end_node = args[0], args[1]
        start_name = rule._get_node_name(start_node)
        end_name = rule._get_node_name(end_node)
        start_val = var_values.get(start_name)
        end_val = var_values.get(end_name)

        msg = (
            f"[{start_name}] ({start_val}) and "
            f"[{end_name}] ({end_val}) must not overlap with existing ranges."
        )
        return {start_name: msg, end_name: msg}
