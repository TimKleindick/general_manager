# Rule Validation Fallback Design

## Context

GitHub issue #387 reports that a `Rule` returning `False` can be silently
accepted when `ignore_if_none=False` unless `custom_error_message` is set. The
evaluation itself is correct: the failure is lost when automatic AST-based
message generation returns an empty mapping. ORM and request-backed validation
only raise `ValidationError` when that mapping is non-empty.

The problem is broader than `None` handling. Any failed expression whose AST
cannot be mapped back to a referenced variable can reach the same path.
`ignore_if_none=False` makes the issue visible because it forces predicates to
evaluate when referenced values are `None`.

## Required Behavior

- A built-in `Rule` that evaluates to `False` always returns a non-empty error
  mapping from `get_error_message()`.
- Existing detailed AST-generated messages remain unchanged when generation
  succeeds.
- Existing custom messages remain unchanged.
- When detailed generation fails, every referenced variable receives a generic
  invalid-combination message.
- When the rule has no detectable referenced variables, the generic error uses
  Django's non-field error key.
- Rules returning `True` or `None` continue to return no error message.

## Design

Keep fallback policy inside `Rule`, which owns both predicate introspection and
message generation. Add one private helper that builds the generic mapping from
the rule's extracted variables. Reuse it when comparison-specific generation
returns no messages and when a custom message has no field keys to attach to.

The fallback message should retain the existing generic wording for referenced
variables: `"[field] combination is not valid"`. For rules without variables,
use `NON_FIELD_ERRORS` with `"Rule validation failed"`. Centralizing the
guarantee means both ORM and request-backed consumers receive a truthy mapping
without duplicating policy or changing their interfaces.

## Testing

Use test-driven development:

1. Change the existing wrapped-comparison unit test to expect a generic field
   message and run it to prove the current implementation fails.
2. Add a no-variable rule test that expects a Django non-field error and prove
   it fails.
3. Add ORM regression coverage using a real `Rule` with
   `ignore_if_none=False`, proving `full_clean` raises without a custom message.
4. Add request-interface regression coverage proving the same rule failure is
   raised before a request mutation proceeds.
5. Run focused rule, ORM-interface, and request-interface tests, followed by
   formatting, lint, typing, and the full test suite as warranted.

## Compatibility and Scope

This is a behavioral correction to match the documented contract that `False`
raises `ValidationError`. It does not change successful evaluation, skipped
`None` evaluation, custom handler dispatch, or detailed message formats. No new
dependencies or unrelated refactors are required.
