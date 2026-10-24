# Expose Managers via GraphQL

Managers with an `Interface` are registered during GeneralManager startup and receive generated query, mutation, and subscription fields based on their interface capabilities.

## Return output-only GraphQL types

Use `GraphQLType` for a response value that needs a GraphQL object shape but
does not need a manager identity, lifecycle, or root operation. Import the
declaration at startup (normally from an application module imported during
app initialization) so GeneralManager discovers it before building the
schema. Concrete subclasses are automatically frozen dataclasses:

```python
from __future__ import annotations

from dataclasses import field

from general_manager import GeneralManager, GraphQLType, graph_ql_property
from general_manager.measurement import Measurement
from your_app.managers import Task, User  # application-specific registered managers


class ProjectHour(GraphQLType):
    user: list[User]
    total_hours: Measurement
    task: Task
    notes: str | None = None
    tags: list[str] = field(default_factory=list)


class ProjectHoursSummary(GeneralManager):
    @graph_ql_property
    def project_hours(self) -> list[ProjectHour]:
        return [
            ProjectHour(
                user=[user],
                total_hours=Measurement(8, "h"),
                task=task,
            )
        ]
```

`User` and `Task` are application-defined `GeneralManager` subclasses in this
illustrative example; replace the import with your registered manager module.
The `user` and `task` values passed to `ProjectHour` must be produced by the
owning property's application-specific calculation logic. `GraphQLType` only
describes the nested response shape and does not fetch or inject those values.

No explicit `@dataclass` decorator is required. Standard dataclass defaults
and `field(default_factory=...)` work, and construction is frozen after the
instance is created. The declaration above becomes the GraphQL object
`ProjectHourType`; it does not create a query, mutation, subscription, filter,
capability field, or manager lifecycle. It is available only through a field
that references it, such as `projectHours` in the example.

For scalar fields, the strict allowlist is `str`, `bool`, `int`, `float`,
`Decimal`, `datetime`, `date`, and subclasses of those types. Unknown
scalar-like classes are rejected and do not receive the legacy mapper's
fallback to `String`. The other supported values are `Measurement`, registered
managers, registered output types, and `list[T]`, `tuple[T, ...]`, or `set[T]`.
Use `T | None` for nullable values. Required annotations become non-null
GraphQL fields, while optional annotations become nullable fields; collection
elements follow the same rule. Bare collections, every fixed-length tuple such
as `tuple[int, int]`, `Any`, `Annotated[...]`, unresolved references, and
multi-target unions are invalid output annotations and fail schema generation
with a field-specific error. Only homogeneous variadic tuples written as
`tuple[T, ...]` are supported. Output declarations themselves are output-only:
input objects and automatic root operations are not generated.

The `@graph_ql_property` on the owning manager remains the authorization
boundary. A nested manager field still uses that manager's normal GraphQL
read permissions. Plain scalar fields and nested `GraphQLType` fields do not
receive an independent permission check, so omit or pre-authorize sensitive
values in the owning property.

The [typed output-object recipe](../examples/graphql_output_types.md) shows the
same contract with a complete startup import, calculation property, and
GraphQL query.

For flat native identities inside an output value, mark each dataclass field:

```python
from dataclasses import field
from uuid import UUID
from general_manager import GraphQLType


class RecordProjection(GraphQLType):
    record_key: int = field(metadata={"is_identifier": True})
    related_keys: list[int] = field(metadata={"is_identifier": True})
    optional_key: UUID | None = field(metadata={"is_identifier": True})
    quantity: int
```

Return native values from the owning property: for example,
`RecordProjection(9007199254740993, [9007199254740993], None, 17)`.
The fields expose `ID!`, `[ID!]!`, `ID`, and `Int!`; GraphQL returns identity
strings and the numeric quantity. Keep the object-returning property's own
identity flag unset. The field flag defaults to false and must be a boolean;
it changes only explicitly marked native int/string/UUID fields and supported
flat collections. Optional and element nullability, frozen values, defaults,
permissions, and native annotations are preserved. Do not cast values to
strings or annotate fields with `graphene.ID`; use native key annotations.
Regenerate client types after opting in and keep IDs as opaque strings.

## Declare manager relations

Annotate related fields with the manager class that should appear in generated
GraphQL. GeneralManager also recognizes collection wrappers, optional values,
and postponed annotations, so this pattern works when `from __future__ import
annotations` is enabled:

```python
from __future__ import annotations

from django.db import models

from general_manager import GeneralManager
from general_manager.bucket import Bucket
from general_manager.interface import DatabaseInterface


class User(GeneralManager):
    name: str

    class Interface(DatabaseInterface):
        name = models.CharField(max_length=100)


class Project(GeneralManager):
    owner: User | None
    reviewer_list: Bucket[User]

    class Interface(DatabaseInterface):
        owner = models.ForeignKey(
            User.Interface._model,
            on_delete=models.SET_NULL,
            null=True,
            blank=True,
        )
        reviewer = models.ManyToManyField(User.Interface._model, blank=True)


attribute_types = Project.Interface.get_attribute_types()
assert attribute_types["owner"]["type"] is User
assert attribute_types["reviewer_list"]["type"] is User
```

`DatabaseInterface` registers both managers and derives relation metadata from
the Django fields: `owner` maps directly to the foreign key, while the
`reviewer` many-to-many field is exposed as `reviewer_list`. When the schema is
built, `owner` becomes a single `User` object field and `reviewer_list` becomes
a paginated relation-list field. The same manager target is used for nested
relation filters, mutation relation inputs, and subscription identifiers.
`list[User]`, `tuple[User, ...]`, `set[User]`, `Optional[User]`, `"User"`, and
`"Bucket[User]"` are also supported. Keep one manager target in a relation
annotation; a union such as `User | Team` is ambiguous and does not produce
manager-relation behavior.

For existing or generated Django models, GeneralManager uses the model's
manager back-reference to recover the corresponding manager type. Register
manager modules during startup as described in the [installation
guide](../installation.md), and then use the generated field names in queries,
mutations, and subscriptions.

## Filter by identifier

Actual ORM primary keys and raw scalar foreign-key references use GraphQL
`ID`, and their outputs serialize as strings. Identifier equality and exact
filters (`id` and `id_Exact`) use `ID`; membership (`id_In`) uses `[ID]`.
This also applies to PKs with other names, such as `code`, and foreign-key
aliases such as `ownerId`. Single manager relations remain object fields and
collection relations remain paginated object fields.

Identity is derived from actual field metadata. An ordinary field named
`external_id` or a calculation/request composite business input named `id`
keeps its native scalar. Actual manager reference inputs use scalar IDs or
structured inputs when the referenced manager has composite constructor
inputs. Ordered comparisons such as `id_Gt` retain the native scalar and its
hints, including `BigIntScalar` where configured.

### Configure UUID and string primary keys

The GraphQL scalar does not change the Python/database type or configure the
manager constructor. Keep an explicit `Input(UUID)` or `Input(str)` for ORM
keys that do not use the default integer input:

```python
from uuid import UUID, uuid4

from django.db import models

from general_manager import GeneralManager
from general_manager.interface import DatabaseInterface
from general_manager.manager import Input


class UuidRecord(GeneralManager):
    class Interface(DatabaseInterface):
        input_fields = {"id": Input(UUID)}
        id = models.UUIDField(primary_key=True, default=uuid4)
        name = models.CharField(max_length=100)


class CodeRecord(GeneralManager):
    class Interface(DatabaseInterface):
        input_fields = {"id": Input(str)}
        code = models.CharField(max_length=32, primary_key=True)
        name = models.CharField(max_length=100)
```

`CodeRecord` exposes `code: ID` and `code`/`code_Exact`/`code_In` filters.
Its detail query still accepts the constructor argument `id: ID!`, which
addresses that model's `code` PK. UUID values remain `UUID` objects in Python;
code values remain strings.

### Declare native identity properties

Mark a property returning real keys explicitly, retaining its native Python
annotation and cache configuration:

```python
from django.db import models

from general_manager import GeneralManager, graph_ql_property
from general_manager.interface import DatabaseInterface


class Record(GeneralManager):
    class Interface(DatabaseInterface):
        id = models.BigAutoField(primary_key=True)
        sequence = models.IntegerField(default=7)

    @graph_ql_property(is_identifier=True, cache="none")
    def record_key(self) -> int:
        return self.id

    @graph_ql_property(is_identifier=True)
    def record_keys(self) -> list[int]:
        return list(type(self).filter(id=self.id).values_list("id", flat=True))

    @graph_ql_property
    def external_id(self) -> int:
        return self.sequence
```

The generated fields are `recordKey: ID`, `recordKeys: [ID]`, and
`externalId: Int`. UUID and string key properties use the same option with
`UUID` and `str` annotations. Homogeneous tuples and sets, optional values,
and optional elements are supported. Properties remain nullable. Do not mark
business numbers or object/manager results as identities; nested collections,
mixed unions, and unsupported native types fail schema generation with
`TypeError`. A property name or an ORM query annotation alone does not select
`ID`. Set `filterable=True` and configure backend filtering separately when
needed. Python and Bucket projection values remain native.

After opting in, regenerate the schema and client types and update comparisons
and cache keys to use strings. A scalar property previously returning the JSON
number `7` now returns `"7"`; a list returns `["7"]`. A key such as
`2147483648`, which previously exceeded GraphQL `Int` and produced an error,
now serializes as `"2147483648"`. See the [query recipe](../examples/graphql_queries.md#project-native-identities)
for an executable selection.

### Migrate existing clients

1. Change affected variable declarations from `Int` or `String` to `ID`, and
   their list equivalents to `[ID]`, keeping the intended nullability.
   Range variables and composite business inputs retain their native types.
2. Regenerate schema-derived client types after updating the schema.
3. Update equality comparisons, selected-item state, and normalized cache keys
   to use returned ID strings consistently. An integer-backed ID now returns
   `"42"`, so comparing it with the number `42` will no longer match.
4. Keep IDs as opaque strings. Avoid `Number`, `parseInt`, or other numeric
   coercion; large integers may lose precision, and UUID/string keys cannot be
   represented as numbers.
5. Check create-operation variables for required fields without real defaults.
   `NOT_PROVIDED` no longer appears as a schema default, so a declaration such
   as `$budget: MeasurementScalar` must become `$budget: MeasurementScalar!`
   when the generated create argument is required, even if a value is supplied.
6. If an older schema exposed a business field named `id` as an `ID` filter,
   change equality and membership variables to its native scalar, such as
   `Int` and `[Int]`. The existing filter operators remain available.

The [identifier filter concept](../concepts/graphql/filters_pagination.md#identifier-filters)
and [query patterns](../examples/graphql_queries.md#reuse-identifier-variables)
show requests using the updated declarations.

## Filter calculation managers by manager input

Calculation managers expose manager-typed `Input(...)` fields as direct nested
GraphQL relation filters. For example, this calculation accepts a `Project`
manager input:

```python
from datetime import date

from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input
from myapp.managers import Project


class ProjectCommercial(GeneralManager):
    class Interface(CalculationInterface):
        project = Input(Project)
        target_date = Input(date)
```

Filter the generated list field with the nested manager's fields:

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

The GraphQL resolver flattens the nested input to the Python lookup
`project__id=<projectId>` before calling the calculation bucket. Nested manager
lookups such as `{project: {name__startswith: "North"}}` are flattened in the
same way. This behavior applies when the calculation input metadata omits
relation descriptors; explicitly declared relation metadata and custom lookup
prefixes remain authoritative.

The same calculation filter contract applies when an input's
`possible_values` returns a Python iterable such as `list(Project.all())` or a
GeneralManager bucket. Manager equality, `_id`, `__in`, and nested lookups are
applied before generated relations are paginated, so each relation remains
scoped to its parent. A cached calculation value does not widen that scope.

## Declare typed calculation collections

Use a parameterized `Input(list[T])` when one calculation input represents an
ordered selection rather than one independent scalar choice. The declaration
generates a native GraphQL list and keeps the selection's order and duplicates:

```python
from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input
from myapp.managers import Employee


class WorkforceSummary(GeneralManager):
    class Interface(CalculationInterface):
        employees = Input(list[Employee], required=False)
        values = Input(list[int], required=False)
```

The generated detail arguments are `employeesId: [ID!]` and
`values: [Int!]`. Python construction requires an actual list; manager members
can be instances, IDs, or constructor mappings. Omission and explicit `null`
resolve to `None`, while `[]` is an explicit empty selection. GraphQL may apply
its normal singleton-to-list coercion, but null elements are rejected. Required
collections use `Input(list[T])` and produce an outer non-null argument.

```graphql
query Summary($employees: [ID!], $values: [Int!]) {
  workforceSummary(employeesId: $employees, values: $values) {
    employees { id name }
    values
  }
}
```

Use `filter(field=selection)` or `filter(field__in=[selection, ...])` for whole
selection comparisons; scalar `__in` behavior is unchanged and other collection
lookups are unsupported. A manager-list output first checks the owning field's
read permission, then omits members that fail their own manager-level read gate
without reordering permitted duplicates. The [collection-input cookbook](../examples/collection_inputs.md)
shows a complete calculation and query.

## Use measurements and large integers

Interface fields typed as `Measurement` are exposed with `MeasurementScalar` for
inputs. Send measurements as strings with a magnitude followed by a Pint unit,
for example `"12.5 m/s"` or `"100 EUR"`. Invalid measurement strings fail during
GraphQL input coercion with the same validation errors raised by
`Measurement.from_string()`.

```graphql
mutation UpdateInventory($id: ID!, $price: MeasurementScalar!) {
  updateInventory(id: $id, price: $price) {
    success
  }
}
```

Native fields marked with `graphql_scalar="bigint"` use `BigIntScalar`, as do
range comparisons on identities with that hint. Identity outputs and equality
filters use `ID` while retaining the native hint in metadata. `BigIntScalar`
returns large integers as strings to avoid precision loss in JavaScript clients
and accepts string or integer inputs. Boolean values are rejected explicitly, and
other non-coercible values fail with a scalar coercion error. Float and `Decimal`
values are accepted for compatibility but are truncated with Python `int(...)`,
so `1.9` becomes `1`.

The low-level scalar mapper only maps concrete scalar classes. Higher-level
schema generation unwraps optional fields and builds list fields before calling
that mapper; direct calls with annotations such as `Optional[int]`, `list[int]`,
or `Annotated[int, ...]` fall back to `String`.

## Create a manager with a manual primary key

Generated ORM create mutations omit auto-increment keys. Editable manual PKs
are accepted as `ID`, including a PK field named `id`. A PK without a default
is required; a model default makes it optional. For the `CodeRecord` above:

```graphql
mutation CreateCodeRecord($code: ID!, $name: String!) {
  createCodeRecord(code: $code, name: $name) {
    success
  }
}
```

```json
{"code": "CAT-042", "name": "Catalog entry"}
```

Create arguments retain literal model defaults. The `NOT_PROVIDED` sentinel
and callable defaults are absent from the schema; schema construction never
calls them. Omitting `id` when creating `UuidRecord` lets the ORM apply `uuid4`
at write time. Requiredness, model validation, and validation of explicit
`null` remain enforced.

## Partially update a generated manager

When an ORM interface supports `update`, GeneralManager generates an
`update<ManagerName>` mutation. The target argument remains `id: ID!`; writable
fields cannot overwrite it, and primary keys are excluded from update payload
fields. Each other editable field is optional. This makes it safe to change one
field without re-sending the rest of the object:

```graphql
mutation RenameProject($id: ID!) {
  updateProject(id: $id, name: "Renamed") {
    success
    project { id name score }
  }
}
```

If `score` has a model default such as `7`, the mutation above preserves the
project's existing score. The same rule applies when a nullable variable is
declared but omitted from the variables object:

```graphql
mutation SetOptionalScore($id: ID!, $score: Int) {
  updateProject(id: $id, score: $score) {
    success
    project { id score }
  }
}
```

With variables `{ "id": "42" }`, `score` is omitted and remains unchanged.
With `{ "id": "42", "score": null }`, `score` is explicitly cleared when the
field is nullable. Supplying a concrete value sets it, including a value equal
to its model default. Create mutations keep their normal model-default
behavior. See the [mutation concept](../concepts/graphql/schema_autogen.md#mutations),
the [GraphQL query cookbook](../examples/graphql_queries.md#update-only-the-fields-you-supply),
and the [API reference](../api/graphql.md#generated-crud-mutation-contract) for
the complete contract and error behavior.

### Keep mutation controls out of client input

Generated create and update schemas reserve `creator_id`, `ignore_permission`,
and `history_comment` for manager execution metadata. If an editable interface
field uses one of those names, schema generation raises a reserved-control
error; a relation alias such as `creator` is rejected when it normalizes to
`creator_id`. Rename the field or make it read-only.

Do not send `creatorId` or `ignorePermission` as generated GraphQL arguments;
they are undeclared and fail schema validation before the resolver runs. The
resolver's runtime guard also rejects `creator_id` or `ignore_permission` kwargs
with `BAD_USER_INPUT` before upload-token preflight, and checks again after
relation normalization. The actor always comes from `info.context.user`. Use the generated
`historyComment` argument when an audit comment is needed:

```graphql
mutation RenameProject($id: ID!) {
  updateProject(id: $id, name: "Renamed", historyComment: "Reviewed") {
    success
    project { id name }
  }
}
```

This is distinct from application code calling `GeneralManager.update()`
directly, where trusted server-side callers may still supply the manager's
execution metadata. See the [GraphQL security concept](../concepts/graphql/security.md),
the [mutation cookbook](../examples/graphql_queries.md#reserved-mutation-controls),
and the [API reference](../api/graphql.md#generated-crud-mutation-contract).

## Query generated lists

Generated list fields accept the arguments that the manager metadata supports,
including `filter`, `exclude`, `orderBy`, `page`, and
`pageSize`. Filters use the same lookup names as buckets. `filter` and
`exclude` may be GraphQL input objects or JSON object strings; malformed JSON
and decoded JSON values that are not objects are treated as empty filters.
Relation `none` filters are supported under `filter`, but not under `exclude`.
Top-level list queries and generated relation-list fields always include
nullable `page` and `pageSize`. They include nullable
`filter` and `exclude` only when a filter input type can be generated for the
manager, and nullable typed `orderBy` only when sortable fields exist.
Soft-delete managers also expose nullable `includeInactive`
on top-level list queries, which defaults to `false` and switches fallback list
loading from `Manager.all()` to `Manager.filter(include_inactive=True)` when
true. That fallback applies only when the resolver returns `None`; other falsey
bucket-like values are used as returned.

`orderBy` accepts an ordered list of typed objects such as
`[{field: name}, {field: date, direction: DESC}]`. The field is required and
the direction defaults to `ASC`; an empty list or `null` leaves existing order
unchanged. The first key is primary and later keys break ties. Generated order
enums expose a direct manager field as an order by that manager's identifier;
for example, `[{field: commercials}]` orders projects by the related commercial
ID. A directly related scalar is available through a one-hop key such as
`commercials__name`. Collection relations and multi-hop paths such
as `commercials__owner__name` are not exposed as sort options. Computed
`@graph_ql_property` values on the related manager are also excluded; only
sortable properties on the root manager are eligible. Top-level and generated
relation-list fields use the same list-valued sort contract.

## Preserve an exact authorized subset

When a custom resolver has already evaluated each candidate manager against an
instance-level policy, keep the originating bucket and reconstruct the result
with `with_instances()`. This preserves the bucket's backend-specific
representation. Non-database buckets that retain full identification mappings,
such as `CalculationBucket`, can preserve composite identifications without
replacing them with an `id__in` lookup. `DatabaseBucket` is ID-based and reads
each manager's `identification["id"]`:

```python
from collections.abc import Callable
from typing import TypeVar

from general_manager.bucket import Bucket
from general_manager.manager import GeneralManager


ManagerT = TypeVar("ManagerT", bound=GeneralManager)


def authorized_subset(
    bucket: Bucket[ManagerT],
    can_read: Callable[[ManagerT], bool],
) -> Bucket[ManagerT]:
    authorized = [manager for manager in bucket if can_read(manager)]
    return bucket.with_instances(authorized)
```

The returned bucket contains exactly the managers that passed the policy. Pass
the managers in the bucket's iteration order when the existing ordering must be
retained. Database-backed buckets reconstruct a source-ordered ID subset;
request-backed and calculation buckets materialize the supplied instances
without re-running their remote request or calculation domain. Generated
GraphQL list resolvers apply this contract automatically for row-level read
authorization, so custom resolvers only need it when they own that policy step.
See the [bucket concept](../concepts/models_entities.md#buckets) and the
[permission cookbook version](../examples/permission_cookbook.md#preserve-an-authorized-bucket-subset).

```graphql
query ActiveProjects($filters: ProjectFilterInput) {
  projectList(filter: $filters, orderBy: [{field: name}], page: 1, pageSize: 20) {
    items {
      id
      name
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

For typed variables, use the generated input list form and add an outer `!`
only when the client requires the variable itself to be non-null. For example:

```graphql
query SortedProjects($order: [ProjectOrderBy!]) {
  projectList(orderBy: $order) {
    items {
      id
      name
    }
  }
}
```

```json
{
  "order": [{"field": "commercials__name"}, {"field": "name"}]
}
```

Use a dedicated grouped field with a required `groupBy` argument:

```graphql
query ProjectsByDescendingStatus {
  projectGroups(groupBy: ["status"], orderBy: [{field: status, direction: DESC}]) {
    items { status name }
    pageInfo { totalCount }
  }
}
```

Ordinary `projectList` returns original records and does not accept `groupBy`.
Grouped items expose flat key and aggregate fields. Numbers sum, distinct text
values join, booleans use `any`, and dates/times use their maximum. Null values
are excluded; an all-null scalar returns null. A group's `id` is null unless
selected as a key. Relation ID aliases retain a shared non-null ID or return
null when references differ.

A grouped singular relation is exposed as a collection of distinct original
managers, with both list and grouping operations:

```graphql
query ProjectsByCommercial {
  projectGroups(groupBy: ["commercials_id"]) {
    items {
      commercialsId
      commercialsList {
        items { id name }
        pageInfo { totalCount }
      }
      commercialsGroups(groupBy: ["name"]) {
        items { name }
        pageInfo { totalCount }
      }
    }
    pageInfo { totalCount }
  }
}
```

Bucket-backed collections support the same pair recursively. Every nested
collection has independent filtering, exclusion, ordering and pagination scoped
to its parent. Plain `List[GraphQLType]` properties remain output lists with no
query controls; use a CalculationInterface and bucket for queryable calculated
collections.

Filtering and row authorization precede grouping. Grouping keys and ordering
fields must be readable, and aggregate fields check all contributing members.
Sorting supports eligible aggregate scalars before pagination. `totalCount`
counts groups. Empty results have `items: []` and `totalCount: 0`; nonpositive
pagination inputs are rejected. Incomplete remote pages cannot be grouped as
complete data. See the [grouping contract](../concepts/graphql/filters_pagination.md#grouping).

Legacy wrapper fields are no longer generated: select keys and aggregates
directly under `items`. Retrieve original records and their count through the
ordinary list endpoint.

## Subscribe to an authorized manager instance

Use the generated detail subscription with the same identification arguments as
the manager's query and mutation fields:

```graphql
subscription ProjectDetails($id: ID!) {
  onProjectChange(id: $id) {
    action
    item { id name }
  }
}
```

GeneralManager checks the request user's instance read permission before it
evaluates selected properties or registers channel listeners. It checks again
after deferred channel registration, before the initial `snapshot`, and before
each later event. If the target is missing or unreadable during initial
subscription setup, setup fails with a GraphQL error before a stream is returned.
If a later check finds the target missing or unreadable, the detail stream
completes without sending a `null` item or the queued action; any registered
listeners and channel groups are cleaned up. Permission-backend failures use the
normal GraphQL error path. Selected fields continue to use their own read rules,
so a readable target can still return `null` for a denied field. See the
[subscription concept](../concepts/graphql/subscriptions.md) and
[GraphQL API reference](../api/graphql.md#detail-subscription-authorization)
for the lifecycle and compatibility contract.

## Class-wide subscription permission checks

Class-wide subscriptions receive committed row-level events for any instance of
the manager class. Before an identified event is yielded, GeneralManager
rehydrates the manager and checks object-level read permission in an
`asyncio.to_thread` worker using the user captured when the subscription starts.
An unreadable or no-longer-existing object is suppressed, while an unexpected
permission exception propagates through the subscription error path. Aggregate
`refresh` events have no identification and yield `item = null` without this
object-level check.

## Protect subscription payload fields

Each selected normal, measurement, and stored-file field in a generated detail or
class-wide subscription applies the manager's usual field-level read permission.
For class-wide subscriptions, this happens after an identified event passes its
object-level check. Configure a field-specific rule on the manager's `Permission`
class as you would for a query:

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

With a subscription such as the one in the
[field-permission recipe](../examples/graphql_queries.md#subscribe-to-fields-with-read-permissions),
an authenticated non-staff user receives `name` and `internalNote: null`.
The denial is limited to that field; sibling fields and the event action still
resolve, and the denied field's value is not accessed. Permission evaluation,
value access, measurement conversion, and stored-file formatting for allowed
subscription fields run together in an async-safe worker. This also keeps
uncached foreign-key values to other GeneralManagers safe to load in the
subscription worker. Query and mutation field resolution stays synchronous. See
the [subscription concept](../concepts/graphql/subscriptions.md#signals-and-channels)
and [GraphQL API reference](../api/graphql.md#subscription-field-authorization)
for the full compatibility and error contract.

## Expose authorization hints

Use GraphQL permission capabilities when frontend code needs business-oriented authorization hints, such as whether the current user can rename a project. These fields are advisory only; backend permissions still enforce all reads and writes.

```python
from general_manager import GeneralManager
from general_manager.permission import AdditiveManagerPermission, object_capability


def can_rename_project(project, user):
    return project.status == "draft" and user.is_authenticated


class Project(GeneralManager):
    class Permission(AdditiveManagerPermission):
        graphql_capabilities = (
            object_capability("canRename", can_rename_project),
        )
```

Query the generated capability object:

```graphql
query {
  projectList {
    items {
      name
      capabilities {
        canRename
      }
    }
  }
}
```

For list-heavy checks, pass `batch_evaluator=` to `object_capability(...)`. The list resolver warms capability values for the returned page only when `capabilities` is selected.

For permission-backed, mutation-backed, and current-user examples, see
[GraphQL permission capabilities](../concepts/graphql/permission_capabilities.md).

## Add a custom mutation

Use `@graph_ql_mutation` for synchronous service-style mutations that do not map directly to the generated create, update, or delete operations. The decorator registers the mutation as soon as the module is imported and returns the original function, so the function remains directly callable in tests.

```python
from general_manager.api.mutation import graph_ql_mutation
from general_manager.permission.mutation_permission import MutationPermission


class CanArchiveProject(MutationPermission):
    __mutate__ = ["isAuthenticated"]


@graph_ql_mutation(permission=CanArchiveProject)
def archive_project(info, project: Project) -> Project:
    project.status = "archived"
    project.save()
    return project
```

Supported decorator forms are `@graph_ql_mutation`, `@graph_ql_mutation()`, `@graph_ql_mutation(SomePermission)`, and `@graph_ql_mutation(permission=SomePermission)`. Do not pass both a positional permission and `permission=`; the positional permission wins.

Annotate every argument except the parameter named `info`, and add a return annotation. `info` is skipped by name and can appear in any position, but conventionally comes first. `Optional[T]` creates a nullable argument, default values become Graphene defaults, and `list[T]` creates a list argument. GeneralManager arguments with no declared inputs or a single `id` input become `ID`; GeneralManager arguments with multiple interface inputs become generated nested input objects. Manager values are normalized before permission checks: existing instances are preserved, `None` stays `None`, mapping inputs construct `Manager(**value)`, and non-mapping inputs construct `Manager(value)`. For `list[Manager]` and `List[Manager]` arguments, each list item follows that same normalization. Other supported annotations use the same scalar/object mapping as generated GraphQL fields.

Return one type for a single output field, or a tuple of types for multiple output fields. Output field names are derived from the Python type name or type-alias name with a lower-case first letter, and every generated mutation also exposes `success`. Tuple return values are assigned to output fields in annotation order and must contain exactly one value per annotated output. A count mismatch is sanitized as `INTERNAL_SERVER_ERROR`; internal mismatch details are not exposed.

At execution time the wrapper normalizes GeneralManager arguments before permission checks and before calling the original function. A configured permission class receives `permission.check(normalized_kwargs, info.context.user)`. Registration is first-writer-wins for duplicate generated mutation class names. Generated names use `snake_to_camel`: the first underscore-delimited segment stays unchanged and later segments are title-cased. Missing parameter annotations raise `MissingParameterTypeHintError`, missing return annotations raise `MissingMutationReturnAnnotationError`, invalid return annotations raise `InvalidMutationReturnTypeError`, and duplicate output field names raise `DuplicateMutationOutputNameError`.

At the decorator boundary, explicit `GraphQLError` instances are preserved, while `ValidationError` and `PublicGraphQLError` retain their intended public behavior. `PermissionError` returns only `Permission denied.` with code `PERMISSION_DENIED`. Every other ordinary `Exception`, including `ValueError`, returns `An internal server error occurred.` with code `INTERNAL_SERVER_ERROR` and an opaque `errorId`; server logs retain the original details and matching `error_id` for correlation. Migrate client-facing `ValueError` uses to `PublicGraphQLError`, or to `ValidationError` for validation.

Use the [safe GraphQL mutation error recipe](../examples/graphql_error_handling.md)
for a copy-ready resolver and client handler. The
[GraphQL API reference](../api/graphql.md) documents the exact error signatures,
extensions, and compatibility behavior.
