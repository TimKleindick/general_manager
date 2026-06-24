# Expose Managers via GraphQL

Managers with an `Interface` are registered during GeneralManager startup and receive generated query, mutation, and subscription fields based on their interface capabilities.

## Filter by identifier

Identifier equality filters (`id`, `id_Exact`, and `id_In`) use the GraphQL
`ID` scalar, matching detail-query arguments. Ordered comparisons such as
`id_Gt` retain the identifier's underlying numeric scalar when available.

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

```graphql
query ActiveProjects($filters: ProjectFilterInput) {
  projectList(filter: $filters, sortBy: NAME, page: 1, pageSize: 20) {
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

Use `groupBy: [""]` to call the bucket's default grouping behavior, or pass
explicit field names such as `groupBy: ["status"]`. `totalCount` is computed
after permission filtering, user filters, excludes, sorting, and grouping, but
before page slicing. Invalid `sortBy` enum values are rejected by Graphene;
invalid filter, grouping, or slicing values propagate the corresponding bucket
or resolver error. When grouping is active, pagination slices grouped manager
objects rather than the original ungrouped rows, and the GraphQL `items` field
still uses the manager's generated item type.

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

Return one type for a single output field, or a tuple of types for multiple output fields. Output field names are derived from the Python type name or type-alias name with a lower-case first letter, and every generated mutation also exposes `success`. Tuple return values are assigned to output fields in annotation order; the current wrapper does not validate that the returned tuple length exactly matches the annotated tuple length.

At execution time the wrapper normalizes GeneralManager arguments before permission checks and before calling the original function. A configured permission class receives `permission.check(normalized_kwargs, info.context.user)`. Registration is first-writer-wins for duplicate generated mutation class names. Generated names use `snake_to_camel`: the first underscore-delimited segment stays unchanged and later segments are title-cased. Missing parameter annotations raise `MissingParameterTypeHintError`, missing return annotations raise `MissingMutationReturnAnnotationError`, invalid return annotations raise `InvalidMutationReturnTypeError`, and duplicate output field names raise `DuplicateMutationOutputNameError`. Handled GeneralManager domain errors are converted to GraphQL errors; other exceptions propagate.
