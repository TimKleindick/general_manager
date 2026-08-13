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

GraphQL and RemoteAPI receive `post_data_change` immediately, but their
websocket publishers schedule delivery with `transaction.on_commit()` on the
changed manager's database alias. With the order above, the outermost commit
runs those callbacks while the notification context remains open; its exit then
flushes one aggregate refresh per target. A rollback, including a savepoint
rollback, discards the callbacks, so no refresh enters the batch.

If another layer already encloses this code in `transaction.atomic()`—including
Django's `ATOMIC_REQUESTS`—place the notification context outside that
enclosing block so it remains open through the actual commit rather than an
intermediate savepoint.

The reverse order is still commit-safe but does not guarantee aggregation:

```python
with transaction.atomic():
    with bulk_data_change_notifications():
        for project in Project.filter(status="active"):
            project.update(status="archived")
```

Here the batch closes before the outer transaction runs its commit callbacks,
so those callbacks publish ordinary row-level notifications instead of queuing
aggregate refreshes.

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
invalidation and unrelated signal receivers still run for each write. For
ordinary identified row-level GraphQL class events, subscriptions hydrate the
changed object and call `can_read_instance()` only after commit, so a create
cannot race pre-commit hydration. Aggregate batch `refresh` events have no
identification, yield `item = null`, and are exempt from object-level permission
hydration and checking. RemoteAPI delivery after commit is best-effort:
unavailable channel layers produce no message, and channel-layer failures are
logged rather than raised.

## Nesting behavior

Nested notification contexts join the already-active outer batch instead of
flushing independently. Outside the context, existing row-level notification
delivery remains commit-bound and rollback-safe.

## Observe the data-change lifecycle

Use the lifecycle signals when an integration needs to coordinate work around
the outermost ORM mutation, rather than around every nested `pre_data_change`
or `post_data_change` callback:

```python
from django.db import transaction

from general_manager.cache.data_change_context import register_data_change_class
from general_manager.cache.signals import (
    data_change_transaction_started,
    post_data_change,
)


def on_transaction_started(sender, transaction_context, database_alias, **kwargs):
    transaction_context.metadata["consumer"] = begin_coordination()
    if transaction_context.caller_in_atomic_block:
        transaction.on_commit(
            lambda: finish_coordination(transaction_context.metadata["consumer"]),
            using=database_alias,
        )


def on_manager_changed(sender, database_alias, **kwargs):
    register_data_change_class(sender.__name__, database_alias)


data_change_transaction_started.connect(on_transaction_started, weak=False)
post_data_change.connect(on_manager_changed, weak=False)
```

`data_change_transaction_finished` reports `committed` when GeneralManager's
own block or savepoint exits successfully and `rolled_back` otherwise. If the
caller already owns the outer transaction, `committed` is not the durable
commit; use `transaction.on_commit(using=database_alias)` as shown. Nested
same-alias mutations share one lifecycle context, and
`transaction_context.changed_classes` deduplicates the classes registered by
the post-change receiver.

For the full callable signature and exception contract, see the
[GraphQL API reference](../api/graphql.md#bulk-notification-context). The
[cookbook recipe](../examples/bulk_data_change_notifications.md) is a compact
version suitable for adapting into a service function.
