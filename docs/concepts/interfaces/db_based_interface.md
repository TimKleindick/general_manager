# Database Interfaces

`DatabaseInterface` connects a manager to a Django model. It generates CRUD behaviour and keeps audit metadata in sync with your application. When you already have a Django model and only need the GeneralManager layer, use [`ExistingModelInterface`](existing_model_interface.md) instead of generating a new table.

## Defining fields

Declare Django model fields inside the interface. GeneralManager converts type hints on the manager into attribute accessors that resolve to these fields.

```python
from django.db.models import CharField, ForeignKey

from general_manager.interface.database_interface import DatabaseInterface
from general_manager.manager import GeneralManager

class Book(GeneralManager):
    title: str
    author_id: int

    class Interface(DatabaseInterface):
        title = CharField(max_length=50)
        author = ForeignKey("auth.User", on_delete=models.CASCADE)
```

You can use all field types, including custom fields like `MeasurementField`. Django validators, constraints, and signals run as usual.

## CRUD operations

Managers expose `create`, `update`, and `deactivate` methods that call the interface. Each method accepts `creator_id` and `history_comment` arguments for audit trails.

```python
material = Material.create(
    creator_id=request.user.id,
    history_comment="Initial import",
    name="Steel",
)

material.update(
    creator_id=request.user.id,
    description="Updated description",
)
```

## Many-to-many relationships

Pass related IDs using the `<field>_id_list` convention. The interface applies the relation after saving the main record to ensure audit entries are consistent.

```python
Project.create(
    creator_id=user.id,
    name="Alpha",
    stakeholder_id_list=[1, 2, 3],
)
```

## Read-only data

Use `general_manager.interface.read_only_interface.ReadOnlyInterface` to mirror static datasets in the database:

```python
class Country(GeneralManager):
    _data = [
        {"code": "US", "name": "United States"},
        {"code": "DE", "name": "Germany"},
    ]

    class Interface(ReadOnlyInterface):
        code = CharField(max_length=2, unique=True)
        name = CharField(max_length=50)
```

On startup the interface synchronises `_data` with the table, creating, updating, or deactivating entries as needed. Managers expose read operations only; write attempts raise exceptions.

## Validation hooks

Override `clean()` or add rules in `Meta.rules` to validate data before it reaches the database. Use `constraints` for uniqueness and relational requirements—GeneralManager surfaces validation errors through GraphQL responses automatically.
