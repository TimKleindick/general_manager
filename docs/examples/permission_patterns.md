# Permission Patterns

These are policy fragments for managers your application already declares.
Keep their interface fields and place each nested `Permission` class inside
its corresponding manager. Complete the
[permission tutorial](../howto/add_permission_rule.md) first.

## Self-service edits

For a Profile whose `user` foreign key identifies its owner, compare
`user_id` with the requesting user's primary key. Register the predicate once
in a module imported during manager discovery:

```python
from general_manager.permission import (
    AdditiveManagerPermission,
    OverrideManagerPermission,
    register_permission,
)


@register_permission("isProfileOwner")
def is_profile_owner(instance, user, config):
    return user.is_authenticated and instance.user_id == user.pk


# Inside the application's Profile manager:
class Permission(AdditiveManagerPermission):
    __read__ = ["isAuthenticated"]
    __update__ = ["isAdmin", "isProfileOwner"]
```

The two update entries are alternatives: staff administrators or the profile's
owner may update it. The built-in `isSelf` checks the `creator` attribute,
so it is not the ownership rule for a separate `user` relation. This example
assumes that relation is not itself user-editable; protecting changes to the
owner field is a separate application policy.

## Hierarchical approval

For a WorkPackage with a `project` manager relation, delegate to Project and
add a local editor-group requirement:

```python
# Inside the application's WorkPackage manager:
class Permission(AdditiveManagerPermission):
    __based_on__ = "project"
    __update__ = ["inGroup:work-package-editors"]
```

The delegated Project check must pass as well as the local group requirement.
Create the application group and assign the intended users. See
[object-level delegation](../concepts/permission/object_level.md) for null
relations and read planning.

## Attribute visibility

For a Contract with a `total_value` field, use the registered group check to
restrict that field to the application's finance group:

```python
# Inside the application's Contract manager:
class Permission(OverrideManagerPermission):
    total_value = {
        "read": ["inGroup:finance"],
    }
```

Generated GraphQL returns `None` for a denied nullable field. GraphQL's normal
null propagation applies if the schema declares it non-null. This does not
authorize direct Python attribute access; custom endpoints must enforce their
own row and field policy.
