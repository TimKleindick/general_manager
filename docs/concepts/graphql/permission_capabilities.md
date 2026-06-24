# GraphQL Permission Capabilities

GraphQL permission capabilities expose small boolean authorization hints to
frontend clients. They answer questions such as "can the current user rename
this project?" without exposing Django groups, role names, or raw permission
strings.

Capabilities are deliberately advisory. They help a client decide which actions
to show, but they do not replace backend authorization. Reads still use manager
read filters and mutations still call `check_create_permission`,
`check_update_permission`, `check_delete_permission`, or the configured
`MutationPermission`.

## Capability model

Declare capabilities on a manager's nested `Permission` class:

```python
from django.db.models import CharField

from general_manager import GeneralManager
from general_manager.interface import DatabaseInterface
from general_manager.permission import AdditiveManagerPermission, object_capability


def can_rename_project(project, user):
    return project.status == "draft" and user.is_authenticated


class Project(GeneralManager):
    class Interface(DatabaseInterface):
        name = CharField(max_length=100)
        status = CharField(max_length=20)

    class Permission(AdditiveManagerPermission):
        __read__ = ["public"]
        __update__ = ["isAuthenticated"]
        graphql_capabilities = (
            object_capability("canRename", can_rename_project),
        )
```

Managers with at least one valid declaration expose a generated `capabilities`
field:

```graphql
query {
  projectList(sortBy: name) {
    items {
      name
      capabilities {
        canRename
      }
    }
  }
}
```

The response contains non-null booleans:

```json
{
  "data": {
    "projectList": {
      "items": [
        {
          "name": "Apollo",
          "capabilities": {
            "canRename": true
          }
        }
      ]
    }
  }
}
```

Name capability fields in stable business language. Prefer `canRename`,
`canArchiveProject`, or `canCreateDerivative` over UI-specific names such as
`showRenameButton`.

Capability declarations also carry GraphQL field descriptions. The helper
functions provide default descriptions, and you can pass `description=` when a
client needs more precise business language in schema introspection.
Entries on `Permission.graphql_capabilities` that are not
`GraphQLPermissionCapability` instances are ignored when the schema is built.

## Object capabilities

Use `object_capability(...)` when the rule is domain-specific and cannot be
expressed by an existing manager operation or custom mutation permission.

```python
from general_manager.permission import AdditiveManagerPermission, object_capability


def can_lock_project(project, user):
    return (
        user.is_authenticated
        and project.status == "draft"
        and project.owner_id == user.id
    )


class Permission(AdditiveManagerPermission):
    __read__ = ["public"]
    graphql_capabilities = (
        object_capability(
            "canLock",
            can_lock_project,
            description="Whether the current user can lock this draft project.",
        ),
    )
```

The evaluator receives `(instance, user)` and should return `True` or `False`.
If it raises an exception, the GraphQL resolver logs the failure and returns
`false` for the capability. The optional `description` text is exposed on the
generated GraphQL field.

## Permission-backed capabilities

Use `permission_capability(...)` when a capability should preview the same
manager permission path that a generated create, update, or delete mutation will
use.

```python
from general_manager import GeneralManager
from general_manager.permission import AdditiveManagerPermission, permission_capability


class Project(GeneralManager):
    class Permission(AdditiveManagerPermission):
        __read__ = ["public"]
        __update__ = ["isAuthenticated"]
        __delete__ = ["isAdmin"]


Project.Permission.graphql_capabilities = (
    permission_capability(
        Project,
        "update",
        name="canUpdateProject",
        payload=lambda project, _user: {"name": project.name},
        description=(
            "Whether the current user can submit an update for this project."
        ),
    ),
    permission_capability(
        Project,
        "delete",
        name="canDeleteProject",
    ),
)
```

`permission_capability(...)` delegates to the manager permission class:

- `action="create"` calls `check_create_permission(payload, target, user)`
- `action="update"` calls `check_update_permission(payload, instance, user)`
- `action="delete"` calls `check_delete_permission(instance, user)`

### Payloads

`payload` means "the input data that should be handed to the permission check
while answering this capability field." Capability evaluation does not execute a
mutation, so GeneralManager cannot infer the future mutation input by itself.
Use `payload=` when a permission rule depends on field values that would
normally come from the GraphQL mutation arguments.

For `permission_capability(...)`, the resolved payload is used like this:

- `action="create"` passes the payload to
  `check_create_permission(payload, target, user)`.
- `action="update"` passes the payload to
  `check_update_permission(payload, instance, user)`.
- `action="delete"` ignores the payload because
  `check_delete_permission(instance, user)` does not accept one.

The payload can be a static mapping:

```python
permission_capability(
    Project,
    "create",
    name="canCreateDraftProject",
    payload={"status": "draft"},
)
```

Or it can be a callable:

```python
permission_capability(
    Project,
    "update",
    name="canRenameProject",
    payload=lambda project, user: {
        "name": project.name,
        "requested_by": user.id,
    },
)
```

The callable receives `(instance, user)`:

- `instance` is the object whose GraphQL `capabilities` field is being resolved.
  In a `projectList { items { capabilities { canRenameProject } } }` query, it
  is the current `Project` item. In a detail query, it is that single resolved
  object. For create previews, it is still the object currently being rendered,
  not a new unsaved object.
- `user` is the request user after GeneralManager's standard permission user
  resolution. It is the same user object that is passed to the permission check.

Return a mapping whose keys match the field or argument names your permission
class expects. The mapping is copied to a plain `dict` before evaluation, so the
lambda should not rely on mutating shared state. If the payload callable raises
an exception, the capability fails closed and GraphQL returns `false`.

## Mutation-backed capabilities

Use `mutation_capability(...)` when the boolean should preview a custom
GraphQL mutation guarded by `MutationPermission`.

```python
from general_manager.api.mutation import graph_ql_mutation
from general_manager.permission import MutationPermission, mutation_capability


class ArchiveProjectPermission(MutationPermission):
    __mutate__ = ["isAuthenticated"]
    status = ["matches:status:draft"]


@graph_ql_mutation(permission=ArchiveProjectPermission)
def archive_project(info, status: str):
    ...


class Project(GeneralManager):
    class Permission(AdditiveManagerPermission):
        __read__ = ["public"]


Project.Permission.graphql_capabilities = (
    mutation_capability(
        archive_project,
        name="canArchiveProject",
        payload=lambda project, _user: {"status": project.status},
    ),
)
```

The capability calls the mutation permission's `check(payload, user)` method.
Permission errors return `false`; successful checks return `true`.

`mutation_capability(...)` uses the same payload rules as
`permission_capability(...)`: a mapping is used directly, and a callable
receives `(instance, user)`. The returned mapping should contain the arguments
that the mutation permission validates. For example, if
`ArchiveProjectPermission` checks a `status` argument, the payload should include
`{"status": project.status}` or another value that represents the action being
previewed.

## Current-user capabilities

Object capabilities live on manager objects. For global user-specific hints,
configure a current-user capability provider in Django settings:

```python
GENERAL_MANAGER = {
    "GRAPHQL_GLOBAL_CAPABILITIES_PROVIDER": "my_app.auth.GraphQLCapabilities",
}
```

The provider can expose explicit fields on `me` and boolean fields under
`me.capabilities`:

```python
from typing import ClassVar

from general_manager.permission import object_capability


class GraphQLCapabilities:
    graphql_fields: ClassVar[dict[str, type]] = {"username": str}
    graphql_capabilities = (
        object_capability(
            "canOpenAdmin",
            lambda current_user, request_user: request_user.is_staff,
        ),
    )

    def resolve_username(self, user, info):
        return user.username
```

Clients can then query:

```graphql
query {
  me {
    username
    capabilities {
      canOpenAdmin
    }
  }
}
```

If `GRAPHQL_GLOBAL_CAPABILITIES_PROVIDER` is not configured, the schema does
not expose a synthetic `me` object.

## List performance

For list pages, an object capability can provide a `batch_evaluator`:

```python
from general_manager.permission import AdditiveManagerPermission, object_capability


def can_rename_project(project, user):
    return project.status == "draft" and user.is_authenticated


def can_rename_projects(projects, user):
    return [
        project.status == "draft" and user.is_authenticated
        for project in projects
    ]


class Permission(AdditiveManagerPermission):
    graphql_capabilities = (
        object_capability(
            "canRename",
            can_rename_project,
            batch_evaluator=can_rename_projects,
        ),
    )
```

List resolvers warm capability values for the returned page only when the query
selects `items { capabilities { ... } }`. The batch evaluator may return a
sequence of booleans in the same order as the input instances, or a mapping from
instances to booleans.

Batch evaluators fail closed. If the batch callable raises, returns a sequence
whose length does not match the warmed page, or omits an instance from a mapping
result, GeneralManager caches `false` for the affected capability values rather
than falling back to repeated per-object checks.

Capability results are cached for the current GraphQL operation using the
manager type, object identity, user identity, and capability name. Batched HTTP
operations and subscription events do not share cached results across
operations.

## Security checklist

- Treat capability fields as hints for rendering clients, not as authorization
  gates.
- Keep the real operation guarded by manager permissions or
  `MutationPermission`.
- Use stable domain names for GraphQL fields.
- Add a `batch_evaluator` for expensive list checks.
- Keep capability declarations on `Permission.graphql_capabilities`, not on
  `Interface.configured_capabilities`. Interface capabilities compose backend
  interface behavior; GraphQL permission capabilities are a frontend
  authorization contract.
