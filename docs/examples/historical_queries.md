# Historical Snapshot Cookbook

Use this recipe when a report must show one consistent version of the data.
Replace `Project` and its fields with managers from your application.

## Python report

```python
from datetime import UTC, datetime

from general_manager.api import as_of, current_as_of_date
from myapp.managers import Project

snapshot = datetime(2022, 1, 1, tzinfo=UTC)

with as_of(snapshot) as active_snapshot:
    rows = [
        {
            "id": project.id,
            "name": project.name,
            "owner": project.owner.name,
        }
        for project in Project.filter(status="active")
    ]
    assert current_as_of_date() == active_snapshot

print(rows)
```

The list, each manager attribute, and the nested `owner` read use the same
historical instant. A cached calculation or GraphQL property reached by those
reads is namespaced by that instant as well.

The timestamp is exact: a recent `search_date` still selects the history row,
and a date before the object's creation does not fall forward to the current
row. `historical_lookup_buffer_seconds` is retained only so existing interface
classes continue to import; changing it does not restore live-row fallback.

## Inspect a manager's history

Given an existing history-enabled `Project` with a `name` and an `owner`,
select an older version and inspect its audit record separately:

```python
from myapp.managers import Project

project = Project(id=42)
history = project.history.order_by("history_date", "history_id")
oldest = history.first()
if oldest is not None:
    print(oldest.name, oldest.owner.name)  # Both use the version's snapshot.

audit = history.raw.first()
if audit is not None:
    print(audit.history_date, audit.history_change_reason)
```

The selected manager is read-only and keeps its snapshot after the selection.
The example assumes the oldest record is a readable version and that the owner
existed at that instant. `.raw` provides Django audit rows, including deletion
records, and is not restricted by an ambient or explicit snapshot date. Use
`history.filter(...)` before selecting to narrow audit fields; the version's
closing instant still comes from the complete entity timeline. For edge cases
and the supported methods, see the [history query reference](../api/core.md#orm-manager-history).

To expose related data through a property, add a mixin like this to the
application's `Project` manager. It also works on a manager selected from
`project.history`:

```python
class ProjectHistoryFields:
    @property
    def owner_name(self):
        return self.owner.name
```

The relation uses the selected snapshot, including for foreign keys targeting
a non-primary-key field. Reverse one-to-one lookups use the historical
membership as well. Materialize iterator results inside a property before
returning them; a raw iterator cannot escape a historical read.

## Equivalent GraphQL request

```graphql
query HistoricalProjects($date: DateTime!) @asOf(date: $date) {
  projectList(filter: {status: "active"}) {
    items {
      id
      name
      owner { id name }
    }
  }
}
```

```json
{"date": "2022-01-01T00:00:00Z"}
```

The directive applies to the complete selected operation, not only the
`projectList` field. A literal is also valid:

```graphql
query HistoricalProjects @asOf(date: "2022-01-01T00:00:00Z") {
  projectList { items { id name } }
}
```

## Guard against mixed snapshots

```python
from general_manager.api import (
    HistoricalContextConflictError,
    HistoricalMutationError,
    as_of,
)

with as_of("2022-01-01"):
    try:
        Project(id=42, search_date="2023-01-01")
    except HistoricalContextConflictError:
        pass

    try:
        Project.create(name="not allowed", ignore_permission=True)
    except HistoricalMutationError:
        pass
```

Use [the how-to](../howto/historical_queries.md) for migration and error
handling guidance, and [the API reference](../api/historical_context.md) for
the exact signatures and GraphQL error codes.
