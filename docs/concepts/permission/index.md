# Permission System Overview

GeneralManager enforces attribute-based access control (ABAC) through permission classes attached to managers. Permissions evaluate context (request user, manager attributes, related managers) before allowing read or write operations.

Each manager defines a nested `Permission` class derived from `general_manager.permission.basePermission.BasePermission` or a subclass such as `ManagerBasedPermission`. The permission class decides:

- Whether the user can perform CRUD actions on the manager.
- Which specific attributes are visible or editable.
- How permission filters are applied to buckets so that only authorised records are returned.

The following pages dive into detailed patterns:

- [Manager-based permissions](manager_based_permission.md)
- [Object-level delegation](object_level.md)
- [Practical examples](examples.md)

When you write GraphQL resolvers or REST endpoints, always go through the manager API so that permissions stay consistent across entry points.
