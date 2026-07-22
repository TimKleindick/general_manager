# Historical Execution Context

GeneralManager can read ORM-backed data as it existed at one point in time.
The historical execution context makes that point-in-time choice once for an
operation, then carries it through compatible managers, relations, calculations,
GraphQL resolvers, and cache lookups.

Use the context for reports, audits, exports, and other reads that must not mix
current values with values from a historical snapshot. It complements the
existing explicit `search_date` argument on ORM managers and buckets: callers
can still bind one object or query explicitly, while `as_of(...)` removes the
need to thread the same date through every nested read.

## One snapshot per operation

The context stores one timezone-aware `datetime` in a Python context variable.
Date-only values and naive datetimes use Django's current timezone; aware
datetimes retain their timezone. ISO strings, including strings ending in `Z`,
are accepted.

```python
from general_manager.api import as_of, current_as_of_date
from myapp.managers import Project

with as_of("2022-01-01T00:00:00Z") as snapshot:
    assert current_as_of_date() == snapshot
    projects = Project.filter(status="active")
    names = [project.name for project in projects]
```

Nested contexts and explicit `search_date` values are allowed only when they
represent the same instant. A different instant raises
`HistoricalContextConflictError` before the conflicting read can run. Managers
and lazy buckets keep their effective snapshot and perform the same check when
they are later materialized, so a current object cannot silently enter a
historical result.

The context follows normal `contextvars` behavior: child async tasks inherit
the snapshot, unrelated threads do not, and leaving the context restores the
previous value even when the operation raises.

## Which interfaces participate

Historical behavior is deliberately explicit and fail-closed:

| Interface family | Historical behavior |
| --- | --- |
| `DatabaseInterface`, `ExistingModelInterface`, and `ReadOnlyInterface` | Resolve scalar and relation reads from history at the effective date. |
| `CalculationInterface` | Transparent to the context; its manager dependencies and properties use the active snapshot. |
| `RequestInterface`, `RemoteManagerInterface`, and unsupported custom interfaces | Reject reads with `HistoricalReadNotSupportedError` before external loading or another current-data fallback. |

Historical relation traversal retains the source snapshot. For many-to-many
fields, both membership and target rows must have usable history; a missing
history path fails closed instead of returning current relation data.

## History completeness

Scalar historical reads use the configured django-simple-history records. For
generated ORM models and auto-registered existing models, declared local
many-to-many fields also receive history tracking. Deploy the generated
`Historical<Model>_<field>` through tables through the normal Django migration
workflow before relying on historical membership. Membership before those
tables existed cannot be reconstructed from scalar history alone.

The latest history row at or before the snapshot wins. Equal
`history_date` values are resolved by the greatest `history_id`, and deletion
rows are excluded from collection snapshots. Configured database aliases are
honored for source membership and target history; cross-database relations
materialize IDs because Django cannot execute a cross-database subquery.

## Read-only and cache-isolated

An active historical context is read-only. GeneralManager and direct interface
`create`, `update`, and `delete` entry points raise `HistoricalMutationError`
before permission checks, transports, signals, or database writes. Direct
application writes through Django are outside the context's mutation guard.

Current, date-A, and date-B values use separate cache identities. Historical
instants are canonicalized to UTC, and the effective date namespaces run,
timeout, and dependency caches, ORM bucket reuse, and GraphQL warm-up recipes.
See the [cache API reference](../api/cache.md#historical-cache-identity) for
the warm-up recipe version and deployment implications.

Continue with the [historical query how-to](../howto/historical_queries.md),
adapt the [historical snapshot cookbook](../examples/historical_queries.md),
and consult the [Historical Context API reference](../api/historical_context.md)
for signatures and error contracts.
