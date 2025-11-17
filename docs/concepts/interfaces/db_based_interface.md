# Database Interfaces

`DatabaseInterface` connects a manager to a Django model. It generates CRUD behaviour and keeps audit metadata in sync with your application. When you already have a Django model and only need the GeneralManager layer, use [`ExistingModelInterface`](existing_model_interface.md) instead of generating a new table.

## Defining fields

Declare Django model fields inside the interface. GeneralManager converts type hints on the manager into attribute accessors that resolve to these fields.

```python
from django.db.models import CharField, ForeignKey

from general_manager.interface import DatabaseInterface
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

Managers expose `create`, `update`, and `delete` methods that call the interface. Each method accepts `creator_id` and `history_comment` arguments for audit trails. The legacy `deactivate` name remains available as a deprecated alias.

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

### Soft deletes

New database-backed managers perform hard deletes by default. Add `use_soft_delete = True` to the interface's `Meta` class to keep the historical `is_active` flag and route `delete()` calls through a soft delete. When enabled GeneralManager automatically injects filtered managers (`objects` returns active rows, `all_objects` includes inactive ones), honours explicit `filter(is_active=…)` lookups, and preserves the existing history comments (`"… (deactivated)"`). Pass `include_inactive=True` to `filter()`/`exclude()` when you need the full dataset without touching the model's managers directly.

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

Use `general_manager.interface.ReadOnlyInterface` to mirror static datasets in the database:

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

On startup the interface synchronises `_data` with the table, creating, updating, or soft-deleting entries as needed (read-only interfaces force `Meta.use_soft_delete = True`). Managers expose read operations only; write attempts raise exceptions.

## Validation hooks

Override `clean()` or add rules in `Meta.rules` to validate data before it reaches the database. Use `constraints` for uniqueness and relational requirements—GeneralManager surfaces validation errors through GraphQL responses automatically.
