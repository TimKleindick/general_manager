# Permission Patterns

## Self-service edits

Allow users to edit their own profile while administrators can edit any profile.

```python
from general_manager.manager import GeneralManager
from general_manager.measurement import Measurement
from general_manager.permission.manager_based_permission import ManagerBasedPermission

class Profile(GeneralManager):
    user: User

    class Permission(ManagerBasedPermission):
        __read__ = ["isAuthenticated"]
        __update__ = ["isAdmin", "isSelf"]
```

## Hierarchical approval

Chain permissions using `__based_on__` for nested workflows.

```python
class WorkPackage(GeneralManager):
    project: Project

    class Permission(ManagerBasedPermission):
        __based_on__ = "project"
        __update__ = ["isProjectManager", "isWorkPackageOwner"]
```

## Attribute visibility

Hide sensitive attributes from unauthorised users by returning `None`.

```python
class Contract(GeneralManager):
    total_value: Measurement

    class Permission(ManagerBasedPermission):
        total_value = {
            "read": ["isFinanceTeam"],
        }
```

Unauthorised users still receive the object but the restricted field resolves to `None` in GraphQL.
