# Enforce Permissions Step by Step

This tutorial walks through building and validating permissions for a `GeneralManager`. It uses the built-in [`ManagerBasedPermission`](../concepts/permission/manager_based_permission.md) class and the reusable checks from the [`permission_checks` registry](../api/permission.md#registry-and-reusable-checks).

## 1. Model the access rules

Start by encoding who may create, read, update, or delete each attribute. `ManagerBasedPermission` exposes class attributes (`__read__`, `__create__`, `__update__`, `__delete__`) plus per-field overrides. Every string in these lists maps to a registered permission function.

```python
from general_manager.manager import GeneralManager
from general_manager.permission.manager_based_permission import ManagerBasedPermission


class Project(GeneralManager):
    creator_id: int
    status: str
    sensitive_note: str

    class Permission(ManagerBasedPermission):
        __read__ = ["isAuthenticated"]
        __create__ = ["isAuthenticated"]
        __update__ = ["isSelf", "inGroup:project_admins"]
        __delete__ = ["isAdmin"]

        sensitive_note = {
            "read": ["inGroup:project_admins"],
            "update": ["inGroup:project_admins"],
        }
```

- Default lists apply to every attribute.
- Attribute overrides restrict specific fields without impacting the rest.
- Checks such as `isAuthenticated`, `isSelf`, and `inGroup` are registered in the [`permission_checks` registry](../api/permission.md#registry-and-reusable-checks).

If you want project-wide defaults for permission classes that omit these lists,
set:

```python
GENERAL_MANAGER = {
    "DEFAULT_PERMISSIONS": {
        "READ": ["public"],
        "CREATE": ["isAuthenticated"],
        "UPDATE": ["isAuthenticated"],
        "DELETE": ["isAuthenticated"],
    }
}
```

When this setting is absent, `ManagerBasedPermission` falls back to the values
shown above.

## 2. Attach filters for queryset access

Read permissions do more than guard individual attribute access. The GraphQL API calls [`get_permission_filter`](../concepts/graphql/security.md#permission-enforcement) to narrow the queryset before results are returned. Each permission function may provide a filter companion.

```python
from general_manager.permission.permission_checks import register_permission


@register_permission(
    "belongsToCustomer",
)
def can_access_customer(instance, user, config):
    customer_field = config[0]
    return getattr(instance, customer_field).owner_id == user.id
```

Add `"belongsToCustomer:customer"` to `__read__` to produce filters automatically when the GraphQL layer runs the resolver. Use `python -m pytest` with fixtures that hit `get_permission_filter()` to ensure the queryset matches expectations.

## 3. Chain permissions with `__based_on__`

Complex domains often reuse another manager's permission logic. Setting `__based_on__` delegates to that nested manager. The implementation documented under [`ManagerBasedPermission`](../api/permission.md#core-classes) validates the attribute, forwards CRUD checks, and merges queryset filters.

```python
class ProjectDocument(GeneralManager):
    project: Project
    file_path: str

    class Permission(ManagerBasedPermission):
        __based_on__ = "project"
        __create__ = ["isAuthenticated"]
        file_path = {"read": ["isAuthenticated"], "update": ["isSelf"]}
```

When a user fails a delegated check, the action is denied immediately. Filters returned from `Project.Permission.get_permission_filter()` are namespaced as `{"filter": {"project__...": ...}}`, keeping queryset logic consistent.

If `project` is `None` at runtime, implicit CRUD rules on the current
permission fall back to `GENERAL_MANAGER["DEFAULT_PERMISSIONS"]` (or to
`public` for reads and `isAuthenticated` for writes when that setting is not
configured). Explicitly declared CRUD lists still win.

## 4. Validate at runtime

`BasePermission` exposes helpers used by managers and mutations to enforce permissions. Call them directly in tests or custom workflows:

```python
from general_manager.permission.base_permission import BasePermission

payload = {"status": "active"}
BasePermission.check_create_permission(payload, Project, request_user=user)
```

- `check_create_permission`, `check_update_permission`, and `check_delete_permission` raise `PermissionCheckError` when a rule fails.
- [`PermissionDataManager`](../api/permission.md#data-access-helpers) merges the old and new state for update checks, making diff-based rules straightforward.

## 5. Capture audit trails

Every permission check may emit audit events when logging is enabled (see the audit logging tutorial for setup). The audit payload contains:

- `action`: `"create"`, `"read"`, `"update"`, `"delete"`, or `"mutation"`
- `attributes`: the fields evaluated
- `permissions`: the expressions considered, including those from `__based_on__`
- `bypassed`: `True` when a superuser short-circuits the evaluation

Use these events in observability pipelines to verify that your permission rules fire as expected and to detect denied access attempts.

## 6. Recommended testing strategy

1. Exercise happy-path scenarios where authorised users succeed.
2. Attempt the same operations with unauthorised users and assert on the raised `PermissionCheckError`.
3. For list endpoints, inspect the queryset returned by `get_permission_filter()` and ensure it hides records belonging to other users.
4. If audit logging is enabled during tests, capture emitted events using a stub logger to assert on the recorded metadata.

With these steps, your permission classes stay in sync with business requirements while remaining transparent to reviewers and observability tooling.
