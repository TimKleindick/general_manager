# generalManager/src/rule/rule.py

from __future__ import annotations
import ast
import inspect
import re
import textwrap
from typing import (
    Callable,
    ClassVar,
    Dict,
    Generic,
    List,
    Optional,
    TypeVar,
    cast,
)

from django.conf import settings
from django.utils.module_loading import import_string

from general_manager.rule.handler import (
    BaseRuleHandler,
    LenHandler,
    MaxHandler,
    MinHandler,
    SumHandler,
)
from general_manager.manager.generalManager import GeneralManager

GeneralManagerType = TypeVar("GeneralManagerType", bound=GeneralManager)


class Rule(Generic[GeneralManagerType]):
    """
    Rule kapselt eine boolsche Bedingungsfunktion und erzeugt bei Fehlschlag
    automatisierte oder benutzerdefinierte Fehlermeldungen auf Basis des AST.
    """

    _func: Callable[[GeneralManagerType], bool]
    _custom_error_message: Optional[str]
    _ignore_if_none: bool
    _last_result: Optional[bool]
    _last_input: Optional[GeneralManagerType]
    _tree: ast.AST
    _variables: List[str]
    _handlers: Dict[str, BaseRuleHandler]

    def __init__(
        self,
        func: Callable[[GeneralManagerType], bool],
        custom_error_message: Optional[str] = None,
        ignore_if_none: bool = True,
    ) -> None:
        self._func = func
        self._custom_error_message = custom_error_message
        self._ignore_if_none = ignore_if_none
        self._last_result = None
        self._last_input = None

        # 1) Quelltext holen, Decorators abschneiden, Dedent
        src = inspect.getsource(func)
        lines = src.splitlines()
        if lines and lines[0].strip().startswith("@"):
            idx = next(i for i, L in enumerate(lines) if not L.strip().startswith("@"))
            src = "\n".join(lines[idx:])
        src = textwrap.dedent(src)

        # 2) AST parsen & Elternverweise setzen
        self._tree = ast.parse(src)
        for parent in ast.walk(self._tree):
            for child in ast.iter_child_nodes(parent):
                setattr(child, "parent", parent)

        # 3) Variablen extrahieren
        self._variables = self._extract_variables()

        # 4) Handler registrieren
        self._handlers = {}  # type: Dict[str, BaseRuleHandler]
        for cls in (LenHandler, MaxHandler, MinHandler, SumHandler):
            inst = cls()
            self._handlers[inst.function_name] = inst
        for path in getattr(settings, "RULE_HANDLERS", []):
            handler_cls: type[BaseRuleHandler] = import_string(path)
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
    def lastEvaluationInput(self) -> Optional[GeneralManagerType]:
        return self._last_input

    @property
    def ignoreIfNone(self) -> bool:
        return self._ignore_if_none

    def evaluate(self, x: GeneralManagerType) -> Optional[bool]:
        """
        Führt die Regel aus. Gibt False bei Fehlschlag, True bei Erfolg
        und None, falls ignore_if_none aktiv ist und eine Variable None war.
        """
        self._last_input = x
        vals = self._extract_variable_values(x)
        if self._ignore_if_none and any(v is None for v in vals.values()):
            self._last_result = None
            return None

        self._last_result = self._func(x)
        return self._last_result

    def validateCustomErrorMessage(self) -> None:
        """
        Stellt sicher, dass in der custom_error_message alle Variablen
        aus self._variables verwendet werden.
        """
        if not self._custom_error_message:
            return

        vars_in_msg = set(re.findall(r"{([^}]+)}", self._custom_error_message))
        missing = [v for v in self._variables if v not in vars_in_msg]
        if missing:
            raise ValueError(
                f"The custom error message does not contain all used variables: {missing}"
            )

    def getErrorMessage(self) -> Optional[Dict[str, str]]:
        """
        Liefert ein Dict variable→message, oder None, wenn kein Fehler.
        """
        if self._last_result or self._last_result is None:
            return None
        if self._last_input is None:
            raise ValueError("No input provided for error message generation")

        # Validierung und Ersetzen der Template-Platzhalter
        self.validateCustomErrorMessage()
        vals = self._extract_variable_values(self._last_input)

        if self._custom_error_message:
            formatted = re.sub(
                r"{([^}]+)}",
                lambda m: str(vals.get(m.group(1), m.group(0))),
                self._custom_error_message,
            )
            return {v: formatted for v in self._variables}

        errors = self._generate_error_messages(vals)
        return errors or None

    def _extract_variables(self) -> List[str]:
        class VarVisitor(ast.NodeVisitor):
            vars: set[str] = set()

            def visit_Attribute(self, node: ast.Attribute) -> None:
                parts: list[str] = []
                curr: ast.AST = node
                while isinstance(curr, ast.Attribute):
                    parts.append(curr.attr)
                    curr = curr.value
                if isinstance(curr, ast.Name) and curr.id == "x":
                    self.vars.add(".".join(reversed(parts)))
                self.generic_visit(node)

        visitor = VarVisitor()
        visitor.visit(self._tree)
        return sorted(visitor.vars)

    def _extract_variable_values(
        self, x: GeneralManagerType
    ) -> Dict[str, Optional[object]]:
        out: Dict[str, Optional[object]] = {}
        for var in self._variables:
            obj: object = x  # type: ignore
            for part in var.split("."):
                obj = getattr(obj, part)
                if obj is None:
                    break
            out[var] = obj
        return out

    def _extract_comparisons(self) -> list[ast.Compare]:
        class CompVisitor(ast.NodeVisitor):
            comps: list[ast.Compare] = []

            def visit_Compare(self, node: ast.Compare) -> None:
                self.comps.append(node)
                self.generic_visit(node)

        visitor = CompVisitor()
        visitor.visit(self._tree)
        return visitor.comps

    def _contains_logical_ops(self) -> bool:
        class LogicVisitor(ast.NodeVisitor):
            found: bool = False

            def visit_BoolOp(self, node: ast.BoolOp) -> None:
                if isinstance(node.op, (ast.And, ast.Or)):
                    self.found = True
                self.generic_visit(node)

        visitor = LogicVisitor()
        visitor.visit(self._tree)
        return visitor.found

    def _generate_error_messages(
        self, var_values: Dict[str, Optional[object]]
    ) -> Dict[str, str]:
        errors: Dict[str, str] = {}
        comparisons = self._extract_comparisons()
        logical = self._contains_logical_ops()

        if comparisons:
            for cmp in comparisons:
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

    def _get_op_symbol(self, op: Optional[ast.cmpop]) -> str:
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
            parts: list[str] = []
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
            # ast.unparse gibt einen str zurück
            return ast.unparse(node)
        except Exception:
            return ""

    def _eval_node(self, node: ast.expr) -> Optional[object]:
        """
        Evaluiert einen AST-Ausdruck im Kontext von `x`.
        """
        if not isinstance(node, ast.expr):
            return None
        try:
            expr = ast.Expression(body=node)
            code = compile(expr, "<ast>", "eval")
            return eval(code, {"x": self._last_input}, {})
        except Exception:
            return None
