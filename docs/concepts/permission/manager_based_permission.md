# Manager-Based Permissions

`ManagerBasedPermission` (`general_manager.permission.manager_based_permission.ManagerBasedPermission`) is the default implementation used by most managers. It interprets class-level lists that describe who may perform CRUD actions.

## Configuration

```python
from general_manager.permission.manager_based_permission import ManagerBasedPermission

class Project(GeneralManager):
    ...

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["isAdmin"]
        __update__ = ["isAdmin", "isProjectManager"]
        __delete__ = ["isAdmin"]
```

Each list contains permission expressions evaluated by `validate_permission_string`. Expressions can reference:

- Built-in keywords such as `public`, `isAuthenticated`, or `isAdmin`.
- Custom methods on the manager (e.g., `isProjectManager`).

If any expression evaluates to `True`, the action is allowed.

## Attribute-level rules

Define nested dictionaries to restrict specific attributes:

```python
class Permission(ManagerBasedPermission):
    total_capex = {
        "update": ["isFinanceTeam"],
    }
```

Bucket operations respect attribute-level restrictions when populating results.

## Permission filters

`ManagerBasedPermission.get_permission_filter()` converts expressions into Django queryset filters. Buckets apply these filters automatically so unauthorised records do not show up in listings.

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

## Extending behaviour

Subclass `ManagerBasedPermission` when you need custom evaluation logic. Override `validate_permission_string` to support new keywords (e.g., location-based permissions) or integrate with external policy engines.
