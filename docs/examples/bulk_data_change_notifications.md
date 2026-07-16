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
exception. Ordinary channel-layer send errors are logged while other queued
targets continue flushing.
