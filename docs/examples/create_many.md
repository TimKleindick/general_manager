# Checkpoint and restart a batched import

Use a stable input ordering and checkpoint only successful, durable batches.
This example reads a CSV again after a failure, skipping rows covered by the
last saved checkpoint. `Product` is an application manager with `sku` and `name`
fields; `load_checkpoint` and `save_checkpoint` are application-owned functions.

```python
import csv
from itertools import islice

from general_manager import CreateManyError, CreateManyPostCommitError
from myapp.imports import load_checkpoint, save_checkpoint
from myapp.managers import Product


def import_products(path, user_id):
    offset = load_checkpoint(path)
    with open(path, newline="", encoding="utf-8") as source:
        rows = csv.DictReader(source)
        remaining = islice(rows, offset, None)
        try:
            for batch in Product.create_many(
                remaining,
                creator_id=user_id,
                history_comment="Product CSV import",
                batch_size=1000,
            ):
                if not batch.committed:
                    raise RuntimeError("This importer requires independent batch commits")
                # Batch indices are relative to `remaining`, so add the offset.
                save_checkpoint(path, offset + batch.end_index)
        except CreateManyPostCommitError as error:
            # The current batch persisted. Reconcile these IDs and the failed
            # dispatch before updating the checkpoint or attempting a restart.
            print("Committed IDs requiring reconciliation:", error.ids)
            raise
        except CreateManyError as error:
            input_index = (
                None if error.failure_index is None else offset + error.failure_index
            )
            print("Failed input index:", input_index, "cause:", error.cause)
            raise
```

Use a source-specific checkpoint key or fingerprint: changing the file or its
ordering invalidates a saved offset. A checkpoint write can fail after the batch
commits, and a process can stop between those two operations. For crash-safe
restart, reconcile the source's unique import keys against persisted rows or
store an import checkpoint transactionally with application-specific import
state. `create_many()` does not provide upsert or silently skip duplicates.
Blindly restarting from an outdated external checkpoint can encounter conflicts.

If a record fails, fix the source and retry from the last durable checkpoint,
not from the failing record alone: all records in its batch rolled back. If you
wrap the import in an outer transaction, save its checkpoint only after that
transaction commits; the example above intentionally rejects that usage.
