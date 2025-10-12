# Quickstart

This quickstart walks you through the minimum steps required to add GeneralManager to a Django project and expose a simple manager over GraphQL. It assumes Django ≥ 4.2 and Python ≥ 3.12.

## 1. Install the package

```bash
pip install GeneralManager
```

Add `general_manager` to `INSTALLED_APPS` in `settings.py` and configure the Django cache backend you use for dependency tracking.

## 2. Define a manager

Create a manager that describes the fields you want to expose. Each manager defines an inner `Interface` class that handles persistence.

```python
# apps/materials/managers.py
from django.db.models import CharField, TextField

from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.manager import GeneralManager

class Material(GeneralManager):
    name: str
    description: str | None

    class Interface(DatabaseInterface):
        name = CharField(max_length=100)
        description = TextField(null=True, blank=True)
```

## 3. Apply database migrations

Interfaces automatically derive a Django model. Include it in an application `models.py` (or import it in `apps.py`) so Django discovers the model, then run:

```bash
python manage.py makemigrations
python manage.py migrate
```

## 4. Create permission rules

Managers enforce access checks through the nested `Permission` class. Grant access based on attributes, user groups, or custom callables.

```python
from general_manager.permission.managerBasedPermission import ManagerBasedPermission

class Material(GeneralManager):
    ...

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["isAdmin"]
        __update__ = ["isAdmin"]
        __delete__ = ["isAdmin"]
```

## 5. Seed data

Use the manager API to populate the database. CRUD operations automatically record audit metadata.

```python
material = Material.create(
    creator_id=request.user.id,
    history_comment="Initial load",
    name="Steel",
    description="High grade structural steel",
)
```

## 6. Expose GraphQL schema

Add a URL route to serve the GraphQL schema by adding the following settings to your `settings.py`:

```python
# settings.py
AUTOCREATE_GRAPHQL = True
GRAPHQL_URL = "graphql/"
```

Run your server and test the following query:

```graphql
query {
  materialList {
    name
    description
  }
}
```

## Next steps

- Study the [architecture overview](concepts/architecture.md) to understand how buckets, managers, and interfaces cooperate.
- Explore the [tutorials](howto/index.md) to learn how to add permissions, measurements, and computed fields.
- When you build production workflows, review the [operations guide](ops/index.md) for deployment and monitoring considerations.
