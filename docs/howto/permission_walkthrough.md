# Enforce Permissions Step by Step

This tutorial walks through building and validating permissions for a `GeneralManager`. It uses the explicit additive [`AdditiveManagerPermission`](../concepts/permission/manager_based_permission.md) class and the reusable checks from the [`permission_checks` registry](../api/permission.md#registry-and-reusable-checks).

Start with [Define permission rules](add_permission_rule.md) for a complete,
runnable example. This guide explains additional patterns to adapt to your own
managers; its declarations omit application-specific interface fields and data.
The [permission overview](../concepts/permission/index.md) explains which entry
points enforce these policies. Plain Python reads require explicit authorization
before their values are returned to a user.

## 1. Model the access rules

Start by encoding who may create, read, update, or delete each attribute. `AdditiveManagerPermission` exposes class attributes (`__read__`, `__create__`, `__update__`, `__delete__`) plus per-field overrides. Every string in these lists maps to a registered permission function.

```python
from general_manager.manager import GeneralManager
from general_manager.permission.manager_based_permission import AdditiveManagerPermission


class Project(GeneralManager):
    creator_id: int
    status: str
    sensitive_note: str

    class Permission(AdditiveManagerPermission):
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

When this setting is absent, `AdditiveManagerPermission` falls back to the values
shown above.

## 2. Attach filters for queryset access

Generated GraphQL list and search queries obtain a read-permission plan from
the manager's permission class. The plan may allow or deny the whole query,
apply row filters, or require a final per-instance check. A filter that completely
expresses the rule can avoid that final check. Field-level permissions still
apply when GraphQL resolves the selected fields. See
[permission enforcement](../concepts/graphql/security.md#permission-enforcement)
for the exact contract. Each registered permission function may provide a filter
companion; plain Python `Manager.filter()` does not apply it automatically.

```python
from general_manager.permission.permission_checks import register_permission


@register_permission(
    "belongsToCustomer",
    permission_filter=lambda user, config: {
        "filter": {f"{config[0]}__owner_id": user.id}
    }
    if config
    else None,
)
def can_access_customer(instance, user, config):
    customer_field = config[0]
    return getattr(instance, customer_field).owner_id == user.id
```

This fragment assumes a `customer` relation whose manager exposes `owner_id`.
Add `"isAuthenticated&belongsToCustomer:customer"` to `__read__`. Test the
registered predicate, its filter, and generated list/search responses with
allowed and denied users: a returned filter must describe the same allowed rows
as the predicate. Return `None` instead if that equivalence cannot be guaranteed.

`permission_filter` may return only a `filter` mapping, only an `exclude`
mapping, both keys, or `None`. Return `None` when a rule depends on runtime
state that cannot be expressed as queryset kwargs. For a rule whose outcome is
the same for every row, return a static decision instead:

```python
from general_manager.permission import PermissionFilterDecision


def is_project_reviewer_filter(user, _config):
    return (
        PermissionFilterDecision.ALLOW_ALL
        if getattr(user, "is_staff", False)
        else PermissionFilterDecision.DENY_ALL
    )


@register_permission(
    "isProjectReviewer", permission_filter=is_project_reviewer_filter
)
def is_project_reviewer(_instance, user, _config):
    return bool(getattr(user, "is_staff", False))
```

Use `ALLOW_ALL` only when the user/configuration grants every possible row and
`DENY_ALL` only when it grants none. GraphQL list and search resolvers use these
decisions to avoid unnecessary prefilters and per-row checks; a deny-all search
manager is skipped. GeneralManager still stores a callable filter in
`permission_functions` for permissions registered without a custom filter; that
default callable returns `None`.

Permission strings are split on `&` and `:` without escaping. Keep registered
names colon-free for ordinary rules, and expect empty config segments to be
passed through unchanged. An empty string attempts to resolve an empty
permission name and raises `PermissionNotFoundError`. If a custom permission
method or filter raises, that exception propagates; catch and convert domain
errors inside the callable when a denial should be represented as `False` or
`None`.

For an AND expression such as
`"isAuthenticated&belongsToCustomer:customer"`, GeneralManager evaluates
fragments from left to right and stops at the first `False` result. Unknown
permission names raise `PermissionNotFoundError` only when the evaluator reaches
that fragment, so `"customDeny&missingPermission"` returns `False` if
`customDeny` denies access before the missing name is inspected.

On production paths, these list/search checks also emit aggregate structured logs through the standard `get_logger(..., context=...)` pattern. That gives you candidate/authorized/denied counts plus reason labels such as unfilterable read rules or delegated `__based_on__` fallbacks without logging one event per row.

## 3. Chain permissions with `__based_on__`

Complex domains often reuse another manager's permission logic. Setting `__based_on__` delegates to that nested manager. The implementation documented under the manager-based permission classes validates the attribute, forwards CRUD checks, and merges queryset filters.

```python
class ProjectDocument(GeneralManager):
    project: Project
    file_path: str

    class Permission(AdditiveManagerPermission):
        __based_on__ = "project"
        __create__ = ["isAuthenticated"]
        file_path = {"read": ["isAuthenticated"], "update": ["isSelf"]}
```

When a user fails a delegated check, the action is denied immediately. Filters returned from `Project.Permission.get_permission_filter()` are namespaced as `{"filter": {"project__...": ...}}`, keeping queryset logic consistent.

Mutation payloads may provide either the related manager or the normalized
foreign-key value. This makes the same permission class work for direct Python
calls and generated GraphQL mutations:

```python
document = ProjectDocument.create(
    project=project,
    creator_id=user.id,
)
document = ProjectDocument.create(
    project_id=str(project.id),
    creator_id=user.id,
)

# The proposed project is checked before the update is written.
document = document.update(
    project_id=str(other_project.id),
    creator_id=user.id,
)
```

GraphQL normalizes `project` to `project_id` before the permission check. If a
Python payload contains both aliases, preserve the order that the ORM will
persist: the last of `project` or `project_id` wins. A `to_field` relation uses
that field's value to resolve the actual related manager, rather than assuming
the value is its primary key. Invalid identifiers do not fall back to default
permissions: input conversion or the related-object lookup may raise before
the delegated permission is evaluated.

If you need a field-specific rule to replace the class-level CRUD rule instead of adding an extra gate, switch to `OverrideManagerPermission`. `__based_on__` still stays an outer gate in that mode.

If `project` is `None` at runtime, implicit CRUD rules on the current
permission fall back to `GENERAL_MANAGER["DEFAULT_PERMISSIONS"]` (or to
`public` for reads and `isAuthenticated` for writes when that setting is not
configured). Explicitly declared CRUD lists still win.

For an instance-level check, a missing configured relation raises
`InvalidBasedOnConfigurationError`; a relation that resolves to something
other than a `GeneralManager` raises `InvalidBasedOnTypeError`. A configured
relation whose value is `None` instead uses the defaults described above.

## 4. Validate at runtime

`BasePermission` exposes helpers used by managers and mutations to enforce permissions. Call them directly in tests or custom workflows:

```python
payload = {"status": "active"}
Project.Permission.check_create_permission(payload, Project, request_user=user)
```

Use the manager's concrete permission class. `BasePermission` defines the
abstract contract and cannot evaluate an application's policy by itself. The
helper checks permission only; it does not validate or persist the payload.
Normal `Project.create(..., creator_id=user.pk)` calls this check for you.

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
3. For list endpoints, test the returned filter specifications and the actual
   generated query response. `get_permission_filter()` returns constraint
   mappings, not a queryset. Include rules that need a per-instance check.
4. If audit logging is enabled during tests, capture emitted events using a stub logger to assert on the recorded metadata.

With these steps, your permission classes stay in sync with business requirements while remaining transparent to reviewers and observability tooling.
