# Define Permission Rules

This guide adds attribute-based permissions and validation rules to a manager.

## Step 1: Add permission class

```python
from general_manager.interface.database_interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.permission.manager_based_permission import ManagerBasedPermission

class Project(GeneralManager):
    ...

    class Permission(ManagerBasedPermission):
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

When a mutation violates a rule, the response includes `success: false` and a structured error list. Display the messages in the frontend or log them for audit purposes.
