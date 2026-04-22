# Security

Security in the GraphQL layer relies on permission checks and robust error handling.

## Permission enforcement

- Every query obtains read filters from the manager's `Permission` class via `get_read_permission_filter()`.
- Mutations invoke `check_create_permission`, `check_update_permission`, or `check_delete_permission` before executing. Permission errors translate into `success: false` responses with descriptive messages.
- Attribute-level restrictions hide protected fields even when the user can access the object.
- Optional GraphQL permission capabilities expose advisory boolean hints for clients. They do not replace read or mutation permission checks.

Always execute GraphQL resolvers through managers; do not reach directly for Django models, or you will bypass permission rules.

## Capability hints

Declare object capability hints on the manager's nested `Permission` class:

```python
from general_manager.permission import AdditiveManagerPermission, object_capability


def can_rename(project, user):
    return project.status == "draft" and user.is_authenticated


class Project(GeneralManager):
    class Permission(AdditiveManagerPermission):
        graphql_capabilities = (
            object_capability("canRename", can_rename),
        )
```

Managers with declarations expose a generated non-null capability object:

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

Use `batch_evaluator=` on `object_capability(...)` for list pages that would otherwise repeat expensive policy checks. Batch warmup runs only when `items { capabilities { ... } }` is selected, and any evaluator exception is logged and resolved as `false`.

Projects can also configure current-user capability hints:

```python
GENERAL_MANAGER = {
    "GRAPHQL_GLOBAL_CAPABILITIES_PROVIDER": "my_app.auth.GraphQLCapabilities",
}


class GraphQLCapabilities:
    graphql_fields = {"username": str}
    graphql_capabilities = (
        object_capability("canOpenAdmin", lambda _user, request_user: request_user.is_staff),
    )

    def resolve_username(self, user, info):
        return user.username
```

When no provider is configured, the schema does not expose `me`.

## Authentication

Set `AUTHENTICATION_BACKENDS` and middleware according to your project. The GraphQL view expects `info.context.user` to be populated. Denied permissions return a GraphQL error or an error entry in the mutation payload.

## Error propagation

Validation errors from interfaces and rules bubble up as GraphQL `GraphQLError` instances. Use try/except blocks in custom resolvers to add more context while preserving the original message for clients.

## Hardening tips

- Enable query depth or complexity limits in your GraphQL server to avoid expensive queries.
- Combine permissions with `filter` arguments so users cannot guess identifiers of objects they do not own.
- Log denied permissions with the manager name and user ID to monitor suspicious behaviour.
- Avoid exposing `ignore_permission=True` paths in public APIs; reserve them for internal management commands.
