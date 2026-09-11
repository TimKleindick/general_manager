# Filtering and Pagination

The GraphQL layer reuses bucket semantics to expose flexible filtering and pagination without writing custom resolvers for every manager.

## Permission-aware filtering

Before executing a query, the resolver calls `get_read_permission_filter()`. Permission classes translate their rules into Django-style filter/exclude dictionaries. The GraphQL layer applies these constraints to ensure clients only see authorised records.

## Query arguments

Each generated list field accepts `filter`, `exclude`, `orderBy`, `page`, and
`pageSize` arguments when the corresponding generated options exist. Separate
`…Groups` fields require `groupBy` and use the same page envelope.
Python-side helpers use the names
`order_by`, `group_by`, and `page_size`; Graphene exposes them as camelCase by
default. Top-level list queries and generated relation-list fields always
include nullable `page` and `pageSize`. They include nullable `filter` and `exclude` only
when a filter input type can be generated for the manager, and nullable typed
`orderBy` only when the manager has sortable root scalar fields, root
`@graph_ql_property` values declared with `sortable=True`, or an eligible direct
manager relation. Direct manager relations add the relation itself, which sorts
by identifier, and one-hop related scalar interface fields such as
`commercials__name`. Collection relations, multi-hop paths, and computed
GraphQL properties on the related manager are not exposed as sort options.
Top-level list queries also include nullable `includeInactive` when the manager
uses soft delete; relation-list fields do not add `includeInactive`. Omitted
`includeInactive` defaults to `false`; omitted filter, exclude, order, group,
and pagination values default to `null` on the Python
side. Filters support Django lookups (`name__icontains`, `total_capex__gte`,
etc.) and automatic casting of measurements and dates.
`filter` and `exclude` may be GraphQL input objects or JSON strings that decode
to objects; malformed JSON, JSON arrays, JSON scalars, and JSON `null` are
treated as empty filters. Bucket chaining happens server-side, so complex
filters remain efficient.

Nested relation filters are normalized before they reach the bucket. Relation
`none` filters are supported in `filter`, but not inside `exclude`; any
dictionary key named `none` at any depth under `exclude`, including a top-level
`none` key, raises
`UnsupportedExcludeNoneRelationFilterError` because the resolver cannot invert
that relation shape safely. For non-calculation lists, permission filters and
any required per-instance row gate run before user filters. Calculation lists
first apply predicates rooted at declared calculation inputs, then run
permission filtering and the row gate; predicates rooted at computed GraphQL
properties remain deferred until afterward. Computed properties are therefore
never evaluated before per-instance authorization. The input-filtered
calculation candidates are fenced as an exact subset before permission
prefilters, so permission constraints can narrow that subset but cannot
replace a same-key user predicate and reintroduce candidates.

The resolver parses and normalizes all query arguments before authorization. For
non-calculation lists, permission prefilters and the row gate run first, then
normalized positive filter predicates, explicit excludes, and the accumulated
relation-derived exclusions from both inputs. For calculation lists, that
predicate order is preserved within two phases: declared-input predicates run
before authorization and deferred computed-property predicates run afterward.
Grouping, sorting, total-count calculation, and pagination follow both predicate
phases. Filter
normalizers receive the parsed object mapping for the current `filter` or
`exclude` input and must return both `filter` and `exclude` mappings; missing
keys propagate the resulting Python `KeyError`.

## Pagination model

Pagination is page-based. Responses include a `pageInfo` object with:

- `total_count`
- `current_page`
- `total_pages`
- `page_size`

`total_count` is computed after permission filtering, user filters, excludes,
grouping, and sorting, but before pagination. If only one pagination argument
is supplied, the other defaults to `page=1` or `page_size=10`. Explicit `page`
and `pageSize` values must be positive; zero and negative values surface as
GraphQL `BAD_USER_INPUT` errors before slicing. `pageInfo` always reports the
effective page and page size. Empty known result sets report `totalPages: 0`,
and an out-of-range positive page returns an empty page. Unpaginated nonempty
lists report `currentPage: 1`, `pageSize: null`, and `totalPages: 1`.
Request-backed lists only know the upstream total when their provider returns
one, so fetched row count must not be presented as a global total.

## Grouping

Generated `…List` fields return original records. Their `…Groups` siblings
require `groupBy` and return `items` / `pageInfo` with a distinct grouped item
type. The contract is the same for Database, Excel and Calculation managers.

```graphql
projectGroups(groupBy: ["status"], orderBy: [{field: amount, direction: DESC}]) {
  items {
    status
    amount
    customerList(orderBy: [{field: name}], page: 1, pageSize: 5) {
      items { id name country }
      pageInfo { totalCount }
    }
    customerGroups(groupBy: ["country"]) {
      items { country name }
      pageInfo { totalCount }
    }
  }
  pageInfo { totalCount }
}
```

The example assumes `Project` has `status`, `amount`, and a `customer` relation,
and `Customer` has `name` and `country`. Supply at least one nonblank eligible
field name; Python names and GraphQL camelCase names are accepted. Collection
fields cannot be grouping keys.

Selected keys retain their values. Other fields aggregate as follows:

| Field type | Grouped value |
| --- | --- |
| Numbers, Decimal, Measurement | Sum of non-null values |
| Strings | Distinct values joined by `", "` in encounter order |
| Booleans | Whether any non-null value is true |
| Dates and times | Maximum non-null value |
| Singular manager relation | Distinct original managers via `…List` and `…Groups` |
| Bucket collection | Distinct original managers via `…List` and `…Groups` |
| Entity `id` | Null unless selected as a grouping key |
| Singular relation ID alias | Shared non-null ID, or null when references differ |

All-null scalar fields return null. Related collections with no members return
empty pages. Each nested list or grouping has its own filters, ordering and
pagination, scoped to its parent's members. Bucket-backed collections support
further grouping at any nesting depth. Repeated references to the same manager
identity contribute one related record, so a customer is not counted once per
project.

If a singular relation already has a corresponding declared `…List` field,
that collection keeps its name. The singular relation uses `…RelationList` and
`…RelationGroups` on grouped items, so both sources remain accessible.

Primitive list properties and `List[GraphQLType]` concatenate original member
lists. These remain plain output lists: they gain no query arguments or grouping
companions. Expose a CalculationInterface and bucket when a calculated
collection needs querying. Unsupported aggregate types produce field errors
before their getters run. Declared GraphQL nullability still applies; groups
have no synthetic entity identity.

Filters, exclusions and row authorization run before grouping. Grouping and
ordering fields require permission before values are read, including the
canonical relation for a foreign-key ID alias. Selected aggregate fields check
all contributing members. Group ordering supports eligible aggregate scalars
and runs before pagination. `totalCount` counts groups; each nested page counts
its own results. Empty results return `items: []` with normal metadata. Partial
remote request pages cannot be grouped as a complete set. Complete request
responses are grouped locally from the authorized records: group ordering and
pagination are not forwarded upstream, and grouping keys need no corresponding
upstream filter declaration.

Legacy `keys`, `sums`, `members` and `count` wrappers are not generated. Select
keys and aggregate fields directly under `items`; query an ordinary filtered
`…List` for original records and their count. Aggregate filtering (`having`) is
not provided: `filter` and `exclude` always constrain source records.

Grouping must inspect the complete authorized source before it can paginate
groups. Nested related collections materialize and deduplicate their original
managers. Small page sizes limit returned results, but do not limit this grouping
work; selecting many nested group fields can therefore cost substantially more
than an ordinary record list.

## Sorting

Use the generated `orderBy` input with an ordered list of typed terms such as
`[{field: name}, {field: date, direction: DESC}]`. The first key is primary,
later keys break ties, and each term controls its own direction. A direct
manager field sorts by that manager's identifier, while a one-hop key
such as `commercials__name` sorts by an eligible related scalar. Collection
relations, multi-hop paths, and computed GraphQL properties on related managers
are excluded.

Python-side tests and helper calls use `order_by`. Buckets validate the requested
fields; invalid names trigger `ValidationError` with descriptive messages.
Invalid GraphQL enum values and null list elements are rejected by Graphene
before the resolver runs. When `orderBy` is omitted, `null`, or an empty list,
sorting is skipped.

Grouped endpoints sort eligible aggregate scalar values after grouping.
Ordinary list endpoints sort individual records.

Typed variables use a list declaration such as `$order: [ProjectOrderBy!]`;
add an outer `!` only when the variable itself must be non-null.

## Extending filters

Register custom filter input types by populating `GraphQL.graphql_filter_type_registry`. For example, you can add an enum to control domain-specific filters or expose nested filters for related managers.
