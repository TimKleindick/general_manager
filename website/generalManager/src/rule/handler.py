# generalManager/src/rule/handlers.py

from typing import Dict, Any, Optional
import ast


class BaseRuleHandler:
    """Interface für alle Rule-Handler."""

    function_name: str  # der Name, unter dem der Handler registriert wird

    def handle(
        self,
        node: ast.AST,
        left: Optional[ast.expr],
        right: Optional[ast.expr],
        op: Optional[ast.cmpop],
        var_values: Dict[str, Optional[Any]],
        rule: Any,
    ) -> Dict[str, str]:
        """Gibt ein Dict var→Fehlermeldung zurück."""
        raise NotImplementedError


class LenHandler(BaseRuleHandler):
    function_name = "len"

    def handle(
        self,
        node: ast.AST,  # jetzt ast.AST statt ast.Compare
        left: Optional[ast.expr],
        right: Optional[ast.expr],
        op: Optional[ast.cmpop],
        var_values: Dict[str, Optional[Any]],
        rule: Any,
    ) -> Dict[str, str]:
        # wir erwarten hier einen Compare-Knoten
        if not isinstance(node, ast.Compare):
            return {}
        left_node = node.left
        right_node = node.comparators[0]
        op_symbol = rule._get_op_symbol(op)

        if isinstance(left_node, ast.Call) and left_node.args:
            arg_node = left_node.args[0]
        else:
            raise ValueError("Invalid left node for len function")

        var_name = rule._get_node_name(arg_node)
        var_value = var_values.get(var_name)

        right_value = rule._eval_node(right_node)
        if right_value is None:
            raise ValueError("Invalid arguments for len function")

        # Schwellenwerte je nach Operator
        if op_symbol == ">":
            compare_value = right_value + 1
        elif op_symbol == ">=":
            compare_value = right_value
        elif op_symbol == "<":
            compare_value = right_value - 1
        elif op_symbol == "<=":
            compare_value = right_value
        else:
            compare_value = right_value

        # Fehlermeldung bauen
        if op_symbol in (">", ">="):
            msg = (
                f"[{var_name}] ({var_value}) is too short (min length {compare_value})!"
            )
        elif op_symbol in ("<", "<="):
            msg = (
                f"[{var_name}] ({var_value}) is too long (max length {compare_value})!"
            )
        else:
            msg = f"[{var_name}] ({var_value}) must be {op_symbol} {right_value}!"

        return {var_name: msg}


class IntersectionCheckHandler(BaseRuleHandler):
    function_name = "intersectionCheck"

    def handle(
        self,
        node: ast.AST,  # jetzt ast.AST statt ast.Call
        left: Optional[ast.expr],
        right: Optional[ast.expr],
        op: Optional[ast.cmpop],
        var_values: Dict[str, Optional[Any]],
        rule: Any,
    ) -> Dict[str, str]:
        # Wir erwarten hier, dass `left` der Call-Knoten ist.
        if not isinstance(left, ast.Call):
            return {"error": "Invalid arguments for intersectionCheck"}
        args = left.args
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
