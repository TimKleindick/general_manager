# Custom GraphQL Mutations

Use `@graph_ql_mutation` when an operation does not fit the generated create,
update, or delete mutations. The decorator turns a typed Python function into a
Graphene mutation and registers it with the generated schema.

## Define a mutation

```python
from general_manager.api.mutation import graph_ql_mutation


@graph_ql_mutation
def publish_project(info, project_id: int, note: str | None = None) -> Project:
    project = Project(id=project_id)
    return project.update(
        status="published",
        publication_note=note,
        creator_id=getattr(info.context.user, "id", None),
    )
```

The function name becomes the GraphQL field name through `snake_to_camel`: the
first underscore-delimited segment is kept unchanged and each later segment is
title-cased. In this example, `publish_project` is exposed as `publishProject`.

The decorated module must be imported before GeneralManager builds the GraphQL
schema. Define mutations in an application module that Django imports during
startup, or import that module from your application setup.
Registration is first-writer-wins for the generated mutation class name: if
another custom mutation has already registered the same camel-case name, the
later decorator still returns the original function but leaves the existing
GraphQL mutation in the registry.

## Resolver contract

The decorator builds the GraphQL arguments and payload from type annotations:

- Name the first resolver parameter `info` to receive GraphQL resolver
  information. It is not exposed as a mutation argument.
- Add a type annotation to every other parameter.
- Add a return annotation.
- Use `T | None` or `Optional[T]` for optional inputs.
- Use `list[T]` or `List[T]` for list inputs.
- Default parameter values become GraphQL default values.

Basic Python types such as `str`, `int`, `float`, and `bool` map to their
corresponding GraphQL scalars. A parameter annotated with a `GeneralManager`
subclass is exposed as a GraphQL `ID` when the manager declares no interface
inputs or uses the conventional single `id` input.

The resolver receives manager-typed arguments as manager instances. The
decorator normalizes GraphQL input before calling mutation permissions or the
resolver: existing manager instances are preserved, `None` stays `None`,
mapping inputs are passed to the manager constructor as `Manager(**value)`, and
non-mapping inputs are passed as the single positional value `Manager(value)`.
For `list[Manager]` and `List[Manager]` arguments, each list item follows the
same normalization. Those constructor paths then cast values through the
manager's `Interface.input_fields`.

```python
@graph_ql_mutation
def archive_project(info, project: Project) -> Project:
    return project.update(
        status="archived",
        creator_id=getattr(info.context.user, "id", None),
    )
```

Managers with multiple identification inputs are exposed as input objects:

```python
@graph_ql_mutation
def preview_price(info, quote: PriceQuote) -> PriceQuote:
    return quote
```

```graphql
mutation {
  previewPrice(quote: { market: "de", sku: "A-100" }) {
    success
    priceQuote {
      sku
    }
  }
}
```

Generated manager input-object types are cached by the manager class module and
qualified name, not just the short class name. Nested manager inputs use `ID`
for single-id managers and nested input objects for multi-input managers. The
input object name is derived from that full manager identifier with non
alphanumeric characters replaced by underscores.

## Return payloads

Every generated mutation payload includes `success`, which is `true` when the
resolver completes successfully. The other payload field is derived from the
return type by lowercasing its first character:

- `Project` becomes `project`.
- `InvoiceResult` becomes `invoiceResult`.
- `str` becomes `str`.

For example:

```graphql
mutation {
  publishProject(projectId: 42) {
    success
    project {
      id
      status
    }
  }
}
```

A resolver may return a typed tuple to expose multiple payload fields:

```python
type PublishedProject = Project
type StatusMessage = str


@graph_ql_mutation
def publish_project(info, project_id: int) -> tuple[PublishedProject, StatusMessage]:
    project = Project(id=project_id).update(
        status="published",
        creator_id=getattr(info.context.user, "id", None),
    )
    return project, "Project published"
```

Tuple output types must produce unique field names. Type aliases are useful when
multiple values share the same underlying Python type. A type alias exposes the
alias name but maps through the alias target when building the Graphene field.
At runtime, tuple values are assigned to output fields in annotation order; the
current implementation does not validate that the returned tuple length exactly
matches the annotated tuple length.

## Protect a mutation

Pass a `MutationPermission` class to the decorator when the operation requires
authorization:

```python
from typing import ClassVar

from general_manager.permission.mutation_permission import MutationPermission


class PublishProjectPermission(MutationPermission):
    __mutate__: ClassVar[list[str]] = ["isAuthenticated"]


@graph_ql_mutation(permission=PublishProjectPermission)
def publish_project(info, project_id: int) -> Project:
    ...
```

The permission class receives the normalized mutation arguments and
`info.context.user` before the resolver runs. Manager-typed arguments are
manager instances there too. Declare `__mutate__` for permissions that apply to
the entire payload, and add field-specific `list[str]` class attributes when a
single argument needs an extra gate. For that argument, both the global gate and
the field-specific gate must pass. Arguments without a field-specific list use
the global gate alone. Omitting `__mutate__` denies by default; use
`__mutate__ = []` only when the global gate should allow all fields and any
field-specific lists should provide the remaining restrictions. `__mutate__`
follows normal class inheritance and must be a `list` containing only strings;
tuples, mixed lists, and other sequences deny the global gate. An empty
field-specific list allows that field gate. Field gates are collected only from
non-dunder attributes declared directly on the concrete permission class whose
value is a `list` containing only strings. Mixed lists, tuples, and other
sequences are ignored. The positional form is equivalent:

```python
@graph_ql_mutation(PublishProjectPermission)
def publish_project(info, project_id: int) -> Project:
    ...
```

Using `@graph_ql_mutation` without a permission class makes the custom mutation
public at the decorator layer. The manager methods called by the resolver can
still enforce their own create, update, or delete permissions.

## Error handling

The generated mutation converts errors handled by GeneralManager into GraphQL
errors. Both Django's `ValidationError`, commonly raised by manager methods, and
a standard `ValueError` raised directly by a resolver become `BAD_USER_INPUT`
errors:

```python
from django.core.exceptions import ValidationError


@graph_ql_mutation
def update_project(info, project_id: int, mode: str) -> Project:
    if mode == "resolver-error":
        raise ValueError("Unknown update mode.")
    if mode == "validation-error":
        raise ValidationError("The project cannot be updated.")
    return Project(id=project_id)
```

Exceptions outside GeneralManager's handled error set surface through GraphQL's
normal error handling. Keep business logic in manager methods where possible so
validation, permissions, history, and other framework behavior remain
consistent.
