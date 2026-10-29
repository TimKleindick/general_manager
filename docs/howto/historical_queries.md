# Run Historical Queries

This guide shows how to select one historical snapshot for Python and GraphQL
reads. It assumes the models being queried have history support and that the
corresponding migrations have been applied.

## 1. Read one snapshot in Python

Import the stable context from `general_manager.api` and keep all dependent
reads inside the same `with` block:

```python
from datetime import UTC, datetime

from general_manager.api import as_of
from myapp.managers import Project

snapshot = datetime(2022, 1, 1, tzinfo=UTC)
with as_of(search_date=snapshot):
    project = Project.get(id=42)
    active_projects = Project.filter(status="active")
    related_projects = project.related_projects_list
```

The manager, bucket, relation, calculation, and cached property reads all use
the same effective instant. `current_as_of_date()` returns that normalized
instant while the block is active.

If a single read is all that is needed, the existing explicit form remains
available:

```python
project = Project(id=42, search_date="2022-01-01T00:00:00Z")
projects = Project.filter(search_date=snapshot, status="active")
```

Inside an active context, an explicit `search_date` must represent the same
instant. It cannot override the operation snapshot.

ORM interfaces use the exact supplied instant for every dated read, including a
timestamp only milliseconds before the current time. The legacy
`historical_lookup_buffer_seconds` attribute remains accepted for source
compatibility, but it no longer falls back to the live row. If no historical
row exists at or before the timestamp, the read remains missing rather than
silently returning current data.

## 2. Expose the snapshot through GraphQL

GeneralManager registers this built-in directive in generated schemas:

```graphql
directive @asOf(date: DateTime!) on QUERY
```

Use a variable for a client-controlled snapshot date:

```graphql
query HistoricalProjects($date: DateTime!) @asOf(date: $date) {
  projectList {
    items {
      id
      name
    }
  }
}
```

Send variables such as:

```json
{"date": "2022-01-01T00:00:00Z"}
```

The directive belongs to the selected query operation, so `operationName`
selects both the operation and its snapshot when a document contains multiple
operations. The date applies to nested relations, calculations, and cache
accesses in that operation. Each item in a GraphQL batch gets an independent
context.

`@asOf` is query-only, accepts exactly one non-null `date`, and cannot be used
on mutations or subscriptions. The synchronous endpoint rejects async mutation
root resolvers before their bodies run so atomic mutation handling remains
safe; use synchronous mutation resolvers there.

## 3. Provision historical many-to-many data

When a generated or auto-registered existing model declares local
many-to-many fields, GeneralManager registers history through tables. Apply
the normal schema workflow after manager/model declarations change:

```bash
python manage.py makemigrations
python manage.py migrate
```

Only membership changes recorded after the through tables are deployed are
available historically. If source membership history or target-row history is
missing, a dated relation read raises `HistoricalReadNotSupportedError`
instead of returning current membership.

## 4. Handle fail-closed behavior

Catch the stable errors when an operation can legitimately encounter an invalid
date, a conflicting snapshot, or an interface without historical support:

```python
from general_manager.api import (
    HistoricalContextConflictError,
    HistoricalReadNotSupportedError,
    InvalidSearchDateError,
    as_of,
)

try:
    with as_of(search_date="2022-01-01"):
        result = Project.filter(status="active")
        list(result)
except InvalidSearchDateError:
    raise ValueError("Choose an ISO date or datetime.")
except HistoricalContextConflictError:
    raise ValueError("Use one snapshot date for the complete operation.")
except HistoricalReadNotSupportedError:
    raise RuntimeError("This data source cannot provide historical reads.")
```

Historical contexts are read-only. `GeneralManager.create()`, `update()`, and
`delete()` and their direct interface equivalents raise
`HistoricalMutationError`, even when permission checks are otherwise skipped.
An explicitly dated manager also rejects `update()` and `delete()` after the
context exits. `manager.history.first()` returns a frozen instance of the
original manager class; use `manager.history.raw.first()` when you need the
underlying audit record and metadata.

To inspect a particular version, filter on history fields, then select a
manager. Its relations and GM property queries use the selected version's
snapshot. For a dated source manager, selection is limited to audit rows at or
before its date; selecting a row whose validity interval does not contain that
date raises `HistoricalContextConflictError`. For deletion records or other
audit-only operations, use `.raw` instead. See the
[history recipe](../examples/historical_queries.md#inspect-a-managers-history)
and [history query reference](../api/core.md#orm-manager-history).

For a property that reads related managers, use ordinary or GraphQL properties;
their GM reads use the manager's snapshot, including custom property subclass
lookups. Return a materialized collection instead of a raw iterator. Dated
non-primary-key foreign keys and reverse one-to-one relations also resolve at
the snapshot and return `None` when no relation exists then.

See the [historical execution concept](../concepts/historical_context.md), the
[snapshot cookbook](../examples/historical_queries.md), and the
[API reference](../api/historical_context.md) for the complete contract.
