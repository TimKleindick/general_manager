from typing import Callable, Optional, Any, Dict, List, TypeVar, Generic, TYPE_CHECKING
import ast
import inspect
import re

if TYPE_CHECKING:
    from generalManager.src.interface.baseInterface import GeneralManagerType


class Rule(Generic[GeneralManagerType]):
    def __init__(
        self,
        func: Callable[[GeneralManagerType], bool],
        custom_error_message: Optional[str] = None,
        ignore_if_none: bool = True,
    ) -> None:
        self.__func: Callable[[GeneralManagerType], bool] = func
        self.__customErrorMessage: Optional[str] = custom_error_message
        self.__variables: List[str] = self.__extractVariables()
        self.__lastEvaluationResult: Optional[bool] = None
        self.__lastEvaluationInput: Optional[Any] = None
        self.__ignoreIfNone: bool = ignore_if_none

        self.__function_handlers: Dict[str, Callable[..., Dict[str, str]]] = {
            "len": self.__handle_len_function,
            "intersectionCheck": self.__handle_intersection_check,
        }

    @property
    def func(self) -> Callable[[GeneralManagerType], bool]:
        return self.__func

    @property
    def customErrorMessage(self) -> Optional[str]:
        return self.__customErrorMessage

    @property
    def variables(self) -> List[str]:
        return self.__variables

    @property
    def lastEvaluationResult(self) -> Optional[bool]:
        return self.__lastEvaluationResult

    @property
    def lastEvaluationInput(self) -> Optional[Any]:
        return self.__lastEvaluationInput

    @property
    def ignoreIfNone(self) -> bool:
        return self.__ignoreIfNone

    def evaluate(self, x: GeneralManagerType) -> bool | None:
        self.__lastEvaluationInput = x
        var_values = self.__extractVariableValues(x)
        if self.__ignoreIfNone:
            if any(value is None for value in var_values.values()):
                self.__lastEvaluationResult = None
                return True
        try:
            self.__lastEvaluationResult = self.__func(x)
        except TypeError:
            self.__lastEvaluationResult = False
        return self.__lastEvaluationResult

    def getErrorMessage(self) -> Optional[Dict[str, str]]:
        if self.__lastEvaluationResult or self.__lastEvaluationResult is None:
            return None
        x = self.__lastEvaluationInput
        if x is None:
            raise ValueError("No input provided for error message generation")
        var_values = self.__extractVariableValues(x)

        if self.__customErrorMessage:
            error_message_formatted = re.sub(
                r"{([^}]+)}",
                lambda match: str(var_values.get(match.group(1), match.group(0))),
                self.__customErrorMessage,
            )
            return {var: error_message_formatted for var in self.__variables}
        else:
            return self.__generateErrorMessages(var_values)

    def validateCustomErrorMessage(self) -> None:
        if self.__customErrorMessage:
            vars_in_message = set(re.findall(r"{([^}]+)}", self.__customErrorMessage))
            missing_vars = [
                var for var in self.__variables if var not in vars_in_message
            ]
            if missing_vars:
                raise ValueError(
                    "The custom error message does not contain all used variables."
                )

    def __extractVariables(self) -> List[str]:
        source = inspect.getsource(self.__func).strip()
        if source.startswith("@"):
            source = "\n".join(source.split("\n")[1:])
        tree = ast.parse(source)

        class VariableExtractor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.variables: set[str] = set()
                self.function_names: set[str] = set()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.function_names.add(node.name)
                self.generic_visit(node)

            def visit_Name(self, node: ast.Name) -> None:
                if (
                    node.id != "x"
                    and node.id not in dir(__builtins__)
                    and node.id not in self.function_names
                ):
                    if not isinstance(node.ctx, ast.Load):
                        return
                    if not self.__is_part_of_function_name(node):
                        self.variables.add(node.id)

            def visit_Attribute(self, node: ast.Attribute) -> None:
                full_name = self._get_full_attribute_name(node)
                if full_name.startswith("x."):
                    full_name = full_name[2:]
                    self.variables.add(full_name)
                self.generic_visit(node)

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name):
                    self.function_names.add(node.func.id)
                self.generic_visit(node)

            def __is_part_of_function_name(self, node: ast.Name) -> bool:
                parent = getattr(node, "parent", None)
                return isinstance(parent, ast.Call) and parent.func == node

            def _get_full_attribute_name(self, node: ast.Attribute) -> str:
                names: list[str] = []
                current = node
                while isinstance(current, (ast.Attribute, ast.Subscript)):
                    if isinstance(current, ast.Attribute):
                        names.append(current.attr)
                        current = current.value
                    else:
                        current = current.value
                if isinstance(current, ast.Name):
                    names.append(current.id)
                return ".".join(reversed(names))

        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                setattr(child, "parent", node)

        extractor = VariableExtractor()
        extractor.visit(tree)

        return list(extractor.variables)

    def __extractVariableValues(
        self, x: GeneralManagerType
    ) -> Dict[str, Optional[Any]]:
        var_values: dict[str, Any] = {}
        for var in self.__variables:
            parts = var.split(".")
            value = x
            for part in parts:
                value = getattr(value, part)
                if value is None:
                    break
            var_values[var] = value
        return var_values

    def __extractComparisons(self) -> List[ast.Compare]:
        source = inspect.getsource(self.__func).strip()
        if source.startswith("@"):
            source = "\n".join(source.split("\n")[1:])
        tree = ast.parse(source)

        class ComparisonExtractor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.comparisons: List[ast.Compare] = []

            def visit_Compare(self, node: ast.Compare) -> None:
                self.comparisons.append(node)
                self.generic_visit(node)

        comp_extractor = ComparisonExtractor()
        comp_extractor.visit(tree)
        return comp_extractor.comparisons

    def __containsLogicalOps(self) -> bool:
        source = inspect.getsource(self.__func).strip()
        if source.startswith("@"):
            source = "\n".join(source.split("\n")[1:])
        tree = ast.parse(source)

        class LogicalOpDetector(ast.NodeVisitor):
            def __init__(self) -> None:
                self.found: bool = False

            def visit_BoolOp(self, node: ast.BoolOp) -> None:
                if isinstance(node.op, (ast.And, ast.Or)):
                    self.found = True
                self.generic_visit(node)

        detector = LogicalOpDetector()
        detector.visit(tree)
        return detector.found

    def __generateErrorMessages(
        self, var_values: Dict[str, Optional[Any]]
    ) -> Dict[str, str]:
        error_messages: dict[str, str] = {}
        comparisons = self.__extractComparisons()
        logical_ops = self.__containsLogicalOps()

        if comparisons:
            for node in comparisons:
                left = node.left
                rights = node.comparators
                ops = node.ops

                for right, op in zip(rights, ops):
                    op_symbol = self.__getOperatorSymbol(op)
                    if op_symbol:
                        handler = self.__get_handler(left)
                        if handler:
                            error_message = handler(node, left, right, op, var_values)
                            if error_message:
                                error_messages.update(error_message)
                            continue

                        left_name = self.__getNodeName(left)
                        right_name = self.__getNodeName(right)

                        left_value = self.__evaluateNode(left)
                        right_value = self.__evaluateNode(right)

                        left_display = self.__formatDisplay(left_name, left_value, left)
                        right_display = self.__formatDisplay(
                            right_name, right_value, right
                        )

                        error_message = (
                            f"{left_display} must be {op_symbol} {right_display}!"
                        )
                        if left_name in var_values:
                            error_messages[left_name] = error_message
                        if right_name in var_values and right_name != left_name:
                            error_messages[right_name] = error_message

            if logical_ops and not self.__lastEvaluationResult:
                combined_vars = ", ".join([f"[{var}]" for var in self.__variables])
                error_message = f"{combined_vars} combination is not valid"
                for var in self.__variables:
                    error_messages[var] = error_message
            return error_messages
        else:
            func_node = self.__getFunctionNode()
            if func_node:
                handler = self.__get_handler(func_node)
                if handler:
                    error_message = handler(func_node, None, None, None, var_values)
                    if error_message:
                        return error_message

            if self.__variables:
                combined_vars = ", ".join([f"[{var}]" for var in self.__variables])
                error_message = f"{combined_vars} combination is not valid"
                for var in self.__variables:
                    error_messages[var] = error_message
                return error_messages
            else:
                return {"error": "An error occurred"}

    def __get_handler(self, node: ast.AST) -> Optional[Callable[..., Dict[str, str]]]:
        if isinstance(node, ast.Call):
            func_name = self.__getNodeName(node.func)
            return self.__function_handlers.get(func_name, None)
        return None

    def __handle_len_function(
        self,
        node: ast.Compare,
        left: Optional[ast.expr],
        right: Optional[ast.expr],
        op: ast.cmpop,
        var_values: Dict[str, Optional[Any]],
    ) -> Dict[str, str]:
        left_node = node.left
        right_node = node.comparators[0]
        op_symbol = self.__getOperatorSymbol(op)

        if isinstance(left_node, ast.Call) and left_node.args:
            arg_node = left_node.args[0]
        else:
            raise ValueError("Invalid left node for len function")
        var_name = self.__getNodeName(arg_node)
        var_value = var_values.get(var_name, None)

        right_value = self.__evaluateNode(right_node)

        if right_value is None:
            raise ValueError("Invalid arguments for len function")
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

        if op_symbol in [">", ">="]:
            error_message = (
                f"[{var_name}] ({var_value}) is too short (min length {compare_value})!"
            )
        elif op_symbol in ["<", "<="]:
            error_message = (
                f"[{var_name}] ({var_value}) is too long (max length {compare_value})!"
            )
        else:
            error_message = (
                f"[{var_name}] ({var_value}) must be {op_symbol} {right_value}!"
            )

        return {var_name: error_message}

    def __handle_intersection_check(
        self,
        node: ast.Call,
        left: Optional[ast.expr],
        right: Optional[ast.expr],
        op: Optional[ast.cmpop],
        var_values: Dict[str, Optional[Any]],
    ) -> Dict[str, str]:
        args = node.args
        if len(args) < 2:
            return {"error": "Invalid arguments for intersectionCheck"}

        start_date_node = args[0]
        end_date_node = args[1]

        start_date_name = self.__getNodeName(start_date_node)
        end_date_name = self.__getNodeName(end_date_node)

        start_date_value = var_values.get(start_date_name, None)
        end_date_value = var_values.get(end_date_name, None)

        error_message = (
            f"[{start_date_name}] ({start_date_value}) and "
            f"[{end_date_name}] ({end_date_value}) must not overlap with existing ranges."
        )

        return {
            start_date_name: error_message,
            end_date_name: error_message,
        }

    def __getFunctionNode(self) -> Optional[ast.AST]:
        source = inspect.getsource(self.__func).strip()
        if source.startswith("@"):
            source = "\n".join(source.split("\n")[1:])
        tree = ast.parse(source)
        func_def = tree.body[0]
        if isinstance(func_def, ast.FunctionDef):
            return getattr(func_def.body[0], "value")
        elif isinstance(func_def, ast.Expr):
            return func_def.value
        return None

    def __evaluateNode(self, node: ast.AST) -> Optional[Any]:
        try:
            value = eval(
                compile(ast.Expression(body=node), "", "eval"),  # type: ignore
                {"x": self.__lastEvaluationInput},
                {},
            )
            return value
        except Exception:
            return None

    def __formatDisplay(self, name: str, value: Optional[Any], node: ast.AST) -> str:
        if isinstance(node, ast.Constant):
            return f"{value}"
        elif name in self.__variables:
            return f"[{name}] ({value})"
        else:
            return f"{value}"

    def __getOperatorSymbol(self, op: ast.cmpop) -> Optional[str]:
        operator_map = {
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
        }
        return operator_map.get(type(op), None)  # type: ignore

    def __getNodeName(self, node: ast.AST) -> str:
        if isinstance(node, ast.Attribute):
            names: list[str] = []
            current = node
            while isinstance(current, (ast.Attribute, ast.Subscript)):
                if isinstance(current, ast.Attribute):
                    names.append(current.attr)
                    current = current.value
                else:
                    current = current.value
            if isinstance(current, ast.Name):
                names.append(current.id)
            full_name = ".".join(reversed(names))
            if full_name.startswith("x."):
                full_name = full_name[2:]
            return full_name
        elif isinstance(node, ast.Name):
            return node.id if node.id != "x" else ""
        elif isinstance(node, ast.Constant):
            return ""
        elif isinstance(node, ast.Call):
            func_name = self.__getNodeName(node.func)
            args = [self.__getNodeName(arg) for arg in node.args]
            return f"{func_name}({', '.join(args)})"
        else:
            try:
                return ast.unparse(node)
            except:
                return str(node)
