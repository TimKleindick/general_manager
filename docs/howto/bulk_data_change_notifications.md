# Batch Data-Change Notifications

Use `bulk_data_change_notifications()` when one operation updates many managers
and GraphQL or RemoteAPI clients should receive one aggregate refresh per
affected resource instead of one row-level event per write.

## Wrap the outermost transaction

Import the context from the stable API module and place it outside the true
outermost database transaction:

```python
from django.db import transaction

from general_manager.api import bulk_data_change_notifications
from myapp.managers import Project


with bulk_data_change_notifications():
    with transaction.atomic():
        for project in Project.filter(status="active"):
            project.update(status="archived")
```

The context is not transaction-aware. If another layer already encloses this
code in `transaction.atomic()`—including Django's `ATOMIC_REQUESTS`—place the
notification context outside that enclosing block so the flush follows the
actual commit or rollback rather than an intermediate savepoint.

## What clients receive

Inside the context, GraphQL and RemoteAPI delivery is deduplicated by target:

- GraphQL detail subscriptions receive `action = "refresh"` and rehydrate the
  subscribed item. Class-wide subscriptions receive `item = null` because the
  event identifies no row.
- RemoteAPI clients receive one websocket event per affected resource with
  `action = "refresh"` and `identification = null`. They should requery the
  resource over REST.

Each aggregate event includes a UUID4 `event_id`. The context does not reveal
which rows changed, how many changed, or the original row-level actions. Cache
invalidation and unrelated signal receivers still run for each write.

## Failure and nesting behavior

The body is always allowed to finish its normal exception behavior: an
exception raised by the body is re-raised after the queued flush is attempted.
Nested notification contexts join the already-active outer batch instead of
flushing independently. Outside the context, existing row-level notification
delivery remains unchanged.

For the full callable signature and exception contract, see the
[GraphQL API reference](../api/graphql.md#bulk-notification-context). The
[cookbook recipe](../examples/bulk_data_change_notifications.md) is a compact
version suitable for adapting into a service function.
