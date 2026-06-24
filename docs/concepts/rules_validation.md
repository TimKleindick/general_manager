# Rule Validation

Rules let you attach declarative validation to managers. They execute before data is written and generate detailed error messages when conditions fail.

## Defining rules

Create rules with the `general_manager.rule.Rule` helper. Rules accept a callable that receives the manager instance under validation and returns a boolean:

```python
from general_manager.rule import Rule

positive_price = Rule(lambda product: product.price > 0)
```

Use the generic form to declare the target manager type so that type checkers and IDEs understand the lambda body:

```python
Rule["Project"](lambda project: project.start_date < project.end_date)
```

## Attaching rules to interfaces

Attach rules inside the `Meta` class of an interface. Each rule runs during `create` and `update` operations.

```python
class Project(GeneralManager):
    ...

    class Interface(DatabaseInterface):
        ...

        class Meta:
            rules = [
                Rule["Project"](lambda x: x.total_capex >= "0 EUR"),
                Rule["Project"](
                    lambda x: x.end_date is None or x.start_date <= x.end_date
                ),
            ]
```

If a rule returns `False`, the interface raises `ValidationError`. Errors propagate to the GraphQL mutation response and include messages per attribute involved.

## Custom messages and `None` handling

Rules automatically generate helpful error messages by analysing the expression. Override the text with `custom_error_message` and use placeholders that match variable names:

```python
Rule(
    lambda order: order.quantity <= order.stock,
    custom_error_message="Ordered quantity ({quantity}) exceeds stock ({stock}).",
)
```

By default, rules ignore `None` values and return `None`. Set `ignore_if_none=False` when `None` should fail the validation.

## Manual evaluation

You can evaluate a rule in isolation, which is useful for unit tests:

```python
result = positive_price.evaluate(product)
if not result:
    print(positive_price.get_error_message())
```

## Custom rule handlers

For advanced scenarios, register additional rule handlers via the
`RULE_HANDLERS` Django setting. The setting is a list of dotted import paths to
`BaseRuleHandler` subclasses. GeneralManager instantiates each class at rule
construction time and stores it by `handler.function_name`; later handlers with
the same name replace earlier handlers, including earlier custom handlers and
the built-in handlers. `function_name` must resolve to a non-empty string after
instantiation. Invalid import paths raise the `ImportError` surfaced by
Django's `import_string()`,
non-`BaseRuleHandler` classes and invalid `function_name` values raise
`InvalidRuleHandlerConfigurationError`, and constructor exceptions from custom
handlers propagate unchanged.

Handlers are invoked while generating error messages after a rule has already
evaluated to `False`; they do not decide whether validation passes. A handler
receives the current comparison node plus the specific `left`, `right`, and
operator being explained. This matters for chained comparisons such as
`1 < len(name) < 5`, where the rule engine may call the same handler for the
`len(name) < 5` leg. Built-in function handlers expect the function call on the
selected left side. If a rule needs to explain a form such as `5 > len(name)`,
the caller must normalize the comparison before invoking the handler.

Built-in handlers support function calls on the comparison side they receive:

- `len(value)`: the threshold must be `int` or `float`, excluding `bool`.
  The runtime value is used only for display; missing variables and `None`
  display as `None` rather than raising.
- `sum(value)`, `max(value)`, and `min(value)`: the resolved value must be a
  non-empty `list` or `tuple` of `int` or `float`, excluding `bool`.

Other iterables such as sets, generators, and strings are rejected by the
aggregate handlers. Missing variables and `None` values are treated as invalid
aggregate inputs. Handler error messages are explanatory strings for failed
rules, not proof that the comparison itself evaluated in that direction.
The built-in function handlers require the selected left side to be a function
call. If it is not a call, they raise `InvalidFunctionNodeError`. Matching calls
must have exactly one positional argument and no keyword arguments; `len()`,
`len(value, other)`, and `len(value=value)` are malformed and raise the same
exception. A direct call to the wrong built-in handler, such as invoking
`SumHandler` for `len(value)`, returns `{}` because that comparison is outside
the handler's responsibility.
For aggregate handlers, the first argument name is obtained with
`rule._get_node_name(arg_node)` and the iterable is read from `var_values` under
that exact key; the argument expression is not otherwise evaluated. If
`_get_node_name()` cannot name the argument, that helper exception propagates.
Missing aggregate values, `None`, empty lists/tuples, and non-list/tuple values
raise `NonEmptyIterableError` with `"<function> expects a non-empty iterable."`.
Lists or tuples containing non-numeric values or bools raise
`NumericIterableError` with `"<function> expects an iterable of numbers."`.
Custom handlers should return `{}` when a node is outside their responsibility
and raise a clear exception when the node matches their function but the
arguments are malformed. Built-in handlers follow that split: non-comparison
nodes and mismatched functions return `{}`, while malformed matching function
calls or invalid aggregate inputs raise the documented handler errors.

The built-in message shapes are stable:

- `len(name) > n`: `[name] (value) is too short (min length n+1)!`
- `len(name) >= n`: `[name] (value) is too short (min length n)!`
- `len(name) < n`: `[name] (value) is too long (max length n-1)!`
- `len(name) <= n`: `[name] (value) is too long (max length n)!`
- `len(name) == n`: `[name] (value) must have a length of n!`
- `len(name) != n`: `[name] (value) must not have a length of n!`
- `sum(name) > n` and `sum(name) >= n`: `[name] (sum=current) is too small (op n)!`
- `sum(name) < n` and `sum(name) <= n`: `[name] (sum=current) is too large (op n)!`
- `sum(name) == n`: `[name] (sum=current) must be n!`
- `sum(name) != n`: `[name] (sum=current) must not be n!`

`max()` and `min()` use the same aggregate message pattern with `max=current` or
`min=current`. Other comparison symbols use the equality-style `must be`
message. Exceptions raised by the `rule` helper methods propagate unchanged from
the built-in handlers.

Implement a custom handler by subclassing `BaseRuleHandler`, defining a
non-empty string `function_name`, and returning a `dict[str, str]` from
`handle()`. The `rule` argument provides helper methods used by built-in handlers:
`_get_node_name(node) -> str`, `_eval_node(node) -> object | None`, and
`_get_op_symbol(op) -> str`.
