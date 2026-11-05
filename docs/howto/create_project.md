# Create a Project Scaffold

Start with the [quickstart](../quickstart.md) to create a Django project, register
the `projects` application, define its `Project` manager, run migrations, and
query an `Apollo` record through GraphQL. That guide is the complete setup path
from an empty directory.

This tutorial continues that project with an authenticated Python write. It
uses the quickstart's default Django user model and `Project` permission policy:
public reads and authenticated create, update, and delete operations.

## Check the project layout

After the quickstart, the relevant files are:

```text
general-manager-demo/
├── manage.py
├── gm_demo/
│   └── settings.py
└── projects/
    ├── apps.py
    ├── managers.py
    └── migrations/
```

Keep manager declarations in `projects/managers.py`. GeneralManager imports this
module from the installed application during startup; no extra import in
`AppConfig.ready()` is needed. For an existing Django project, follow
[Installation](../installation.md) and use your application's module names.

## Create an application user

From the directory containing `manage.py`, with the virtual environment active,
open a shell:

```bash
python manage.py shell
```

Create an ordinary user for the demo. Use a fresh username if you repeat this
step:

```python
from django.contrib.auth import get_user_model

User = get_user_model()
author = User.objects.create_user(username="gm-author")
assert not author.is_superuser
```

This user has no usable password. The shell example passes its ID explicitly;
it does not log a browser or GraphQL client in. Use your application's normal
Django authentication flow for HTTP requests.

## Create and update a project

In the same shell, supply the actor through `creator_id`:

```python
from projects.managers import Project

project = Project.create(creator_id=author.pk, name="First customer project")
project.update(creator_id=author.pk, name="Customer launch")
print(Project.get(id=project.id).name)
```

Expected output:

```text
Customer launch
```

`create()` returns a new manager. `update()` changes the stored record and
refreshes the same manager instance. `creator_id` identifies the user for write
permission checks and history; in a Django view, derive it from
`request.user.pk`, not a client-supplied actor ID.

Omitting `creator_id` supplies an anonymous permission context. With this
project's policy, the write is denied. The quickstart uses
`Project.Factory.create()` to seed demo data; application writes should use
the manager methods with the appropriate actor. Superusers bypass permission
checks, which is why this example uses an ordinary user.

## Continue with permissions

Plain Python reads such as `Project.get(...)`, `Project.all()`, and
`project.name` do not infer a request user or apply read permissions. Generated
GraphQL queries enforce the configured read policy using their request context.
Custom endpoints must explicitly authorize the records and fields they expose;
see [where permissions run](../concepts/permission/index.md#where-permissions-run).

Continue with [Define Permission Rules](add_permission_rule.md) to restrict
writes and verify a denial, or add
[measurement fields](add_model_with_measurement.md).
