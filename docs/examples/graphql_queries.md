# GraphQL Query Patterns

## Reuse identifier variables

Use `ID` for actual ORM primary keys and scalar foreign-key references, and
`[ID]` for membership filters. The same ID variable works for a detail query
and an equality filter:

```graphql
query ProjectIdentity($id: ID!, $ids: [ID]) {
  project(id: $id) { id name }
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

Returned IDs are strings, including integer-backed keys. Keep those strings
in comparisons, application state, and cache keys; avoid numeric coercion.
When migrating from `Int`/`String` declarations, update identity variables to
`ID`, their lists to `[ID]`, and regenerate client types. Range variables and
ordinary business inputs keep their native types, including a composite
calculation/request input named `id`. Manager references retain object outputs
and scalar or structured input shapes. See the
[identifier guide](../howto/expose_via_graphql.md#filter-by-identifier)
for explicit UUID/string input configuration and the full migration steps.

## Project native identities

Using the `Record` declaration from the
[native identity property guide](../howto/expose_via_graphql.md#declare-native-identity-properties),
create a row with PK `2147483648` and query:

```graphql
query RecordIdentity($id: ID!) {
  record(id: $id) { id recordKey recordKeys externalId }
}
```

```json
{"id": "2147483648"}
```

The result is:

```json
{
  "record": {
    "id": "2147483648",
    "recordKey": "2147483648",
    "recordKeys": ["2147483648"],
    "externalId": 7
  }
}
```

Only the properties declared with `is_identifier=True` adopt identity output.
The native `record.record_key` and `values_list("id", flat=True)` values remain
integers. Keep schema-generated ID outputs as strings; update client types and
equality/cache-key handling when adding the declaration to an existing schema.

## Filter global search by native identity

With `leader_id` declared as a real foreign-key filter on the `global` index:

```graphql
query SearchByLeader($filters: JSONString) {
  search(query: "", filters: $filters) {
    total
    results { __typename }
  }
}
```

```json
{"filters": "[{\"field\": \"leader_id\", \"values\": [\"9007199254740993\"]}]"}
```

The resolver sends the native integer `9007199254740993` to the selected
manager's backend. Business integer filters still require JSON numbers, and
string keys such as `"007"` remain strings. Native property filters require the
explicit identity declaration shown above; backend collection membership
semantics remain unchanged. See the [search guide](../howto/search.md#step-4-query-via-graphql).

Search is still permission-scoped after the backend lookup. The resolver
enumerates the authorized native source instances and matches hits to those
instances by normalized identification, so missing or out-of-scope identities
cannot appear in `results` or inflate `total`. Indexed text and filter values
can still match older content until reindexing; returned objects come from the
authorized source snapshot. No client-side filtering is a replacement for this
server check. Use `types` to select manager classes. Synthetic `type` filter
keys retain backend matching semantics and are excluded from operand permission
checks unless the selected manager actually exposes a field named `type`.

## Paginated listings

```graphql
query ProjectList($page: Int!, $pageSize: Int!) {
  projectList(page: $page, pageSize: $pageSize, orderBy: [{field: startDate, direction: DESC}]) {
    items {
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

## Group through a dedicated endpoint

`orderBy` orders eligible aggregate scalars after grouping and before pagination.
This keeps descending group order stable across pages:

```graphql
query ProjectsByStatus {
  projectGroups(
    groupBy: ["status"]
    orderBy: [{field: status, direction: DESC}]
    page: 1
    pageSize: 10
  ) {
    items { status name }
    pageInfo { totalCount currentPage totalPages pageSize }
  }
}
```

If the filter produces no groups, the same query shape returns `items: []` and
page metadata rather than an empty-group slicing error.

## Group by a related manager identity

Group by the scalar relation ID and retrieve distinct original related managers:

```graphql
query ProjectsByCommercial {
  projectGroups(groupBy: ["commercials_id"]) {
    items {
      commercialsId
      commercialsList { items { id name } pageInfo { totalCount } }
    }
    pageInfo { totalCount }
  }
}
```

Bucket relations also expose `…Groups` for recursive grouping, with independent
filters, ordering and pagination. Plain `List[GraphQLType]` properties remain
output lists without query controls. See the [grouped-data concept](../concepts/models_entities.md#grouped-data),
[GraphQL how-to](../howto/expose_via_graphql.md#query-generated-lists), and
[core API reference](../api/core.md#general_manager.manager.group_manager.GroupManager)
for the full grouping and error contract.

## Aggregate unique text values in groups

Generated group fields can return a text field directly under `items` as a
compact, stable summary:

```graphql
query ProjectNamesByStatus {
  projectGroups(
    groupBy: ["status"]
    orderBy: [{field: name, direction: ASC}]
  ) {
    items {
      status
      name
    }
    pageInfo { totalCount }
  }
}
```

For member values `["Alpha", "Alpha", "Beta", null]`, the `name` aggregate is
`"Alpha, Beta"`. Values are deduplicated in encounter order, nulls are
excluded, and an all-null group returns `null`. Numeric sums retain their
existing addition behavior. `orderBy` sorts the aggregated `name` values even
though `name` is not selected in `groupBy`; `totalCount` counts groups. See the
[grouping concept](../concepts/graphql/filters_pagination.md#grouping),
[GraphQL how-to](../howto/expose_via_graphql.md#query-generated-lists), and
[GraphQL API reference](../api/graphql.md#explicit-grouped-result-sums) for
permissions, arguments, and compatibility details.

## Nested buckets

```graphql
query ProjectWithDerivatives($id: ID!) {
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

The ordinary root and relation pages use the related manager's item-derived
page type. A relation to `ProjectType` therefore uses `ProjectTypeTypePage`
and items of type `ProjectTypeType`; update named fragments and regenerate
schema-derived clients after the page-type rename. The query field, arguments,
`items`/`pageInfo` shape, IDs, and permissions are unchanged.

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

## Query manager-valued properties

For the `selected_list` and `reviewers_list` properties in the [how-to](../howto/expose_via_graphql.md#expose-manager-valued-properties),
and the generated `reviewer_list` relation on the same `Project`, use:

```graphql
query ProjectPropertyShapes($projectId: ID!) {
  project(id: $projectId) {
    selectedList { id name }
    reviewersList { id name }
    reviewerList(page: 1, pageSize: 20) {
      items { id name }
      pageInfo { totalCount }
    }
  }
}
```

```json
{"projectId": "42"}
```

Replace `42` with an existing readable project ID. The first property returns
an object or `null`; the second returns a plain array. The generated relation
returns a page. Denied properties resolve to `null` without evaluating their
getters, and unreadable members are removed from native manager collections.
See the [API reference](../api/graphql.md#relation-annotation-compatibility) for
the version and client compatibility details.

## Update only the fields you supply

Generated ORM update and delete mutations require a stable `id: ID!` target.
Primary keys are excluded from update payload fields; other editable fields
are optional. Omit a field when the stored value should stay unchanged, even if
the model declares a default for that field:

```graphql
mutation RenameProject($id: ID!) {
  updateProject(id: $id, name: "Renamed") {
    success
    project { id name score }
  }
}
```

The omission rule also applies to nullable variables that are not present in
the variables object:

```graphql
mutation UpdateOptionalScore($id: ID!, $score: Int) {
  updateProject(id: $id, score: $score) {
    success
    project { id score }
  }
}
```

Use `{"id": "42"}` to preserve `score`, or
`{"id": "42", "score": null}` to explicitly clear a nullable score. A
concrete `score` value is written as supplied. Create mutations continue to
apply model defaults when their fields are omitted. See the [GraphQL how-to](../howto/expose_via_graphql.md#partially-update-a-generated-manager),
the [mutation concept](../concepts/graphql/schema_autogen.md#mutations), and
the [API reference](../api/graphql.md#generated-crud-mutation-contract).

## Reserved mutation controls

Generated create and update payloads cannot override the trusted manager
controls `creator_id` or `ignore_permission`. For example, this request fails
GraphQL validation because `creatorId` and `ignorePermission` are not declared
arguments; no mutation resolver or file-upload preflight runs:

```graphql
mutation UnsafeUpdate($id: ID!, $creator: ID!) {
  updateProject(
    id: $id
    name: "Attempted impersonation"
    creatorId: $creator
    ignorePermission: true
  ) {
    success
  }
}
```

The resolver also guards against reserved keys supplied directly or introduced
by relation normalization. That guard raises `BAD_USER_INPUT`, with the initial
check running before file-upload preflight.

The authenticated GraphQL user is always the manager actor. Use the separate
`historyComment` argument for audit metadata:

```graphql
mutation ReviewedUpdate($id: ID!) {
  updateProject(id: $id, name: "Approved", historyComment: "Reviewed") {
    success
    project { id name }
  }
}
```

Editable fields or relation aliases that normalize to `creator_id`,
`ignore_permission`, or `history_comment` are rejected while the schema is
generated. See the [GraphQL task guide](../howto/expose_via_graphql.md#keep-mutation-controls-out-of-client-input),
the [security concept](../concepts/graphql/security.md), and the
[API reference](../api/graphql.md#generated-crud-mutation-contract).

## Create with a manual primary key

For a manager with an editable integer PK field named `id` and no default,
declare the create variable as `ID!`:

```graphql
mutation CreateManualRecord($id: ID!, $name: String!) {
  createManualRecord(id: $id, name: $name) {
    success
  }
}
```

```json
{"id": "42", "name": "Manual record"}
```

Auto-increment PKs are omitted from create arguments. A manual PK with a model
default is optional. Literal defaults remain in the schema; `NOT_PROVIDED`
and callable defaults are omitted, and the ORM applies callable defaults when
the create write omits the field. Schema construction does not evaluate them,
and input/model validation still applies. Required fields without real
defaults need non-null variable declarations, such as `$name: String!` above.
See the [manual-PK guide](../howto/expose_via_graphql.md#create-a-manager-with-a-manual-primary-key).

## Sort by a compound relation key

Generated list fields accept typed `orderBy` terms. This request sorts
projects by the related commercial name first, then by project name and unique
project ID to make ties deterministic:

```graphql
query ProjectsByCommercialName($order: [ProjectOrderBy!]) {
  projectList(orderBy: $order, page: 1, pageSize: 20) {
    items {
      id
      name
      commercialsList { items { id name } pageInfo { totalCount } }
    }
    pageInfo {
      totalCount
      currentPage
      totalPages
      pageSize
    }
  }
}
```

```json
{"order": [{"field": "commercials__name"}, {"field": "name"}, {"field": "id"}]}
```

The enum values are exposed by the generated `ProjectOrderField` type. Each
input has a required field and an `ASC` default direction; an empty list is a
no-op. See the [sorting concept](../concepts/graphql/filters_pagination.md#sorting),
the [generated-list how-to](../howto/expose_via_graphql.md#query-generated-lists),
and the [GraphQL API reference](../api/graphql.md#compound-list-sorting) for
the supported relation paths and error behavior.

## Subscribe to a protected manager instance

Detail subscriptions use the same generated identification arguments as the
manager's other operations:

```graphql
subscription ProjectDetails($id: ID!) {
  onProjectChange(id: $id) {
    action
    item { id name }
  }
}
```

The initial target must be readable by the authenticated user. GeneralManager
checks that permission before evaluating `item` properties, checks again before
the snapshot is delivered, and rechecks before each queued update. Revoking
access or deleting the target ends the detail stream after cleanup; it does not
emit an event with `item: null`. An allowed target can still mask individual
fields according to their normal read rules. See the [subscription concept](../concepts/graphql/subscriptions.md)
for dependency and lifecycle details.

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
Aggregate `refresh` events require class-level read operation permission, have
`item: null`, and do not disclose a row ID.

## Subscribe to fields with read permissions

Field-level read rules also apply to the fields selected inside a subscription
payload. This manager keeps `internalNote` visible only to staff users while
leaving the public `name` field readable for any authenticated user:

```python
from general_manager import GeneralManager
from general_manager.permission import AdditiveManagerPermission, register_permission


@register_permission("isStaff")
def is_staff(_instance, user, _config):
    return bool(getattr(user, "is_staff", False))


class Project(GeneralManager):
    name: str
    internal_note: str

    class Permission(AdditiveManagerPermission):
        __read__ = ["isAuthenticated"]
        internal_note = {"read": ["isStaff"]}
```

Subscribe with the generated field names:

```graphql
subscription ProjectChangesWithFieldRules {
  onProjectClassChange {
    action
    item {
      id
      name
      internalNote
    }
  }
}
```

For an authenticated non-staff subscriber, `internalNote` resolves to `null`
while `name` and the event action remain available. The same rule applies to
normal, measurement, and stored-file payload fields; a denied field is not
accessed. For an allowed subscription field, permission evaluation, lazy value
access, measurement conversion, and stored-file formatting run together in an
async-safe worker, so uncached foreign-key values to other GeneralManagers are
safe to resolve. Query and mutation field resolution remains synchronous. See the
[GraphQL how-to](../howto/expose_via_graphql.md#protect-subscription-payload-fields)
and [API reference](../api/graphql.md#subscription-field-authorization) for
execution and exception details.

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
{"projectId": "42"}
```

The generated filter is directly usable with a normal GraphQL request. The
server translates `project: {id: ...}` to the calculation lookup
`project__id=...`; replace `id` with a supported nested field or lookup when
needed. See the [calculation how-to](../howto/expose_via_graphql.md#filter-calculation-managers-by-manager-input)
and [API reference](../api/graphql.md#manager-typed-calculation-input-filters) for
the declaration and compatibility rules.

The same lookup behavior applies when `possible_values` returns a list instead
of a bucket:

```python
from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input
from myapp.managers import Project


class ProjectCommercial(GeneralManager):
    class Interface(CalculationInterface):
        project = Input(
            Project,
            possible_values=lambda: list(Project.all()),
        )


ProjectCommercial.filter(project__name__startswith="North")
```

Manager objects, `project_id`, `project__id__in`, and nested manager lookups
work for both domain shapes. Generated reverse relations keep the parent filter
when the relation is paginated, so a project never receives another project's
calculation rows. See the [calculation concept](../concepts/interfaces/computed_data_interfaces.md#iterating-combinations)
and [task guide](../howto/expose_via_graphql.md#filter-calculation-managers-by-manager-input).

## Custom mutation with Measurement input

Custom scalar arguments keep their declared Python annotation. For a resolver
whose argument is annotated `id: int`, use `Int`; the argument name alone does
not make it an ID. Annotate a manager reference to generate an ID or structured
composite input instead.

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
query ProjectSummary($id: ID!) {
  project(id: $id) {
    name
    totalCapex
    duration
    derivativeSummary
  }
}
```

Use these patterns as a starting point and adapt filters or selections to your domain.
