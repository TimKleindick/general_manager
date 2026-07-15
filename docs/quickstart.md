# Quickstart

## What you will build

In this guide you will start from an empty directory, create a new Django
project, define one typed `Project` manager, persist an `Apollo` record, and
retrieve it through GeneralManager's generated GraphQL schema.

The walkthrough uses SQLite, Django's default for a new project. It should take
about five minutes once Python 3.12 or newer is available.

## 1. Create an isolated project

Create and enter an empty working directory:

```bash
mkdir general-manager-demo
cd general-manager-demo
python -m venv .venv
```

Activate the environment on macOS or Linux:

```bash
source .venv/bin/activate
```

On PowerShell instead, run:

```powershell
.venv\Scripts\Activate.ps1
```

Install the package, create the Django project in the current directory, and
add a `projects` application:

```bash
python -m pip install --upgrade pip
python -m pip install GeneralManager
django-admin startproject gm_demo .
python manage.py startapp projects
```

For production requirements and optional integrations, see
[Installation](installation.md).

## 2. Configure Django

Append this block to `gm_demo/settings.py`:

```python
INSTALLED_APPS += [
    "general_manager",
    "projects.apps.ProjectsConfig",
]

AUTOCREATE_GRAPHQL = True
GRAPHQL_URL = "graphql/"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
```

Adding the two applications lets GeneralManager import `managers.py` from the
installed `projects` application during Django startup. `AUTOCREATE_GRAPHQL`
builds the schema and appends the configured `GRAPHQL_URL`; you do not need to
edit `urls.py` for this quickstart.

## 3. Define a manager

Create `projects/managers.py` with this content:

```python
from typing import ClassVar

from django.db.models import CharField

from general_manager import GeneralManager
from general_manager.interface import DatabaseInterface
from general_manager.permission import AdditiveManagerPermission


class Project(GeneralManager):
    name: str

    class Interface(DatabaseInterface):
        name = CharField(max_length=100)

    class Permission(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        __create__: ClassVar[list[str]] = ["isAuthenticated"]
        __update__: ClassVar[list[str]] = ["isAuthenticated"]
        __delete__: ClassVar[list[str]] = ["isAuthenticated"]
```

`DatabaseInterface` generates and registers the backing Django model. The
permission policy makes reads public only for this demo; create, update, and
delete operations require an authenticated context. GeneralManager discovers
this module automatically, so do not add a redundant import to
`AppConfig.ready()`.

## 4. Create the database

Capture the generated model in a migration and initialize the database:

```bash
python manage.py makemigrations projects
python manage.py migrate
```

Naming `projects` in `makemigrations` makes the intended generated-model
migration explicit.

## 5. Seed one record

Create one project through the generated factory:

```bash
python manage.py shell -c 'from projects.managers import Project; Project.Factory.create(name="Apollo")'
```

`Project.Factory.create(name="Apollo")` is a convenient demo-seeding path. In
application code, perform writes through the normal authenticated permission
context.

Start Django:

```bash
python manage.py runserver
```

## 6. Query generated GraphQL

In another terminal, retrieve the persisted record:

```bash
curl --get 'http://127.0.0.1:8000/graphql/' \
  --data-urlencode 'query=query { projectList { items { name } } }'
```

The GraphQL operation is:

```graphql
query { projectList { items { name } } }
```

Expected response:

```json
{"data":{"projectList":{"items":[{"name":"Apollo"}]}}}
```

List queries return a pagination object, which is why `projectList` contains an
`items` field. The example uses a GET request so it is copyable without a CSRF
token. GraphQL writes still require the normal authentication and CSRF handling
for your application.

## Troubleshooting

### `No installed app with label 'projects'`

Add `projects.apps.ProjectsConfig` to `INSTALLED_APPS`, then rerun the migration
commands.

### `No changes detected`

Keep the manager in `projects/managers.py` and confirm that `general_manager` is
installed and present in `INSTALLED_APPS`.

### `Cannot query field 'projectList'`

Set `AUTOCREATE_GRAPHQL = True`, restart Django, and confirm that
`projects/managers.py` imports without errors.

### Permission or empty-result surprises

Keep public read access only for this demo. Use authenticated policies for
application writes and restart the development server after permission changes.

## Next steps

- Understand [managers, interfaces, and buckets](concepts/architecture.md).
- Build an application policy with the [permission walkthrough](howto/permission_walkthrough.md).
- Add [measurements and currency-aware fields](concepts/measurement/index.md).
- Explore [generated GraphQL](concepts/graphql/index.md), including filters,
  pagination, mutations, and security.
- Configure [search](howto/search.md).
- Model operations with [workflows](concepts/workflow.md).
- Add optional [GraphQL file uploads](howto/graphql_file_uploads.md).
- Configure optional [provider-based chat](concepts/chat_prompting.md).
