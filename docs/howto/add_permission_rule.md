# Define Permission Rules

This tutorial continues the [quickstart](../quickstart.md) project. You will let
members of a `project-editors` group create and rename projects, require
authenticated reads through GraphQL, and verify that another user cannot rename
a project. It uses Django's default user model and the existing `Project.name`
field.

Permissions decide **who** may perform an operation. Validation rules decide
**which data** is valid; use the separate
[validation-rule guide](write_validation_rules.md) for `Meta.rules`.

## Step 1: Register a permission and attach the policy

Replace `projects/managers.py` with:

```python
from typing import ClassVar

from django.db.models import CharField

from general_manager import GeneralManager
from general_manager.interface import DatabaseInterface
from general_manager.permission import AdditiveManagerPermission, register_permission


@register_permission("isProjectEditor")
def is_project_editor(_instance, user, _config):
    return bool(
        user.is_authenticated
        and user.groups.filter(name="project-editors").exists()
    )


class Project(GeneralManager):
    name: str

    class Interface(DatabaseInterface):
        name = CharField(max_length=100)

    class Permission(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["isAuthenticated"]
        __create__: ClassVar[list[str]] = ["isAdmin", "inGroup:project-editors"]
        __update__: ClassVar[list[str]] = ["isProjectEditor"]
        __delete__: ClassVar[list[str]] = ["isAdmin"]
```

The create list contains alternatives: a staff user (`isAdmin`) **or** a member
of `project-editors` may create a project. Use `&` inside one expression when
both checks must pass, such as `"isAuthenticated&inGroup:project-editors"`.

The custom function demonstrates registration: `isProjectEditor` becomes a
keyword only because `@register_permission` registers it. Defining a method on
`Project` with that name would not register a permission. For this simple group
check, `"isAuthenticated&inGroup:project-editors"` is also a suitable built-in
expression; custom functions are useful when the application needs additional
policy logic.

GeneralManager imports `projects/managers.py` at startup, registering the
function before the policy is used. Restart the server and open a fresh shell
after changing the file. Do not execute the declaration repeatedly in the same
shell: registering the same permission name twice raises `ValueError`. The
database fields are unchanged, so this policy edit needs no schema migration.

## Step 2: Create two ordinary users

Run `python manage.py shell`, then create the group and two users. Choose fresh
usernames if you repeat the example:

```python
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

User = get_user_model()
editor = User.objects.create_user(username="gm-editor")
reader = User.objects.create_user(username="gm-reader")
editors, _ = Group.objects.get_or_create(name="project-editors")
editor.groups.add(editors)
assert not editor.is_superuser and not reader.is_superuser
```

These users have no usable passwords; the following Python calls provide their
IDs directly. Superusers bypass the checks, so they would not demonstrate this
policy's allow/deny behavior.

## Step 3: Verify an allowed write and a denied write

In the same shell:

```python
from projects.managers import Project

project = Project.create(creator_id=editor.pk, name="Editor project")
project.update(creator_id=editor.pk, name="Renamed by editor")

try:
    project.update(creator_id=reader.pk, name="Not allowed")
except PermissionError:
    print("Update denied")
else:
    raise AssertionError("The reader must not be allowed to rename a project")

print(Project.get(id=project.id).name)
```

Expected output:

```text
Update denied
Renamed by editor
```

Manager write methods check the supplied actor before delegating to the
interface. In a Django view, take that actor from `request.user.pk`. Generated
GraphQL mutations obtain it from the authenticated request context.

## Step 4: Understand read checks

Inspect the policy explicitly in the same shell:

```python
from django.contrib.auth.models import AnonymousUser

assert Project.Permission(project, reader).check_permission("read", "name")
assert not Project.Permission(project, AnonymousUser()).check_permission("read", "name")
```

These assertions evaluate permission rules; they do not install a user context
on the manager. Direct Python reads such as `Project.get(...)` and
`project.name` do not automatically enforce that policy. Generated GraphQL
queries apply read checks using `info.context.user`. With the new policy, the
quickstart's anonymous query no longer exposes project records; use an
authenticated client to read them.

For custom endpoints, authorize both the returned records and their selected
fields. The [permission overview](../concepts/permission/index.md) explains the
boundary, and the [permission walkthrough](permission_walkthrough.md) covers
filters, field rules and delegation.

## Step 5: Handle errors in GraphQL

When a mutation is denied, GraphQL returns a top-level `errors` entry and sets
that mutation's data field to `null`. `PermissionError` exposes only the fixed
message `Permission denied.` with code `PERMISSION_DENIED`. A structured Django
`ValidationError` exposes `fieldErrors` and `nonFieldErrors` for invalid data.

Display deliberately public error messages. Use an internal error's opaque
`errorId` for support correlation and server audit logs for internal details.
