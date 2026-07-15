# GeneralManager

[![PyPI](https://img.shields.io/pypi/v/GeneralManager.svg)](https://pypi.org/project/GeneralManager/)
[![Python](https://img.shields.io/pypi/pyversions/GeneralManager.svg)](https://pypi.org/project/GeneralManager/)
[![Build](https://github.com/TimKleindick/general_manager/actions/workflows/quality.yml/badge.svg?branch=main)](https://github.com/TimKleindick/general_manager/actions/workflows/quality.yml)
[![Coverage](https://img.shields.io/codecov/c/github/TimKleindick/general_manager)](https://app.codecov.io/gh/TimKleindick/general_manager)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/TimKleindick/general_manager/blob/main/LICENSE)

GeneralManager is a typed, declarative framework for building data-rich Django
applications. Define domain objects once, then use the same model through the
Django ORM, generated GraphQL, permission policies, validation, calculations,
search, and workflows.

GeneralManager is pre-1.0. The `main` branch and latest PyPI release can differ;
use the [release history](https://github.com/TimKleindick/general_manager/releases)
to distinguish published behavior from current development.

## What GeneralManager provides

### Domain models and interfaces

- Generate Django-backed models from typed manager definitions.
- Wrap [existing Django models](https://timkleindick.github.io/general_manager/concepts/interfaces/existing_model_interface/)
  or expose [calculated](https://timkleindick.github.io/general_manager/concepts/interfaces/computed_data_interfaces/),
  [request-backed](https://timkleindick.github.io/general_manager/concepts/interfaces/request_interface/),
  and remote data through the same manager API.

Start with the [architecture overview](https://timkleindick.github.io/general_manager/concepts/architecture/)
and [interface concepts](https://timkleindick.github.io/general_manager/concepts/interfaces/).

### APIs, permissions, and validation

- Generate [GraphQL](https://timkleindick.github.io/general_manager/concepts/graphql/)
  queries, mutations, subscriptions, filters, and pagination.
- Apply [manager-, operation-, object-, and field-level permission policies](https://timkleindick.github.io/general_manager/concepts/permission/).
- Keep business [validation](https://timkleindick.github.io/general_manager/concepts/rules_validation/)
  close to the domain with declarative rules.

### Data and operations

- Work with [unit- and currency-aware measurements](https://timkleindick.github.io/general_manager/concepts/measurement/)
  and [dataframe conversion helpers](https://timkleindick.github.io/general_manager/api/dataframes/).
- Coordinate derived values with [dependency-aware caching](https://timkleindick.github.io/general_manager/concepts/caching/),
  [search](https://timkleindick.github.io/general_manager/concepts/search/),
  [workflows](https://timkleindick.github.io/general_manager/concepts/workflow/),
  [audit logging](https://timkleindick.github.io/general_manager/concepts/observability/audit_logging/),
  and [metrics](https://timkleindick.github.io/general_manager/concepts/observability/graphql_metrics/).

### Development and optional integrations

- Seed realistic data with [generated factories and manager-landscape helpers](https://timkleindick.github.io/general_manager/concepts/factories/).
- Opt into [secure GraphQL file uploads](https://timkleindick.github.io/general_manager/concepts/graphql/file_uploads/)
  and [provider-based LLM chat](https://timkleindick.github.io/general_manager/concepts/chat_prompting/)
  when those subsystems fit the application.

## Compatibility

- Python 3.12 or newer
- Django 5.2.15 or newer
- CI exercises Python 3.12, 3.13, and 3.14 on SQLite

GeneralManager builds on Django's database layer, but this project only claims
backend support covered by its tests or maintained examples.

## Five-minute setup

Start in an empty directory. On macOS or Linux:

```bash
mkdir general-manager-demo
cd general-manager-demo
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install GeneralManager
django-admin startproject gm_demo .
python manage.py startapp projects
```

Append the following to `gm_demo/settings.py`:

```python
INSTALLED_APPS += [
    "general_manager",
    "projects.apps.ProjectsConfig",
]

AUTOCREATE_GRAPHQL = True
GRAPHQL_URL = "graphql/"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
```

Create `projects/managers.py`:

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

GeneralManager discovers `managers.py` modules in installed Django applications
during startup, so no manual import in `AppConfig.ready()` is needed. Create the
generated model's migration, initialize the database, and seed one demo record:

```bash
python manage.py makemigrations projects
python manage.py migrate
python manage.py shell -c 'from projects.managers import Project; Project.Factory.create(name="Apollo")'
python manage.py runserver
```

`Project.Factory.create(...)` is intended here for demo seeding. Application
writes should run through an authenticated permission context.

In another terminal, retrieve the record through generated GraphQL. This GET
request is copyable without a CSRF token:

```bash
curl --get 'http://127.0.0.1:8000/graphql/' \
  --data-urlencode 'query=query { projectList { items { name } } }'
```

Expected response:

```json
{"data":{"projectList":{"items":[{"name":"Apollo"}]}}}
```

You now have a typed manager, generated Django model, migration, persisted
record, permission policy, and generated GraphQL query. Continue with the
[annotated quickstart](https://timkleindick.github.io/general_manager/quickstart/)
for Windows activation, framework behavior, and troubleshooting.

## Documentation

- [Documentation home](https://timkleindick.github.io/general_manager/)
- [Quickstart](https://timkleindick.github.io/general_manager/quickstart/)
- [Installation and optional extras](https://timkleindick.github.io/general_manager/installation/)
- [Concept guides](https://timkleindick.github.io/general_manager/concepts/)
- [API reference](https://timkleindick.github.io/general_manager/api/core/)
- [Examples and recipes](https://timkleindick.github.io/general_manager/examples/)

## Project links

- [Releases](https://github.com/TimKleindick/general_manager/releases)
- [Issues](https://github.com/TimKleindick/general_manager/issues)
- [Contributing](https://github.com/TimKleindick/general_manager/blob/main/CONTRIBUTING.md)
- [MIT License](https://github.com/TimKleindick/general_manager/blob/main/LICENSE)
