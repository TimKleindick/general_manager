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

No explicit `@dataclass` decorator is required. Standard dataclass defaults
and `field(default_factory=...)` work, and construction is frozen after the
instance is created. The declaration above becomes the GraphQL object
`ProjectHourType`; it does not create a query, mutation, subscription, filter,
capability field, or manager lifecycle. It is available only through a field
that references it, such as `projectHours` in the example.

The output mapper accepts built-in scalar annotations, `Measurement`, registered
managers, registered output types, and `list[T]`, `tuple[T, ...]`, or `set[T]`.
Use `T | None` for nullable values. Required annotations become non-null
GraphQL fields, while optional annotations become nullable fields; collection
elements follow the same rule. Bare collections, heterogeneous tuples,
`Any`, `Annotated[...]`, unresolved references, and multi-target unions are
invalid output annotations and fail schema generation with a field-specific
error. Output declarations themselves are output-only: input objects and
automatic root operations are not generated.

The `@graph_ql_property` on the owning manager remains the authorization
boundary. A nested manager field still uses that manager's normal GraphQL
read permissions. Plain scalar fields and nested `GraphQLType` fields do not
receive an independent permission check, so omit or pre-authorize sensitive
values in the owning property.

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

Identifier equality filters (`id`, `id_Exact`, and `id_In`) use the GraphQL
`ID` scalar, matching detail-query arguments. Ordered comparisons such as
`id_Gt` retain the identifier's underlying numeric scalar when available.

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

## Use measurements and large integers

Interface fields typed as `Measurement` are exposed with `MeasurementScalar` for
inputs. Send measurements as strings with a magnitude followed by a Pint unit,
for example `"12.5 m/s"` or `"100 EUR"`. Invalid measurement strings fail during
GraphQL input coercion with the same validation errors raised by
`Measurement.from_string()`.

```graphql
mutation UpdateInventory($id: Int!, $price: MeasurementScalar!) {
  updateInventory(id: $id, price: $price) {
    success
  }
}
```

Fields marked with `graphql_scalar="bigint"` use `BigIntScalar`. The scalar
returns large integers as strings to avoid precision loss in JavaScript clients
and accepts string or integer inputs. Boolean values are rejected explicitly, and
other non-coercible values fail with a scalar coercion error. Float and `Decimal`
values are accepted for compatibility but are truncated with Python `int(...)`,
so `1.9` becomes `1`.

The low-level scalar mapper only maps concrete scalar classes. Higher-level
schema generation unwraps optional fields and builds list fields before calling
that mapper; direct calls with annotations such as `Optional[int]`, `list[int]`,
or `Annotated[int, ...]` fall back to `String`.

## Query generated lists

Generated list fields accept the arguments that the manager metadata supports,
including `filter`, `exclude`, `sortBy`, `reverse`, `groupBy`, `page`, and
`pageSize`. Filters use the same lookup names as buckets. `filter` and
`exclude` may be GraphQL input objects or JSON object strings; malformed JSON
and decoded JSON values that are not objects are treated as empty filters.
Relation `none` filters are supported under `filter`, but not under `exclude`.
Top-level list queries and generated relation-list fields always include
nullable `reverse`, `page`, `pageSize`, and `groupBy`. They include nullable
`filter` and `exclude` only when a filter input type can be generated for the
manager, and nullable `sortBy` only when sortable fields exist. If omitted,
`reverse` defaults to `false`; `reverse: true` has no effect when `sortBy` is
omitted or `null`. Soft-delete managers also expose nullable `includeInactive`
on top-level list queries, which defaults to `false` and switches fallback list
loading from `Manager.all()` to `Manager.filter(include_inactive=True)` when
true. That fallback applies only when the resolver returns `None`; other falsey
bucket-like values are used as returned.

`sortBy` accepts an ordered list of keys. GraphQL's list coercion also keeps an
inline singleton such as `sortBy: name` valid. An empty list, `sortBy: []`, is a
no-op equivalent to omitting `sortBy` or passing `sortBy: null`. The first key is
primary, later keys break ties, and `reverse: true` reverses every key in the
list. Generated sort enums expose a direct manager field as a sort by that
manager's identifier; for example, `sortBy: commercials` orders projects by the
related commercial ID. A directly related scalar is available through a one-hop
key such as `commercials__name`. Collection relations and multi-hop paths such
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
  projectList(filter: $filters, sortBy: name, page: 1, pageSize: 20) {
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

For typed variables, migrate a previous singleton enum declaration such as
`$sort: ProjectSortByOptions` to the list form
`$sort: [ProjectSortByOptions!]`. Add an outer `!` only when the client requires
the variable itself to be non-null. For example:

```graphql
query SortedProjects($sort: [ProjectSortByOptions!]) {
  projectList(sortBy: $sort) {
    items {
      id
      name
    }
  }
}
```

```json
{
  "sort": ["commercials__name", "name"]
}
```

Use `groupBy: [""]` to call the bucket's default grouping behavior, or pass
explicit field names such as `groupBy: ["status"]`. Filtering and exclusion run
before grouping; `sortBy` runs after grouping and before pagination. Without
`groupBy`, `sortBy` orders records as usual. With `groupBy`, it orders the
returned grouped manager objects, so sorting by a grouping key honors `reverse`,
while sorting by another exposed field uses that field's aggregated group value.
`totalCount` is computed after permission filtering, user filters, excludes,
grouping, and sorting, but before page slicing.

Grouping by a scalar foreign-key identifier also normalizes the aggregated
manager value. Rows that point to the same related manager collapse to one
object; a group containing distinct related managers returns their union bucket.
For example, this groups projects by the related commercial identity while
returning the generated relation field:

```graphql
query ProjectsByCommercial {
  projectList(groupBy: ["commercials_id"]) {
    items { commercials { id name } }
  }
}
```

```graphql
query ProjectsByDescendingStatus {
  projectList(groupBy: ["status"], sortBy: status, reverse: true) {
    items {
      status
    }
    pageInfo {
      totalCount
    }
  }
}
```

Invalid `sortBy` enum values are rejected by Graphene; invalid filter, grouping,
or slicing values propagate the corresponding bucket or resolver error. When
grouping is active, pagination slices grouped manager objects rather than the
original ungrouped rows, and the GraphQL `items` field still uses the manager's
generated item type. If no rows match, a paginated grouped query returns an
empty `items` list with `totalCount: 0`; negative `page` or `pageSize` values
still raise the normal input error.

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
resolve. Permission evaluation for subscription fields runs in an async-safe
worker, while value access, measurement conversion, and stored-file formatting
remain in GraphQL's execution context. Query and mutation field resolution stays
synchronous. See the [subscription concept](../concepts/graphql/subscriptions.md#signals-and-channels)
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
