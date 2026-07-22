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
