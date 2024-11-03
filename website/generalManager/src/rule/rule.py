import ast
import inspect
import re


class Rule:
    def __init__(self, func, custom_error_message=None, ignore_if_none=True):
        self.__func = func
        self.__customErrorMessage = custom_error_message
        self.__variables = self.__extractVariables()
        self.__lastEvaluationResult = None
        self.__lastEvaluationInput = None
        self.__ignoreIfNone = ignore_if_none

        # Dispatch table for special function handlers
        self.__function_handlers = {
            "len": self.__handle_len_function,
            "intersectionCheck": self.__handle_intersection_check,
            # Add other function handlers here
        }

    # Property methods to access hidden attributes
    @property
    def func(self):
        return self.__func

    @property
    def customErrorMessage(self):
        return self.__customErrorMessage

    @property
    def variables(self):
        return self.__variables

    @property
    def lastEvaluationResult(self):
        return self.__lastEvaluationResult

    @property
    def lastEvaluationInput(self):
        return self.__lastEvaluationInput

    @property
    def ignoreIfNone(self):
        return self.__ignoreIfNone

    def evaluate(self, x):
        """Executes the rule function with the given input x."""
        self.__lastEvaluationInput = x
        var_values = self.__extractVariableValues(x)
        if self.__ignoreIfNone:
            if any(value is None for value in var_values.values()):
                self.__lastEvaluationResult = None
                return True
        self.__lastEvaluationResult = self.__func(x)
        return self.__lastEvaluationResult

    def getErrorMessage(self):
        """Generates error messages based on the last call to evaluate()."""
        if self.__lastEvaluationResult or self.__lastEvaluationResult is None:
            return None  # No error message needed
        else:
            x = self.__lastEvaluationInput
            var_values = self.__extractVariableValues(x)

            if self.__customErrorMessage:
                # Replace variables in {} with their values
                error_message_formatted = re.sub(
                    r"{([^}]+)}",
                    lambda match: str(var_values.get(match.group(1), match.group(0))),
                    self.__customErrorMessage,
                )
                error_messages = {
                    var: error_message_formatted for var in self.__variables
                }
                return error_messages
            else:
                error_messages = self.__generateErrorMessages(var_values)
                return error_messages

    def validateCustomErrorMessage(self):
        """Checks if the custom error message contains all variables."""
        if self.__customErrorMessage:
            # Extract variables in {} from the custom error message
            vars_in_message = set(re.findall(r"{([^}]+)}", self.__customErrorMessage))
            missing_vars = [
                var for var in self.__variables if var not in vars_in_message
            ]
            if missing_vars:
                raise ValueError(
                    "The custom error message does not contain all used variables."
                )

    # Internal methods (hidden)
    def __extractVariables(self):
        """Extracts variables used in the function."""
        source = inspect.getsource(self.__func).strip()
        # Remove decorators or comments
        if source.startswith("@"):
            source = "\n".join(source.split("\n")[1:])
        tree = ast.parse(source)

        class VariableExtractor(ast.NodeVisitor):
            def __init__(self):
                self.variables = set()
                self.function_names = set()

            def visit_FunctionDef(self, node):
                # Collect function names to exclude them from variables
                self.function_names.add(node.name)
                self.generic_visit(node)

            def visit_Name(self, node):
                # Exclude 'x', built-in names, and function names
                if (
                    node.id != "x"
                    and node.id not in dir(__builtins__)
                    and node.id not in self.function_names
                ):
                    if not isinstance(node.ctx, ast.Load):
                        return
                    if not self.__is_part_of_function_name(node):
                        self.variables.add(node.id)

            def visit_Attribute(self, node):
                # Extract attributes like x.attribute
                full_name = self._get_full_attribute_name(node)
                if full_name.startswith("x."):
                    full_name = full_name[2:]
                    self.variables.add(full_name)
                self.generic_visit(node)

            def visit_Call(self, node):
                # Collect function names to exclude them from variables
                if isinstance(node.func, ast.Name):
                    self.function_names.add(node.func.id)
                self.generic_visit(node)

            def __is_part_of_function_name(self, node):
                # Check if the name is part of a function call
                parent = getattr(node, "parent", None)
                return isinstance(parent, ast.Call) and parent.func == node

            def _get_full_attribute_name(self, node):
                names = []
                current = node
                while isinstance(current, (ast.Attribute, ast.Subscript)):
                    if isinstance(current, ast.Attribute):
                        names.append(current.attr)
                        current = current.value
                    elif isinstance(current, ast.Subscript):
                        current = current.value
                if isinstance(current, ast.Name):
                    names.append(current.id)
                return ".".join(reversed(names))

        # Annotate nodes with their parents to check context
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                child.parent = node  # type: ignore

        extractor = VariableExtractor()
        extractor.visit(tree)

        return list(self.__remove_duplicates_preserve_order(extractor.variables))

    def __remove_duplicates_preserve_order(self, seq):
        seen = set()
        return [x for x in seq if not (x in seen or seen.add(x))]

    def __extractVariableValues(self, x):
        """Extracts the values of variables from the object x."""
        var_values = {}
        for var in self.__variables:
            parts = var.split(".")
            value = x
            for part in parts:
                value = getattr(value, part)
                if value is None:
                    break
            var_values[var] = value
        return var_values

    def __extractComparisons(self):
        """Extracts comparison operations from the function."""
        source = inspect.getsource(self.__func).strip()
        if source.startswith("@"):
            source = "\n".join(source.split("\n")[1:])
        tree = ast.parse(source)

        class ComparisonExtractor(ast.NodeVisitor):
            def __init__(self):
                self.comparisons = []

            def visit_Compare(self, node):
                self.comparisons.append(node)
                self.generic_visit(node)

        comp_extractor = ComparisonExtractor()
        comp_extractor.visit(tree)
        return comp_extractor.comparisons

    def __containsLogicalOps(self):
        """Checks if the function contains logical operators like 'and' or 'or'."""
        source = inspect.getsource(self.__func).strip()
        if source.startswith("@"):
            source = "\n".join(source.split("\n")[1:])
        tree = ast.parse(source)

        class LogicalOpDetector(ast.NodeVisitor):
            def __init__(self):
                self.found = False

            def visit_BoolOp(self, node):
                if isinstance(node.op, (ast.And, ast.Or)):
                    self.found = True
                self.generic_visit(node)

        detector = LogicalOpDetector()
        detector.visit(tree)
        return detector.found

    def __generateErrorMessages(self, var_values):
        """Generates error messages based on the comparison operations."""
        error_messages = {}
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
                        # Check for special function handlers
                        handler = self.__get_handler(left)
                        if handler:
                            error_message = handler(node, left, right, op, var_values)
                            if error_message:
                                error_messages.update(error_message)
                            continue

                        left_name = self.__getNodeName(left)
                        right_name = self.__getNodeName(right)

                        # Evaluate left and right
                        left_value = self.__evaluateNode(left)
                        right_value = self.__evaluateNode(right)

                        # Generate left and right display
                        left_display = self.__formatDisplay(left_name, left_value, left)
                        right_display = self.__formatDisplay(
                            right_name, right_value, right
                        )

                        # Generate error message
                        error_message = (
                            f"{left_display} must be {op_symbol} {right_display}!"
                        )
                        # Assign error messages to variables
                        if left_name in var_values:
                            error_messages[left_name] = error_message
                        if right_name in var_values and right_name != left_name:
                            error_messages[right_name] = error_message

            # If logical operators are present and evaluation is False, generate combined error
            if logical_ops and not self.__lastEvaluationResult:
                combined_vars = ", ".join([f"[{var}]" for var in self.__variables])
                error_message = f"{combined_vars} combination is not valid"
                for var in self.__variables:
                    error_messages[var] = error_message
            return error_messages
        else:
            # Check if the entire function is a call to a special function
            func_node = self.__getFunctionNode()
            if func_node:
                handler = self.__get_handler(func_node)
                if handler:
                    error_message = handler(func_node, None, None, None, var_values)
                    if error_message:
                        return error_message

            # If no comparisons or special functions found
            if self.__variables:
                combined_vars = ", ".join([f"[{var}]" for var in self.__variables])
                error_message = f"{combined_vars} combination is not valid"
                for var in self.__variables:
                    error_messages[var] = error_message
                return error_messages
            else:
                return {"error": "An error occurred"}

    def __get_handler(self, node):
        """Returns a handler function if the node is a call to a special function."""
        if isinstance(node, ast.Call):
            func_name = self.__getNodeName(node.func)
            return self.__function_handlers.get(func_name, None)
        return None

    def __handle_len_function(self, node, left, right, op, var_values):
        """Handler for len() function in comparisons."""
        left_node = node.left
        right_node = node.comparators[0]
        op_symbol = self.__getOperatorSymbol(op)

        arg_node = left_node.args[0]
        var_name = self.__getNodeName(arg_node)
        var_value = var_values.get(var_name, None)

        # Evaluate right value
        right_value = self.__evaluateNode(right_node)

        # Adjust compare_value according to the operator
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
            compare_value = right_value  # default

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

    def __handle_intersection_check(self, node, left, right, op, var_values):
        """Handler for intersectionCheck function."""
        # Extract arguments from the function call
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

    def __getFunctionNode(self):
        """Extracts the main function node if the entire function is a call."""
        source = inspect.getsource(self.__func).strip()
        if source.startswith("@"):
            source = "\n".join(source.split("\n")[1:])
        tree = ast.parse(source)
        func_def = tree.body[0]
        if isinstance(func_def, ast.FunctionDef):
            return func_def.body[0].value  # type: ignore
        elif isinstance(func_def, ast.Expr):
            return func_def.value
        return None

    def __evaluateNode(self, node):
        """Evaluates an AST node to get its value."""
        try:
            value = eval(
                compile(ast.Expression(body=node), "", "eval"),
                {"x": self.__lastEvaluationInput},
                {},
            )
            return value
        except Exception:
            return None

    def __formatDisplay(self, name, value, node):
        """Formats the display of a variable and its value."""
        if isinstance(node, ast.Constant):
            # For constants, just display the value
            return f"{value}"
        elif name in self.__variables:
            return f"[{name}] ({value})"
        else:
            return f"{value}"

    def __getOperatorSymbol(self, op):
        """Returns the operator symbol for an AST operator."""
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
        return operator_map.get(type(op), None)

    def __getNodeName(self, node):
        """Extracts the name of a node in the AST."""
        if isinstance(node, ast.Attribute):
            names = []
            current = node
            while isinstance(current, (ast.Attribute, ast.Subscript)):
                if isinstance(current, ast.Attribute):
                    names.append(current.attr)
                    current = current.value
                elif isinstance(current, ast.Subscript):
                    current = current.value
            if isinstance(current, ast.Name):
                names.append(current.id)
            full_name = ".".join(reversed(names))
            if full_name.startswith("x."):
                full_name = full_name[2:]
            return full_name
        elif isinstance(node, ast.Name):
            if node.id != "x":
                return node.id
            else:
                return ""
        elif isinstance(node, ast.Constant):
            return ""
        elif isinstance(node, ast.Call):
            # For function calls, return the function name and arguments
            func_name = self.__getNodeName(node.func)
            args = [self.__getNodeName(arg) for arg in node.args]
            return f"{func_name}({', '.join(args)})"
        else:
            # For other nodes, use ast.unparse if available, else str(node)
            try:
                return ast.unparse(node)
            except:
                return str(node)
