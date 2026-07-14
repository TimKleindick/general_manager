# Define Permission Rules

This guide adds attribute-based permissions and validation rules to a manager.

## Step 1: Add permission class

```python
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.permission.manager_based_permission import AdditiveManagerPermission

class Project(GeneralManager):
    ...

    class Permission(AdditiveManagerPermission):
        __read__ = ["public"]
        __create__ = ["is_staff", "group:project-admin"]
        __update__ = ["isProjectManager"]
        __delete__ = ["isProjectManager"]
```

Implement `Project.isProjectManager()` to compare `self.owner` with `request_user`.

## Step 2: Add rules

```python
from datetime import date

import pytest
from general_manager.rule import Rule

class Project(GeneralManager):
    ...

    class Interface(DatabaseInterface):
        ...

        class Meta:
            rules = [
                Rule["Project"](
                    lambda project: project.end_date is None
                    or project.start_date <= project.end_date
                ),
            ]
```

## Step 3: Test in Django shell

```python
project = Project.create(
    creator_id=your_user.id,
    name="New project",
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
)

with pytest.raises(PermissionError):
    project.update(
        creator_id=another_user.id,
        description="Not allowed",
    )
```

## Step 4: Expose error messages in GraphQL

When a mutation violates a rule, the failure is raised into the GraphQL `errors`
list and GraphQL execution sets that mutation's data field to `null`.
`PermissionError` exposes only the fixed message `Permission denied.` with code
`PERMISSION_DENIED`. For validation failures, a structured Django
`ValidationError` exposes `fieldErrors` and `nonFieldErrors`.

Display only deliberately safe public or validation messages. Use the opaque
`errorId` from an internal error for support correlation, and use server audit
logs—not the client response—for internal details.
