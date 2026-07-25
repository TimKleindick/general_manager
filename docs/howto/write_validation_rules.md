# Write validation rules with reliable fallback errors

Use a `Rule` when validation depends on more than one manager attribute or when
the same predicate should run for both `create()` and `update()`.

## Define and attach a rule

Keep the predicate in source code that Python can inspect. Attach it through the
interface `Meta.rules` list:

```python
from django.db.models import CASCADE, CharField, ForeignKey, IntegerField

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.rule import Rule


class Project(GeneralManager):
    name: str

    class Interface(DatabaseInterface):
        name = CharField(max_length=200)


project_required = Rule(
    lambda booking: booking.project is not None,
    custom_error_message="A project is required; received {project.name}.",
    ignore_if_none=False,
)


class Booking(GeneralManager):
    starts_at: int
    ends_at: int
    code: str | None
    project: Project | None

    class Interface(DatabaseInterface):
        starts_at = IntegerField()
        ends_at = IntegerField()
        code = CharField(max_length=100, null=True)
        project = ForeignKey(
            Project.Interface._model,
            on_delete=CASCADE,
            null=True,
        )

        class Meta:
            rules = [
                Rule["Booking"](
                    lambda booking: booking.starts_at < booking.ends_at,
                    custom_error_message=(
                        "Booking must end after {starts_at}."
                    ),
                ),
                project_required,
            ]
```

Placeholders are optional. A template may use only a subset of the attributes
referenced by the predicate, as above, or may be entirely static. The formatted
message is still attached to the predicate's existing error fields, so this
example reports the same message for both `starts_at` and `ends_at`.

## Choose how `None` behaves

The default `ignore_if_none=True` makes `evaluate()` return `None` when a
referenced value is `None`; skipped rules contribute no validation error. Use
`ignore_if_none=False` when absence must fail:

```python
required_code = Rule(
    lambda booking: booking.code is not None,
    custom_error_message="A booking code is required.",
    ignore_if_none=False,
)
```

## Test both the result and message

```python
from types import SimpleNamespace

booking = SimpleNamespace(code=None)

assert required_code.evaluate(booking) is False
assert required_code.get_error_message() == {
    "code": "A booking code is required."
}

booking_without_project = SimpleNamespace(project=None)

assert project_required.evaluate(booking_without_project) is False
assert project_required.get_error_message() == {
    "project": "A project is required; received None."
}
```

## Use dotted placeholders for manager relations

A placeholder may follow declared single-manager relations using only
dot-separated Python identifiers. In the attached `project_required` rule
above, `{project.name}` has the predicate root `project`, and
`Booking.Interface.project` declares a single-manager relation. Shared manager
startup therefore discovers the rule and validates `project.name` against the
`Booking` and `Project` schemas. When the predicate fails because `project` is
`None`, formatting stops at that intermediate value and renders `"None"`. A
final `None`, such as a project whose `name` is `None`, renders the same way.

Placeholder syntax and roots are validated when the rule is constructed.
Dotted paths such as `project.name` are validated against the declared manager
schemas during shared manager startup. Invalid syntax, unrelated roots, unknown
fields, scalar traversal, and collection traversal raise
`InvalidErrorTemplateError`.

Templates do not evaluate Python. Calls, indexes, conversion and format
specifications, filters, arbitrary expressions, and literal-brace escaping are
unsupported. In particular, `{project.name}` reads an attribute; it never calls
a method.

The deprecated `MissingErrorTemplateVariableError` remains importable from
`general_manager.rule.rule` for compatibility. A template no longer has to
mention every predicate variable, so omission does not raise that exception.

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
