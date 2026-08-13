# GraphQL Query Patterns

## Paginated listings

```graphql
query ProjectList($page: Int!, $pageSize: Int!) {
  projectList(page: $page, pageSize: $pageSize, orderBy: ["-start_date"]) {
    results {
      id
      name
      startDate
    }
    pageInfo {
      totalCount
      totalPages
    }
  }
}
```

## Sort grouped pages

`sortBy` orders grouped manager objects after `groupBy` and before pagination.
This keeps descending group order stable across pages:

```graphql
query ProjectsByStatus {
  projectList(
    groupBy: ["status"]
    sortBy: status
    reverse: true
    page: 1
    pageSize: 10
  ) {
    items { status }
    pageInfo { totalCount currentPage totalPages pageSize }
  }
}
```

If the filter produces no groups, the same query shape returns `items: []` and
page metadata rather than an empty-group slicing error.

## Nested buckets

```graphql
query ProjectWithDerivatives($id: Int!) {
  project(id: $id) {
    name
    derivativeList(filter: { maturity_date__gte: "2024-01-01" }) {
      id
      maturityDate
      volume
    }
  }
}
```

## Query a manager relation

For a manager declaration such as `owner: User | None` and
`reviewer_list: Bucket[User]`, generated GraphQL exposes an object field and a
paginated relation-list field. Query both fields directly:

```graphql
query ProjectRelations($projectId: ID!) {
  project(id: $projectId) {
    owner { id name }
    reviewerList(page: 1, pageSize: 20) {
      items { id name }
      pageInfo { totalCount currentPage totalPages pageSize }
    }
  }
}
```

Nested relation filters use the same resolved manager type. A direct relation
uses a nested object, while a collection relation uses `any` or `none`:

```graphql
query ProjectsWithRelatedUsers {
  projectList(filter: {
    owner: { name: "Alice" }
    reviewerList: { any: { name: "Alice" } }
  }) {
    items { id name owner { id name } }
  }
}
```

For the Python annotation forms and the generated mutation/subscription
contracts, see the [GraphQL concept guide](../concepts/graphql/schema_autogen.md#relation-annotation-compatibility),
the [task guide](../howto/expose_via_graphql.md#declare-manager-relations), and
the [API reference](../api/graphql.md#relation-annotation-compatibility).

## Subscribe to committed class changes

```graphql
subscription ProjectChanges {
  onProjectClassChange {
    action
    item { id name }
  }
}
```

Identified class-wide events are checked against the subscribing user's read
permission in an async-safe worker after commit; unreadable objects are omitted.
Aggregate `refresh` events have `item: null` and do not disclose a row ID.

## Bound run-scoped cache memory

For a worker that serves long-lived GraphQL requests, configure the optional
process-local run-cache budget:

```python
GENERAL_MANAGER = {
    "RUN_CONTEXT_CACHE_MAX_BYTES": 256 * 1024 * 1024,
}
```

The value is an estimated-memory LRU budget shared by live run contexts in that
worker. Omit it or use `None` for unlimited retention; pending dependency-cache
publications remain pinned until their lifecycle completes.

## Filter a calculation by manager input

For a calculation manager with `project = Input(Project)`, use the same nested
direct-relation shape as a persisted manager:

```graphql
query ProjectCommercials($projectId: ID!) {
  projectCommercialList(filter: {project: {id: $projectId}}) {
    items {
      project { id name }
      targetDate
    }
  }
}
```

```json
{"projectId": 42}
```

The generated filter is directly usable with a normal GraphQL request. The
server translates `project: {id: ...}` to the calculation lookup
`project__id=...`; replace `id` with a supported nested field or lookup when
needed. See the [calculation how-to](../howto/expose_via_graphql.md#filter-calculation-managers-by-manager-input)
and [API reference](../api/graphql.md#manager-typed-calculation-input-filters) for
the declaration and compatibility rules.

## Custom mutation with Measurement input

```graphql
mutation UpdateInventory($id: Int!, $price: MeasurementScalar!) {
  updateInventoryItem(id: $id, price: $price) {
    success
    errors
    inventoryItem {
      id
      price
    }
  }
}
```

## Aggregation via GraphQL property

```graphql
query ProjectSummary($id: Int!) {
  project(id: $id) {
    name
    totalCapex
    duration
    derivativeSummary
  }
}
```

Use these patterns as a starting point and adapt filters or selections to your domain.
