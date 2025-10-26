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

## Extending behaviour

Subclass `ManagerBasedPermission` when you need custom evaluation logic. Override `validate_permission_string` to support new keywords (e.g., location-based permissions) or integrate with external policy engines.
