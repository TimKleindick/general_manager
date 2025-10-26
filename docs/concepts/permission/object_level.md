# Object-Level Delegation

Complex domains often need permissions that depend on related objects. `ManagerBasedPermission` supports this through the `__based_on__` attribute, which delegates evaluation to the permission class of another manager.

```python
class ProjectMember(GeneralManager):
    project: Project
    user: User

    class Permission(ManagerBasedPermission):
        __based_on__ = "project"
        __create__ = ["isProjectManager"]
        __update__ = ["isProjectManager"]
        __delete__ = ["isProjectManager"]
```

When the permission class encounters `__based_on__`, it loads the referenced manager (either an attribute or a nested manager type) and invokes its permission checks. If the delegation returns `False`, the original action is denied.

## Permission data manager

Behind the scenes, `PermissionDataManager` builds a comparison context containing the payload, the current manager state, and the requesting user. It exposes helper methods for change tracking (for example, `for_update`) so that permission expressions can compare old and new values.

## Filters and delegation

`get_permission_filter()` merges filters returned by the delegated permission. This ensures bucket queries respect constraints from both the primary manager and the related manager. When delegation fails to load the referenced manager, the permission defaults to no access.

## Tips

- Validate that the delegated attribute resolves to a manager instance; otherwise permission evaluation raises `TypeError`.
- Use attribute-level overrides (`field = {"update": ["isOwner"]}`) alongside `__based_on__` to fine-tune write access on sensitive fields.
- In GraphQL mutations, always run permission checks before performing side effects to avoid inconsistent state when a delegated permission rejects the action.
