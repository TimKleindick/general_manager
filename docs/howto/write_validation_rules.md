# Write validation rules with reliable fallback errors

Use a `Rule` when validation depends on more than one manager attribute or when
the same predicate should run for both `create()` and `update()`.

## Define and attach a rule

Keep the predicate in source code that Python can inspect. Attach it through the
interface `Meta.rules` list:

```python
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.rule import Rule


class Booking(GeneralManager):
    starts_at: object
    ends_at: object
    code: str | None

    class Interface(DatabaseInterface):
        class Meta:
            rules = [
                Rule["Booking"](
                    lambda booking: booking.starts_at < booking.ends_at,
                    custom_error_message=(
                        "Start {starts_at} must be before end {ends_at}."
                    ),
                )
            ]
```

The placeholders in `custom_error_message` must match variables referenced by
the predicate. Missing placeholders raise `MissingErrorTemplateVariableError`
when the message is generated.

## Choose how `None` behaves

The default `ignore_if_none=True` makes `evaluate()` return `None` when a
referenced value is `None`; skipped rules contribute no validation error. Use
`ignore_if_none=False` when absence must fail:

```python
required_code = Rule(
    lambda booking: booking.code is not None,
    custom_error_message="A booking code is required; received {code}.",
    ignore_if_none=False,
)
```

## Test both the result and message

```python
from types import SimpleNamespace

booking = SimpleNamespace(code=None)

assert required_code.evaluate(booking) is False
assert required_code.get_error_message() == {
    "code": "A booking code is required; received None."
}
```

As of 0.62.2, every failed rule produces a non-empty mapping. If a predicate
cannot be explained by a registered AST handler, referenced fields receive a
generic combination error. A variable-free predicate uses Django's non-field
error key, `"__all__"`. Custom messages are preserved in either fallback.

Do not call `get_error_message()` as the first operation: evaluate the rule
first. Passing and skipped evaluations return `None` from
`get_error_message()`.

For the model and fallback behavior, read [Rule Validation](../concepts/rules_validation.md).
For copy-ready tests, use the [rule-validation cookbook](../examples/rule_validation.md).
The [API reference](../api/core.md#general_manager.rule.rule.Rule) documents the
constructor, return values, and exceptions.
