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

## Opt into bulk SQL

The default remains the canonical path. Managers can declare that their create
validation and observers are safe to evaluate for a whole batch:

```python
class Product(GeneralManager):
    class BulkCreate:
        enabled = True
        local_rules = True
        local_permissions = True
        local_search = True

    # Define Interface, Permission, and SearchConfig as usual.
```

These declarations do not disable checks. Every record still passes its create
permission gate, normalization, scalar validation, and configured local rules.
They declare that application predicates and search target resolvers are pure
and do not depend on earlier records having been inserted. They must not mutate the input,
install receivers, perform nested writes, or produce external effects. Do not
opt in a rule or permission that
queries a running total, allocates a sequence by reading existing rows, or needs
per-record database visibility.
Python field defaults and validators must follow the same locality contract;
leave SQL disabled when they depend on earlier inserts or changing database state.

Use `bulk_create_eligibility(Product)` to inspect selection before importing.
Its immutable `BulkCreateEligibility` result contains `eligible` and `reasons`.
Ineligible managers retain canonical persistence. Dynamic custom receivers and
unsupported model behavior must not be bypassed to force SQL batching.

The SQL path is deliberately conservative:

| Surface | SQL eligibility |
| --- | --- |
| Persistence | Canonical ORM capabilities, constructors, model save/clean methods, and ordinary model managers |
| Database | Default database, no custom routers, and backend support for returning bulk-inserted IDs |
| Fields | Supported built-in scalar and foreign-key fields; no files or many-to-many fields |
| Validation | Local rules and permissions; simple field/composite uniqueness; unsupported constraint, collation, or custom-field behavior falls back |
| History | Default database-aware history configuration without custom attribution, timestamps, persistence, or history receivers |
| Observers | Known framework receivers and public batch refresh registrations; arbitrary row or transaction-lifecycle receivers fall back |

Eligibility is checked again after consuming each batch, so a receiver connected
between yielded batches cannot silently lose its events. Database constraints
remain authoritative under concurrent inserts. A database failure that cannot
be associated with one row retains the batch range and original exception;
`failure_index` may be `None`.

SQL imports accept concrete field assignments. A payload assigning model methods
or other non-field attributes raises an indexed `CreateManyUnsupportedError`;
disable `BulkCreate.enabled` to retain canonical attribute-assignment behavior.
Database-generated defaults, custom collations, and foreign keys using
`to_field` or `limit_choices_to` currently require the canonical fallback.
An import started inside another import's callback also uses the canonical path
to preserve the enclosing mutation's savepoint and per-row callback semantics.

Dependency-cache invalidation is conservative at the SQL batch boundary. It may
evict more cached queries than an individual-row change would, but it runs before
observers read the new rows. Related search rules are selected once per changed
manager class in each batch; target resolution still runs for every record.

## Application refresh receivers

`connect_batch_refresh_receiver` provides a public registration point for
application cache invalidation. It returns a `BatchRefreshDisconnect` handle;
call its `disconnect()` method to remove only that registration.

```python
from django.core.cache import cache
from general_manager import connect_batch_refresh_receiver


def refresh_products(sender, identifications, action, database_alias):
    if sender is Product:
        # A generation bump invalidates application-owned derived cache keys.
        cache.add("products:generation", 0, timeout=None)
        cache.incr("products:generation")


registration = connect_batch_refresh_receiver(refresh_products, on_commit=True)
```

The callback receives the manager class, a tuple of full identification mappings,
the mutation action, and the database alias. Each mapping is a read-only snapshot
taken when the mutation signal arrives. ORM identities look like `{"id": 42}`;
request-backed identities may instead contain several keys, such as
`{"tenant": "acme", "code": "widget"}`. All keys are preserved, including when
callbacks wait until commit. A missing identification mapping raises `TypeError`
rather than delivering an unusable `None` identifier. By default it runs once for
an eligible bulk SQL batch, inside the transaction before iteration returns.
Ordinary mutations and canonical fallback retain per-record timing. This callback is a refresh/invalidation notification;
it does not replace audit rows or per-record workflow events. A rollback may
leave a conservative extra cache invalidation, so callbacks must not interpret
this phase as proof of durable persistence.

Use `on_commit=True` when registering an external refresh callback. Django then
delays it until the owning transaction commits and discards it on rollback.
Failures follow the existing post-commit error contract. Existing arbitrary
row-level signal receivers retain their semantics through canonical fallback.

The shared-cache example uses commit timing deliberately: an applied-only
generation bump can let another connection rebuild old committed data before
the import commits. Cache readers should capture the generation before querying
and store results under that captured generation. During write transactions,
applications must bypass their own shared derived cache or use a transaction-local
cache; never publish uncommitted derived values into shared storage. Framework
cache guarantees do not automatically extend to application-owned caches.

Applications can register separate applied and commit callbacks when they need
both transaction-local invalidation and shared-cache refresh. Invalidations must
be safe to repeat, and the commit callback must still advance the shared
generation. This timing API does not itself implement a cache publication barrier.

## Supported create behavior

The canonical fallback uses per-record persistence within each batch. Eligible
opted-in managers use SQL bulk insertion for source records and their history.
The SQL path validates before insertion and obtains IDs from the database's
returned rows. Framework consumers that need a manager receive one backed by
the persisted row, without a per-record database readback. Returned results
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

## Original canonical-batch benchmark

The following measurements describe the original canonical batching in PR #507.
For the PostgreSQL/Redis SQL bulk comparison, see
[Benchmark bulk imports](benchmark_bulk_imports.md).

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
