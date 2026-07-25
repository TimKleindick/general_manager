# Validate a manager with field and non-field rule errors

This recipe exercises the public `Rule` API directly and is suitable for a unit
test. Defining named functions keeps their source available to `inspect`.

```python
from dataclasses import dataclass

from django.core.exceptions import NON_FIELD_ERRORS

from general_manager.rule import Rule


@dataclass
class Project:
    name: str | None


@dataclass
class Booking:
    starts_at: int
    ends_at: int
    project: Project | None = None


def ordered(booking: Booking) -> bool:
    return booking.starts_at < booking.ends_at


field_rule = Rule(
    ordered,
    custom_error_message="Start {starts_at} must be before the end.",
)

assert field_rule.evaluate(Booking(starts_at=20, ends_at=10)) is False
assert field_rule.get_error_message() == {
    "starts_at": "Start 20 must be before the end.",
    "ends_at": "Start 20 must be before the end.",
}


def maintenance_window_closed(_booking: Booking) -> bool:
    return False


non_field_rule = Rule(
    maintenance_window_closed,
    custom_error_message="Bookings are temporarily disabled.",
    ignore_if_none=False,
)

assert non_field_rule.evaluate(Booking(starts_at=1, ends_at=2)) is False
assert non_field_rule.get_error_message() == {
    NON_FIELD_ERRORS: "Bookings are temporarily disabled."
}


def project_selected(booking: Booking) -> bool:
    return booking.project is not None


dotted_rule = Rule(
    project_selected,
    custom_error_message="A project is required; received {project.name}.",
    ignore_if_none=False,
)

assert dotted_rule.evaluate(Booking(starts_at=1, ends_at=2, project=None)) is False
assert dotted_rule.get_error_message() == {
    "project": "A project is required; received None."
}
```

The first custom template intentionally covers only `starts_at`; placeholders
are optional and do not change the existing `starts_at` and `ends_at` error
keys. The second message is static. The last rule demonstrates a dotted
attribute path without calling a method. With `ignore_if_none=False`, an
intermediate or final `None` renders as `"None"` when the failed rule is
formatted.

Only dot-separated Python identifiers are supported in placeholders. Syntax and
predicate roots are validated at rule construction, while paths are validated
against declared manager schemas during shared manager startup. Invalid syntax,
unrelated roots, and unknown or non-traversable paths raise
`InvalidErrorTemplateError`. Calls, indexes, conversions, format
specifications, filters, arbitrary expressions, and literal-brace escaping are
unsupported. Passing and skipped rules return no message.

`MissingErrorTemplateVariableError` remains available from
`general_manager.rule.rule` for compatibility, but a template may omit
predicate variables without raising it.

The guaranteed non-empty fallback was added in 0.62.2. Earlier versions could
return `None` after a failed predicate when no AST handler produced a message.

See the [concept page](../concepts/rules_validation.md),
[task guide](../howto/write_validation_rules.md), and
[API reference](../api/core.md#general_manager.rule.rule.Rule).
