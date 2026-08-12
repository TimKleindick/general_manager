# Issue 442: Async-Safe Class Subscription Permissions

## Problem

Class-wide GraphQL subscriptions hydrate each changed manager with
`asyncio.to_thread()`, then evaluate `can_read_instance()` synchronously on the
async event-loop thread. A permission implementation that performs Django ORM
work therefore raises `SynchronousOnlyOperation` and terminates the stream.

The existing subscription tests set `DJANGO_ALLOW_ASYNC_UNSAFE=true`, which
masks this failure mode.

## Design

Capture `info.context.user` once when the class-wide subscription starts. For
each identified event, run manager hydration and the object-level read check in
the same `asyncio.to_thread()` worker operation using that captured user.

The synchronous worker returns the hydrated manager only when
`can_read_instance()` grants access. A denied event returns no item and the
async stream continues waiting for later events. Expected hydration failures
retain their current behavior and silently suppress the affected event.

Keeping hydration and authorization in one worker operation ensures all ORM
work performed by either step is async-safe and avoids an extra event-loop to
worker transition. Capturing the user outside the event loop prevents later
changes to the GraphQL context from changing the identity used by a long-lived
subscription.

## Error Handling and Diagnostics

Unexpected exceptions raised while constructing or evaluating the permission
will be logged with the manager class and event action before being re-raised.
The existing subscription transport remains responsible for converting the
iteration failure into its established error and completion behavior. The log
must not include the full event identification or other potentially sensitive
object data.

Permission denials are expected control flow and will not be logged as errors.

## Compatibility

This change affects only identified events delivered through class-wide
subscriptions. Single-instance subscriptions, aggregate class `refresh` events
without identification, hydration-error suppression, and public GraphQL schema
shapes remain unchanged. No dependency or database-schema change is required.

## Testing

Extend the class-wide subscription coverage with a permission that performs a
real ORM lookup. Run the regression case with `DJANGO_ALLOW_ASYNC_UNSAFE`
removed, even though the surrounding legacy test fixture enables it.

The test will publish an unauthorized event followed by an authorized event and
assert that only the authorized event is delivered. Before the production
change, the first permission lookup must fail with `SynchronousOnlyOperation`;
after the change, the denied event is suppressed and the same stream delivers
the later authorized event.

Add focused diagnostic coverage to verify that an unexpected permission
exception is logged with non-sensitive manager/action context and still
propagates through the existing stream error path.

Validation will run the focused subscription tests first, followed by Ruff,
format checking, mypy, and the broader test suite as warranted by runtime.
