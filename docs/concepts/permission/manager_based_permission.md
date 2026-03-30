# Manager-Based Permissions

GeneralManager now exposes two explicit manager-based permission classes:

- `AdditiveManagerPermission`: attribute-specific rules add an extra gate on top of the class-level CRUD rule.
- `OverrideManagerPermission`: attribute-specific rules replace the class-level CRUD rule for that field/action.

`ManagerBasedPermission` remains available as a compatibility alias for `AdditiveManagerPermission`, but new code should use the explicit class names.

## Configuration

```python
from general_manager.permission.manager_based_permission import AdditiveManagerPermission

class Project(GeneralManager):
    ...

    class Permission(AdditiveManagerPermission):
        __read__ = ["public"]
        __create__ = ["isAdmin"]
        __update__ = ["isAdmin", "isProjectManager"]
        __delete__ = ["isAdmin"]
```

Each list contains permission expressions evaluated by `validate_permission_string`. Expressions can reference:

- Built-in keywords such as `public`, `isAuthenticated`, or `isAdmin`.
- Custom methods on the manager (e.g., `isProjectManager`).

If any expression evaluates to `True`, the action is allowed.

## Default permissions from settings

If a permission class does not define one or more CRUD lists explicitly,
`AdditiveManagerPermission` and `OverrideManagerPermission` fill them from Django settings:

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

When `GENERAL_MANAGER["DEFAULT_PERMISSIONS"]` is not configured, these same
values are used as the built-in fallback.

This affects three places:

- subclasses that omit `__read__`, `__create__`, `__update__`, or `__delete__`
- direct use of `AdditiveManagerPermission`, `OverrideManagerPermission`, or the `ManagerBasedPermission` compatibility alias
- `__based_on__` permissions when the delegated manager attribute exists but is `None`

For `__based_on__` subclasses, implicit CRUD defaults are still initialised as
empty lists at class creation time so delegation remains the primary source of
permissions. If the delegated object is `None` at runtime, the instance falls
back to the configured defaults above unless the subclass explicitly defined its
own CRUD list for that action.

## Attribute-level rules

Define nested dictionaries to restrict specific attributes:

```python
class Permission(AdditiveManagerPermission):
    total_capex = {
        "update": ["isFinanceTeam"],
    }
```

### Merge semantics

Use `AdditiveManagerPermission` when a field-specific rule should add a second requirement:

```python
class Permission(AdditiveManagerPermission):
    __update__ = ["isAdmin"]
    total_capex = {"update": ["isFinanceTeam"]}
```

For `total_capex`, the user must satisfy both `isAdmin` and `isFinanceTeam`.

Use `OverrideManagerPermission` when a field-specific rule should replace the class-level CRUD rule:

```python
class Permission(OverrideManagerPermission):
    __update__ = ["isAdmin"]
    total_capex = {"update": ["isFinanceTeam"]}
```

For `total_capex`, only `isFinanceTeam` is evaluated locally; the class-level `__update__` rule still applies to other attributes.

When `__based_on__` is set, delegated permissions always remain an outer gate in both classes.

## Permission filters

`AdditiveManagerPermission.get_permission_filter()` and `OverrideManagerPermission.get_permission_filter()` convert read expressions into Django queryset filters. Buckets use those filters as a prefilter, then run a final per-instance read check before a row contributes to list membership or counts. This keeps list and search authorization fail-closed even when a read rule cannot be represented as a queryset constraint.

The read path also plugs into the project's existing observability pattern:

- GraphQL list and search paths emit one aggregate structured log event per manager/query path, with the structured payload attached at the log call site (for example `logger.info(..., context=...)`).
- The log context records candidate rows, authorized rows, denied rows, whether a final instance gate was required, and the reason labels that triggered it.
- These events complement the existing GraphQL metrics pipeline; the permission hardening does not introduce a separate telemetry subsystem or a new public metrics API.

## Custom permission functions

Use the `register_permission` decorator to add project-specific keywords to the global permission registry:

```python
from general_manager.permission import register_permission


@register_permission("isProjectManager")
def is_project_manager(instance, user, config):
    return instance.project.managers.filter(pk=user.pk).exists()
```

Optionally supply a queryset filter when the permission can be represented as a lookup:

```python
@register_permission(
    "inDepartment",
    permission_filter=lambda user, config: {
        "filter": {"department__slug": config[0]}
    }
    if config
    else None,
)
def in_department(_instance, user, config):
    return bool(config and user.department.slug == config[0])
```

Registered permissions are immediately available to every process that imports the module, so each worker should load the module (for example in `AppConfig.ready`). Attempting to register the same name twice raises `ValueError` to prevent accidental overrides.

## Superuser bypass

`BasePermission` short-circuits evaluation for users with `is_superuser=True`. Superusers skip all CRUD checks and associated queryset filters, ensuring the registry logic never blocks administrative maintenance tasks.
