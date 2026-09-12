# Create records in bounded batches

`Manager.create_many()` imports an iterable through the ORM create path. It
preserves create permissions, normalization, model validation and business rules,
model save hooks, history, and per-record lifecycle events. Each batch is atomic:
a failed record rolls back the other records and history in that batch.

## Consume the result iterator

```python
from myapp.managers import Product

records = ({"sku": f"SKU-{number}", "name": f"Product {number}"} for number in range(100_000))
for batch in Product.create_many(records, creator_id=user.pk, batch_size=1000):
    print(batch.start_index, batch.end_index, batch.ids)
```

Calling `create_many()` returns an iterator. Writes happen when you advance it;
consume it with a `for` loop even when you do not need the IDs. It consumes at
most `batch_size` records before writing the batch and yields its result before
reading the next batch. `end_index` is exclusive and indices start at zero for
the supplied iterable. Stopping iteration between batches leaves later records
unconsumed and unwritten. Do not call `list()` on the results for a large import
unless you intend to retain all returned IDs.

`batch_size` defaults to 1000 and must be a positive integer; booleans are not
accepted; invalid sizes raise `CreateManyInvalidBatchSizeError`, a `ValueError`.
`creator_id`, `history_comment`, and `ignore_permission` apply to every
record. Record mappings contain model fields, not these reserved operation
options. Permission checks run for every record unless you explicitly pass
`ignore_permission=True`, as with `create()`.

## Transactions and restart positions

Without a caller-owned transaction, each yielded batch is committed and earlier
successful batches survive a later failure. Use `batch.end_index` as a restart
position only when `batch.committed` is true. See the
[checkpoint and restart example](../examples/create_many.md).

Inside an existing transaction, batch results have `committed=False`. They mean
that the batch savepoint succeeded, not that the data is durable:

```python
from django.db import transaction

with transaction.atomic(using="default"):
    for batch in Product.create_many(records, creator_id=user.pk):
        assert not batch.committed
    # An exception here rolls back every batch in this transaction.
```

Notifications and workflow handlers wait for the outer commit. A result is an
immutable snapshot; its `committed` flag does not change later. Decide whether
to checkpoint after the outer transaction completes using your application's
transaction outcome. Once a result is provisional, exhaust the iterator within
that same caller transaction and savepoint scope. Advancing it after leaving
that scope (including rollback, commit, or entry into another atomic block)
raises `CreateManyError` with a `CreateManyUnsupportedError` cause before
consuming more input. This prevents a later checkpoint from skipping rows that
an earlier caller savepoint rolled back. Use `atomic()` for caller scopes;
low-level manual savepoint manipulation is outside this contract.

Manually disabling autocommit outside `transaction.atomic()` is unsupported
and rejected before consuming the next batch. A hook that marks a transaction
for rollback without raising also fails the batch; it cannot produce a
successful committed result.

Working input and results are bounded by one batch. An outer transaction can
retain Django commit callbacks for every pending batch until it finishes, and
database transaction resources also grow with its size. Use independently
committed batches when bounded import memory is required.

## Failures

Catch `CreateManyPostCommitError` before `CreateManyError`, since it is a
subclass. A normal batch failure preserves the original exception in `cause`
and through exception chaining. `failure_index` identifies the absolute input
record when known; `batch_start_index` and `batch_end_index` identify the batch.
Django validation errors retain their field errors on `error.cause.message_dict`.
Uniqueness is checked against earlier input rows and persisted data, and database
constraints remain authoritative for concurrent inserts. Conflicts are never
ignored.

A post-commit failure means the batch has already persisted. Its `ids` identify
the committed rows; do not retry those rows as though they had rolled back.
Investigate the failed dispatch and reconcile its effects. Built-in search and
notification handlers keep their existing best-effort error handling; logged
backend failures do not become a false write rollback. If an outer transaction
owns the commit, errors from its callbacks surface when that outer transaction
exits rather than during iteration.

Input iterator failures also carry progress information. A partially consumed
batch is not written when reading its input fails. Repair the source and restart
from the last durable batch boundary, including records consumed from the failed
batch. `successful_count` counts successful batch savepoints, including provisional
ones. `committed_successful_count` counts known committed records and
`pending_successful_count` counts results awaiting an outer transaction outcome.
These are snapshots, not a guarantee that a caller's outer transaction committed.

## Supported create behavior

The initial implementation uses canonical per-record persistence within each
batch. It shares work that can be batched without skipping validation; it does
not replace model saves with Django `bulk_create()`. Managers may still be
constructed for framework signals, search, and workflow hooks. Returned results
contain IDs rather than retained manager objects.

Writable ORM interfaces, ordinary foreign-key normalization, business rules,
history attribution and many-to-many writes use this correctness-preserving
path. Request, Excel, read-only, and other non-writable interfaces are outside
this API. Custom manager/interface create overrides, custom create capabilities, upload
claims, and file objects requiring storage writes are rejected rather
than silently bypassed. Historical mutations remain forbidden.

Set `Interface.database` explicitly for non-default Django database routing.
Implicit routes that would write outside the batch database are rejected,
including a route changed by model validation. Many-to-many through rows must
use that same database. When custom routers are configured, existing models
must use `DatabaseAwareHistoricalRecords` so their required history cannot be
routed independently. Generated ORM models already use this tracker.

When the built-in workflow bridge uses `DatabaseEventRegistry`, the source must
be the default database and workflow dispatch must be asynchronous. Both
`WorkflowEventRecord` and `WorkflowOutbox` must route writes to that same database.
That keeps the required outbox records in the same transaction as the imported
rows. Custom durable registry subclasses, synchronous durable dispatch, and a
non-default source database are explicitly unsupported combinations.
In-memory workflow handlers run once per record after
the source commit. Arbitrary application signal receivers must use
`transaction.on_commit()` themselves for external I/O.

Dependency-cache eviction remains immediate so reads within a transaction do
not reuse stale values. GraphQL and RemoteAPI refresh notifications can be
coalesced after commit. Search invalidation retains all related targets and its
existing dirty-index recovery behavior. Audit history and workflow events remain
per-record; they are not replaced by one aggregate event.

## Reproduce the benchmark

Run `scripts/benchmark_create_many.py` from the repository with the development
dependencies installed. It creates and destroys a Django test database; use a
dedicated database account with permission to create test databases. Configure
`GENERAL_MANAGER_TEST_DATABASE=postgresql` and the
`GENERAL_MANAGER_TEST_DATABASE_NAME`, `_USER`, `_PASSWORD`, `_HOST`, and `_PORT`
environment variables for that account, then run:

```bash
python scripts/benchmark_create_many.py --rows 1000 --batch-size 100 --repeats 3
```

Both paths use the same generated records, unique field, shared foreign key,
creator, history comment, workflow handler, and search configuration. Both use
the explicit permission bypass; permission behavior is covered separately by
integration tests. Search discovery and invalidation planning remain enabled,
while external search dispatch is replaced identically for both paths. A fresh
workflow registry is installed outside every measured execution, and path order
alternates across repetitions. Assertions verify stored rows, uniqueness,
foreign keys, creator/history attribution, and per-record workflow counts.

A local run on Python 3.12.4, Django 5.2.16, and PostgreSQL 18.6 produced these
means over three repetitions. Elapsed time, SQL statements, and `tracemalloc`
peak allocations are collected in separate passes so tracing does not distort
the elapsed measurement.

| Path | Unprofiled elapsed | SQL statements | Peak Python bytes |
| --- | ---: | ---: | ---: |
| Repeated `create()` | 10.210 s | 16,003 | 574,986 |
| `create_many()`, batch size 100 | 9.059 s | 11,053 | 729,684 |

This workload used 30.9% fewer SQL statements and 11.3% less elapsed time.
Batch callback retention increased peak Python allocation; the input is still
consumed one bounded batch at a time. These figures exclude database/server
memory and do not predict production throughput. Model validation, application
hooks, batch size, and database latency affect the result. The benchmark has no
timing threshold and does not claim the performance of Django `bulk_create()`.
