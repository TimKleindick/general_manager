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
that relation shape safely. For ordinary database lists, a static proof can
allow all normalized filters and exclusions to narrow the native bucket before
row and operand authorization. When any predicate cannot be proved safe, row
and operand authorization runs first across the row-visible source, before user
filters are applied. Calculation lists
first apply only proven plain constructor-input constraints that need no input
normalization callback (apart from a native daily date range with identity
normalization) to restrict their possible candidate domain, then apply permission
filtering and the row gate. An exclusion mapping moves into that early phase only
when every member is such a constraint, so its NOT(AND) meaning stays atomic. The
authorized rows are frozen before all remaining declared-input
and computed-property predicates run. User input
filters therefore cannot replace permission constraints and reintroduce
unauthorized candidates.

Native filter pushdown applies only when every filter and exclusion operand
is proven to have no additional field-read restriction. Relation paths also
require unrestricted traversed fields and an unconditional row-read plan for
each traversed target. Canonical relation and ID aliases share the field rule.
An unrelated field restriction does not by itself prevent pushdown. The proof
assumes permission declarations stay stable during the query; custom providers,
custom hydration and unknown or object-dependent policies use the conservative
path. Nullable relations and exclusions retain SQL semantics.

After any safe narrowing, row authorization and query-field checks still run on
the remaining candidate domain. Sorting and pagination do not narrow that domain
before operand checks. On the conservative path, checks include nonmatching and
off-page row-visible candidates; a denied operand fails the query. Static
preparation stores no user or object permission decision. See the
[security guide](security.md#query-operands-and-search-projections) and
[API reference](../../api/graphql.md#native-filter-pushdown-and-field-permissions).

The resolver parses and normalizes query arguments before authorization. The
native early phase moves the complete filter and exclusion plan together; if
its proof fails, normalized filters and exclusions run after authorization.
Calculation lists have an early
plain-constructor phase (including proven native daily date ranges) before authorization, followed by remaining
declared-input predicates and then deferred computed-property predicates on the
frozen, authorized rows. Grouping, sorting, total-count calculation, and pagination
follow all applicable phases. Filter
normalizers receive the parsed object mapping for the current `filter` or
`exclude` input and must return both `filter` and `exclude` mappings; missing
keys propagate the resulting Python `KeyError`.

Request-backed lists follow a separate path. Unless the permission plan denies
all rows, user predicates are applied to the base request bucket before that
filtered source is authorized. When query operands are present, an earlier
query-field permission check uses a separately authorized subset; it does not
restrict the base bucket used to apply user predicates. Do not assume these
predicates operate only on authorized rows. Supported remote ordering and
pagination controls may also be forwarded while preparing the request source,
before its final row authorization.

## Computed-property filters and ordering

For computed fields, `filterable=True` and `sortable=True` declare the available
GraphQL controls. A database-backed property's `query_annotation` can express
filtering, exclusion, and sorting in the ORM, while its Python getter still
supplies the returned field value. Calculation properties use Python evaluation
and do not execute query annotations. See the
[GraphQL property guide](../../howto/expose_via_graphql.md#declare-computed-graphql-properties)
for expression and queryset-callback examples, fallback costs, and backend limits.

## Identifier filters

Actual ORM primary keys and raw foreign-key aliases use `ID` for equality and
exact filters, and `[ID]` for membership. With default camel-casing, an `id`
field exposes `id`, `id_Exact`, and `id_In`; a PK named `code` exposes `code`,
`code_Exact`, and `code_In`; a raw `owner_id` reference exposes `ownerId`,
`ownerId_Exact`, and `ownerId_In`. The names come from field metadata, so
ordinary fields with ID-like names and calculation/request composite business
inputs keep their native scalar filters.

```graphql
query ProjectsById($id: ID!, $ids: [ID]) {
  exact: projectList(filter: {id_Exact: $id}) {
    items { id name }
  }
  selected: projectList(filter: {id_In: $ids}) {
    items { id name }
  }
}
```

```json
{"id": "42", "ids": ["42", "43"]}
```

ID outputs are strings, and the server converts equality filter values back
to the configured Python/ORM type before querying. Raw foreign keys use their
referenced target field for conversion, including relations configured with
`to_field`. Ordered comparisons such as `id_Gt` retain the native scalar:
an integer PK uses `Int`, and a field with the native `bigint` hint uses
`BigIntScalar`. Those range variables do not change to `ID`.
String-key pattern filters also retain their native `String` arguments.

Object relation filters retain their nested shape, and collection relation
filters retain `any` and `none`. When updating clients, change affected scalar
variables from `Int`/`String` to `ID` and lists to `[ID]`, regenerate client
types, and keep IDs as strings in comparisons, state, and cache keys. See the
[identifier guide](../../howto/expose_via_graphql.md#filter-by-identifier).

## Pagination model

Pagination is page-based. Responses include a `pageInfo` object with:

- `total_count`
- `current_page`
- `total_pages`
- `page_size`

`total_count` is computed after permission filtering, user filters, excludes,
grouping, and sorting, but before pagination. Generated root and nested `…List`
and `…Groups` fields return the complete result when both `page` and `pageSize`
are omitted. In that unpaged mode, `pageInfo` reports `currentPage: 1`,
`pageSize: null`, and `totalPages: 1` for a nonempty known result (`0` when
empty). Supplying either argument enables pagination; the missing argument then
defaults to page 1 or size 10. The maximum page size is 100.
Operators can configure these sizes in Django settings:

```python
GENERAL_MANAGER = {
    "GRAPHQL_DEFAULT_PAGE_SIZE": 10,
    "GRAPHQL_MAX_PAGE_SIZE": 100,
}
```

Both settings must be positive integers (not booleans), and the default must not
exceed the maximum. Explicit `page` and `pageSize` values must be positive;
`pageSize` above the configured maximum is rejected rather than clamped.
Invalid pagination is rejected before loading the list source. `pageInfo`
always reports the effective page and size. Empty known result sets report
`totalPages: 0`; an out-of-range positive page returns an empty page while
retaining the known total.

Request-backed lists forward pagination only when the caller supplies `page` or
`pageSize`. An incomplete upstream page may satisfy an explicitly requested
page only when its coordinates match; returned items are capped even if the
provider overdelivers. An unpaged GraphQL request requires a complete upstream
response. If the upstream response is partial, the resolver rejects the request
instead of presenting one remote page as a complete result or fetching an
unbounded sequence of remote pages. Upstream row counts are not presented as
global totals unless authorization can establish their validity; unknown totals
remain null.

These bounds limit items returned by generated paged fields. Permission checks,
exact counts, grouping, and Python-side sorting may still traverse or materialize
the source before pagination. Plain list-valued properties without a generated
`items` / `pageInfo` contract are not covered by these settings.

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
