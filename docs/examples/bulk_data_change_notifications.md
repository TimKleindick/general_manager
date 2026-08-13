# Bulk Data-Change Notification Recipe

This recipe batches a multi-row write while preserving the transaction boundary
seen by GraphQL and RemoteAPI clients:

```python
from django.db import transaction

from general_manager.api import bulk_data_change_notifications
from myapp.managers import Project


def archive_active_projects() -> None:
    with bulk_data_change_notifications():
        with transaction.atomic():
            for project in Project.filter(status="active"):
                project.update(status="archived")
```

The outer context emits one `refresh` event per affected GraphQL manager class
or RemoteAPI resource after the transaction block exits. Each event has
`identification = null` and a UUID4 `event_id`; clients should refetch their
resource or rehydrate their subscribed item. The context does not replace
cache invalidation or other per-write signals.

Keep the notification context outside any pre-existing outer transaction. It
flushes queued targets even when the body raises, then re-raises the body
exception. If both the body and notification flush fail, the context raises a
`BaseExceptionGroup` containing both failures. Ordinary channel-layer send
errors are logged while other queued targets continue flushing.

To observe the surrounding ORM transaction lifecycle, register a started
receiver and collect changed classes from the ordinary post-change signal:

```python
from general_manager.cache.data_change_context import register_data_change_class
from general_manager.cache.signals import (
    data_change_transaction_started,
    post_data_change,
)


def started(sender, transaction_context, **kwargs):
    transaction_context.metadata["batch"] = begin_coordination()


def changed(sender, database_alias, **kwargs):
    register_data_change_class(sender.__name__, database_alias)


data_change_transaction_started.connect(started, weak=False)
post_data_change.connect(changed, weak=False)
```

`data_change_transaction_finished` reports `committed` for GeneralManager's
own block or savepoint and `rolled_back` for failures. When the caller owns an
outer transaction, register durable completion work with
`transaction.on_commit(using=database_alias)`; the lifecycle's `committed`
outcome alone does not prove that outer transaction committed.
