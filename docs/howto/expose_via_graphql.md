# Expose Managers via GraphQL

Managers with an `Interface` are registered during GeneralManager startup and receive generated query, mutation, and subscription fields based on their interface capabilities.

## Expose authorization hints

Use GraphQL permission capabilities when frontend code needs business-oriented authorization hints, such as whether the current user can rename a project. These fields are advisory only; backend permissions still enforce all reads and writes.

```python
from general_manager import GeneralManager
from general_manager.permission import AdditiveManagerPermission, object_capability


def can_rename_project(project, user):
    return project.status == "draft" and user.is_authenticated


class Project(GeneralManager):
    class Permission(AdditiveManagerPermission):
        graphql_capabilities = (
            object_capability("canRename", can_rename_project),
        )
```

Query the generated capability object:

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

For list-heavy checks, pass `batch_evaluator=` to `object_capability(...)`. The list resolver warms capability values for the returned page only when `capabilities` is selected.
