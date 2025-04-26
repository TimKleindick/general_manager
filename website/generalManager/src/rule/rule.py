# generalManager/src/rule/rule.py
from __future__ import annotations
from typing import Callable, Optional, Any, Dict, List, TypeVar, Generic, TYPE_CHECKING
import ast
import inspect
import re
import textwrap


from django.conf import settings
from django.utils.module_loading import import_string

from generalManager.src.rule.handler import (
    BaseRuleHandler,
    LenHandler,
    IntersectionCheckHandler,
)

from generalManager.src.manager.generalManager import GeneralManager

GeneralManagerType = TypeVar("GeneralManagerType", bound=GeneralManager)


class Rule(Generic[GeneralManagerType]):
    def __init__(
        self,
        func: Callable[[GeneralManagerType], bool],
        custom_error_message: Optional[str] = None,
        ignore_if_none: bool = True,
    ) -> None:
        self._func = func
        self._custom_error_message = custom_error_message
        self._ignore_if_none = ignore_if_none
        self._last_result: Optional[bool] = None
        self._last_input: Optional[Any] = None

        # 1x Quelltext & AST parsen
        src = inspect.getsource(func)
        lines = src.splitlines()
        if lines and lines[0].strip().startswith("@"):
            idx = next(i for i, L in enumerate(lines) if not L.strip().startswith("@"))
            src = "\n".join(lines[idx:])
        src = textwrap.dedent(src)
        self._tree = ast.parse(src)
        # Elternverweise für komplexe Analysen
        for parent in ast.walk(self._tree):
            for child in ast.iter_child_nodes(parent):
                setattr(child, "parent", parent)

        # Attribute x.foo.bar als Variablen extrahieren
        self._variables = self._extract_variables()

        # Handler laden: built-in + custom aus settings
        self._handlers: Dict[str, BaseRuleHandler] = {}
        for cls in (LenHandler, IntersectionCheckHandler):
            inst = cls()
            self._handlers[inst.function_name] = inst
        for path in getattr(settings, "RULE_HANDLERS", []):
            handler_cls = import_string(path)
            inst = handler_cls()
            self._handlers[inst.function_name] = inst

    @property
    def func(self) -> Callable[[GeneralManagerType], bool]:
        return self._func

    @property
    def customErrorMessage(self) -> Optional[str]:
        return self._custom_error_message

    @property
    def variables(self) -> List[str]:
        return self._variables

    @property
    def lastEvaluationResult(self) -> Optional[bool]:
        return self._last_result

    @property
    def lastEvaluationInput(self) -> Optional[Any]:
        return self._last_input

    @property
    def ignoreIfNone(self) -> bool:
        return self._ignore_if_none

    def evaluate(self, x: GeneralManagerType) -> bool | None:
        self._last_input = x
        vals = self._extract_variable_values(x)
        if self._ignore_if_none and any(v is None for v in vals.values()):
            self._last_result = None
            return True
        self._last_result = self._func(x)
        return self._last_result

    def validateCustomErrorMessage(self) -> None:
        """Prüft, dass alle Variablen in der custom_error_message vorkommen."""
        if not self._custom_error_message:
            return
        vars_in_msg = set(re.findall(r"{([^}]+)}", self._custom_error_message))
        missing = [v for v in self._variables if v not in vars_in_msg]
        if missing:
            raise ValueError(
                f"The custom error message does not contain all used variables: {missing}"
            )

    def getErrorMessage(self) -> Optional[Dict[str, str]]:
        if self._last_result or self._last_result is None:
            return None
        if self._last_input is None:
            raise ValueError("No input provided for error message generation")

        # Erst Custom-Template validieren
        self.validateCustomErrorMessage()

        vals = self._extract_variable_values(self._last_input)

        # custom message?
        if self._custom_error_message:
            formatted = re.sub(
                r"{([^}]+)}",
                lambda m: str(vals.get(m.group(1), m.group(0))),
                self._custom_error_message,
            )
            return {v: formatted for v in self._variables}

        # automatische Generierung
        errors = self._generate_error_messages(vals)
        return errors or None

    def _extract_variables(self) -> List[str]:
        class VarVisitor(ast.NodeVisitor):
            def __init__(self):
                self.vars: set[str] = set()

            def visit_Attribute(self, node: ast.Attribute) -> None:
                names: List[str] = []
                curr = node
                while isinstance(curr, ast.Attribute):
                    names.append(curr.attr)
                    curr = curr.value
                if isinstance(curr, ast.Name) and curr.id == "x":
                    self.vars.add(".".join(reversed(names)))
                self.generic_visit(node)

        visitor = VarVisitor()
        visitor.visit(self._tree)
        return sorted(visitor.vars)

    def _extract_variable_values(
        self, x: GeneralManagerType
    ) -> Dict[str, Optional[Any]]:
        out: Dict[str, Any] = {}
        for var in self._variables:
            obj = x
            for part in var.split("."):
                obj = getattr(obj, part)
                if obj is None:
                    break
            out[var] = obj
        return out

    def _extract_comparisons(self) -> List[ast.Compare]:
        class CompVisitor(ast.NodeVisitor):
            def __init__(self):
                self.comps: List[ast.Compare] = []

            def visit_Compare(self, node: ast.Compare) -> None:
                self.comps.append(node)
                self.generic_visit(node)

        v = CompVisitor()
        v.visit(self._tree)
        return v.comps

    def _contains_logical_ops(self) -> bool:
        class LogicVisitor(ast.NodeVisitor):
            def __init__(self):
                self.found = False

            def visit_BoolOp(self, node: ast.BoolOp) -> None:
                if isinstance(node.op, (ast.And, ast.Or)):
                    self.found = True
                self.generic_visit(node)

        v = LogicVisitor()
        v.visit(self._tree)
        return v.found

    def _generate_error_messages(
        self, var_values: Dict[str, Optional[Any]]
    ) -> Dict[str, str]:
        errors: Dict[str, str] = {}
        comps = self._extract_comparisons()
        logical = self._contains_logical_ops()

        if comps:
            for cmp in comps:
                left, rights, ops = cmp.left, cmp.comparators, cmp.ops
                for right, op in zip(rights, ops):
                    # Spezial-Handler?
                    if isinstance(left, ast.Call):
                        fn = self._get_node_name(left.func)
                        handler = self._handlers.get(fn)
                        if handler:
                            errors.update(
                                handler.handle(cmp, left, right, op, var_values, self)
                            )
                            continue

                    # Standard-Fehler
                    lnm = self._get_node_name(left)
                    rnm = self._get_node_name(right)
                    lval = self._eval_node(left)
                    rval = self._eval_node(right)
                    ldisp = f"[{lnm}] ({lval})" if lnm in var_values else str(lval)
                    rdisp = f"[{rnm}] ({rval})" if rnm in var_values else str(rval)
                    sym = self._get_op_symbol(op)
                    msg = f"{ldisp} must be {sym} {rdisp}!"
                    if lnm in var_values:
                        errors[lnm] = msg
                    if rnm in var_values and rnm != lnm:
                        errors[rnm] = msg

            if logical and not self._last_result:
                combo = ", ".join(f"[{v}]" for v in self._variables)
                msg = f"{combo} combination is not valid"
                for v in self._variables:
                    errors[v] = msg

            return errors

        # kein Vergleich → pauschale Meldung
        combo = ", ".join(f"[{v}]" for v in self._variables)
        return {v: f"{combo} combination is not valid" for v in self._variables}

    def _get_op_symbol(self, op: ast.cmpop) -> str:
        return {
            ast.Lt: "<",
            ast.LtE: "<=",
            ast.Gt: ">",
            ast.GtE: ">=",
            ast.Eq: "==",
            ast.NotEq: "!=",
            ast.Is: "is",
            ast.IsNot: "is not",
            ast.In: "in",
            ast.NotIn: "not in",
        }.get(type(op), "?")

    def _get_node_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Attribute):
            parts: List[str] = []
            curr: ast.AST = node
            while isinstance(curr, ast.Attribute):
                parts.insert(0, curr.attr)
                curr = curr.value
            return ".".join(parts)
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return ""
        if isinstance(node, ast.Call):
            fn = self._get_node_name(node.func)
            args = ", ".join(self._get_node_name(a) for a in node.args)
            return f"{fn}({args})"
        try:
            return ast.unparse(node)
        except Exception:
            return ""

    def _eval_node(self, node: ast.expr) -> Optional[Any]:
        """
        Evaluiert einen AST-Ausdrucks-Knoten sicher im Kontext von `x`.
        Gibt None zurück, wenn der Knoten kein ast.expr ist oder ein Fehler auftritt.
        """
        # Pylance-freundliche Typprüfung
        if not isinstance(node, ast.expr):
            return None

        try:
            # ast.Expression erwartet nun wirklich einen ast.expr
            expr = ast.Expression(body=node)
            code = compile(expr, filename="<ast>", mode="eval")
            return eval(code, {"x": self._last_input}, {})
        except Exception:
            return None
