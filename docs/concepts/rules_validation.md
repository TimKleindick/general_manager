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

For advanced scenarios, register additional rule handlers via the `RULE_HANDLERS` Django setting. Each handler customises error message extraction for custom or built-in functions such as `len()`, `sum()`, `max()`, or `min()`.
