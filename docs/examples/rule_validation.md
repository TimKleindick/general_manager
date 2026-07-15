# Validate a manager with field and non-field rule errors

This recipe exercises the public `Rule` API directly and is suitable for a unit
test. Defining named functions keeps their source available to `inspect`.

```python
from dataclasses import dataclass

from django.core.exceptions import NON_FIELD_ERRORS

from general_manager.rule import Rule


@dataclass
class Booking:
    starts_at: int
    ends_at: int


def ordered(booking: Booking) -> bool:
    return booking.starts_at < booking.ends_at


field_rule = Rule(
    ordered,
    custom_error_message="Start {starts_at} must be before end {ends_at}.",
)

assert field_rule.evaluate(Booking(starts_at=20, ends_at=10)) is False
assert field_rule.get_error_message() == {
    "starts_at": "Start 20 must be before end 10.",
    "ends_at": "Start 20 must be before end 10.",
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
```

The guaranteed non-empty fallback was added in 0.62.2. Earlier versions could
return `None` after a failed predicate when no AST handler produced a message.

See the [concept page](../concepts/rules_validation.md),
[task guide](../howto/write_validation_rules.md), and
[API reference](../api/core.md#general_manager.rule.rule.Rule).
