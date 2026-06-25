# Filtering and Pagination

The GraphQL layer reuses bucket semantics to expose flexible filtering and pagination without writing custom resolvers for every manager.

## Permission-aware filtering

Before executing a query, the resolver calls `get_read_permission_filter()`. Permission classes translate their rules into Django-style filter/exclude dictionaries. The GraphQL layer applies these constraints to ensure clients only see authorised records.

## Query arguments

Each generated list field accepts `filter`, `exclude`, `sortBy`, `reverse`,
`groupBy`, `page`, and `pageSize` arguments when the corresponding generated
options exist. Python-side helpers use the names `sort_by`, `group_by`, and
`page_size`; Graphene exposes them as camelCase by default. Top-level list
queries and generated relation-list fields always include nullable `reverse`,
`page`, `pageSize`, and `groupBy`. They include nullable `filter` and `exclude`
only when a filter input type can be generated for the manager, and nullable
`sortBy` only when the manager has sortable scalar fields or sortable GraphQL
properties. Top-level list queries also include nullable `includeInactive` when
the manager uses soft delete; relation-list fields do not add
`includeInactive`. Omitted `reverse` and `includeInactive` values default to
`false`; omitted filter, exclude, sort, group, and pagination values default to
`null` on the Python side. Filters support Django lookups (`name__icontains`,
`total_capex__gte`, etc.) and automatic casting of measurements and dates.
`filter` and `exclude` may be GraphQL input objects or JSON strings that decode
to objects; malformed JSON, JSON arrays, JSON scalars, and JSON `null` are
treated as empty filters. Bucket chaining happens server-side, so complex
filters remain efficient.

Nested relation filters are normalized before they reach the bucket. Relation
`none` filters are supported in `filter`, but not inside `exclude`; any
dictionary key named `none` at any depth under `exclude`, including a top-level
`none` key, raises
`UnsupportedExcludeNoneRelationFilterError` because the resolver cannot invert
that relation shape safely. Permission filters are applied before user filters,
and any permission plan that still needs per-instance checks runs its row gate
before user filters, sorting, grouping, and pagination.

The resolver applies query arguments in a fixed order: permission prefilters and
the row gate run first, then explicit filters, normalized filter-side excludes,
explicit excludes, normalized exclude-side excludes, sorting, grouping, and
pagination. Filter normalizers receive the parsed object mapping for the current
`filter` or `exclude` input and must return both `filter` and `exclude`
mappings; missing keys propagate the resulting Python `KeyError`.

## Pagination model

Pagination is page-based. Responses include a `pageInfo` object with:

- `total_count`
- `current_page`
- `total_pages`
- `page_size`

`total_count` is computed after permission filtering, user filters, excludes, sorting, and grouping, but before pagination. If only one pagination argument is supplied, slicing defaults the other to `page=1` or `page_size=10`.
Falsey explicit pagination values such as `page: 0` or `pageSize: 0` follow the
same Python fallback for slicing. `currentPage` is reported as `page || 1`.
`pageSize` reports the original GraphQL argument value, not the effective
slicing default, so it remains `null` when only `page` is supplied and remains
`0` for `pageSize: 0`. `totalPages` is computed from a truthy original
`pageSize`; when `pageSize` is omitted or falsey it is reported as `1`, including
empty result sets. Negative `page` or `pageSize` values are rejected before
slicing and surface as GraphQL `BAD_USER_INPUT` errors.

## Grouping

Use `groupBy` to return grouped list results instead of materialized manager
items. `groupBy: null` leaves the list ungrouped. `groupBy: [""]` calls the
bucket's default `group_by()` behavior, while every other list is forwarded as
explicit group keys. Empty lists are still forwarded to the bucket as explicit
grouping input. Bucket validation errors, such as unknown group keys, propagate
through the GraphQL execution path.

Grouped responses keep their `GroupBucket` shape on the Python side while the
GraphQL page type still exposes the generated `items` field as a list of the
manager's generated GraphQL item type. Pagination slices the grouped bucket, so
pages contain grouped manager objects that resolve through the same item fields,
rather than the original ungrouped rows. Because dependency-cache prefetching
and capability warmups operate on materialized item lists, those warmups run
only for ungrouped result pages. Dependency-cache prefetch is triggered for
ungrouped pages only when selected item fields include dependency-cache-backed
properties. Capability warmup is triggered for ungrouped pages only when
`items.capabilities` is selected and the manager declares GraphQL permission
capabilities. Invalid group keys propagate the bucket's validation error through
GraphQL execution.

## Sorting

Use the generated `sortBy` enum together with `reverse` for descending order.
Python-side tests and helper calls use `sort_by`. Buckets validate the requested
fields; invalid names trigger `ValidationError` with descriptive messages.
Invalid GraphQL enum values are rejected by Graphene before the resolver runs.
When `sortBy` is omitted or `null`, sorting is skipped even if `reverse` is true.

## Extending filters

Register custom filter input types by populating `GraphQL.graphql_filter_type_registry`. For example, you can add an enum to control domain-specific filters or expose nested filters for related managers.
