# Object-Level Delegation

Complex domains often need permissions that depend on related objects. Both `AdditiveManagerPermission` and `OverrideManagerPermission` support this through the `__based_on__` attribute, which delegates evaluation to the permission class of another manager.

This fragment belongs inside an existing `ProjectMember` manager whose
`project` relation resolves to a `Project` manager. Keep its interface and
fields. The local rule requires membership in the application-defined
`project-editors` Django group, in addition to the delegated Project policy:

```python
from general_manager.permission import AdditiveManagerPermission

# Inside ProjectMember:
class Permission(AdditiveManagerPermission):
    __based_on__ = "project"
    __create__ = ["inGroup:project-editors"]
    __update__ = ["inGroup:project-editors"]
    __delete__ = ["inGroup:project-editors"]
```

When the permission class encounters `__based_on__`, it loads the referenced manager (either an attribute or a nested manager type) and invokes its permission checks. If the delegation returns `False`, the original action is denied before local additive or override rules are considered.

## Permission data manager

Behind the scenes, `PermissionDataManager` gives permission checks one attribute-access surface for either payload dictionaries or manager instances. For update checks, `for_update` overlays the new payload on top of `dict(old_instance)` and exposes the merged final state to permission expressions; keep the original manager instance separately when a custom workflow needs an explicit before/after comparison. The requesting user lives on the permission class itself, not on the data manager.

## Filters and delegation

`get_permission_filter()` merges filters returned by the delegated permission. Generated GraphQL uses that permission plan when authorizing list queries.
A plain Python bucket does not apply it automatically; a custom endpoint still
owns its [read-authorization boundary](index.md#where-permissions-run). When the delegated attribute is `None`, implicit CRUD rules fall back to `GENERAL_MANAGER["DEFAULT_PERMISSIONS"]`; if that setting is not configured, the fallback is `["public"]` for reads and `["isAuthenticated"]` for create, update, and delete.

## Tips

- Validate that the delegated attribute resolves to a manager instance; otherwise permission evaluation raises `TypeError`.
- Use attribute-level overrides (`field = {"update": ["inGroup:project-editors"]}`) alongside `__based_on__` to fine-tune write access on sensitive fields.
- In GraphQL mutations, always run permission checks before performing side effects to avoid inconsistent state when a delegated permission rejects the action.
- Superusers bypass all permission checks and filters, so delegation should focus on regular users while admin tooling continues to function.
