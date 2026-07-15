# Database Interfaces

`DatabaseInterface` connects a manager to a generated GeneralManager Django
model. The interface uses the writable ORM capability bundle, so query, create,
update, delete, history, validation, and observability behavior are wired during
manager class creation. When you already have a Django model and only need the
GeneralManager layer, use [`ExistingModelInterface`](existing_model_interface.md)
instead of generating a new table.

During class creation the ORM lifecycle builds the Django model, a concrete
interface subclass, and a generated `AutoFactory` subclass. Interface-level
`Meta.use_soft_delete` chooses a soft-delete-aware model base and configures
`objects`/`all_objects`; when soft delete is disabled, `all_objects` is not
installed by the ORM lifecycle and should not be treated as available. The
lifecycle extracts `Meta.use_soft_delete` and `Meta.rules` by deleting those
attributes from the original nested `Meta` class; the remaining `Meta`
attributes stay on the generated model. Rules moved onto the model metadata
participate in `full_clean()`. A nested `Factory` class on either the manager
attrs or the interface is copied into the generated factory before `Meta.model`
is attached.

## Defining fields

Declare Django model fields inside the interface. GeneralManager converts type hints on the manager into attribute accessors that resolve to these fields.

```python
from django.db import models
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

Foreign keys expose both the relation field and a raw ID helper. In the example
above, `book.author` resolves the related object or manager, while
`book.author_id` returns the stored foreign-key value directly. Prefer the raw
ID helper in hot paths when you only need the identifier.

## CRUD operations

Managers expose `create`, `update`, and `delete` methods through the writable ORM
capabilities configured by `DatabaseInterface`; the interface shell itself does
not define separate CRUD methods. Each manager method accepts `creator_id` and
`history_comment` arguments for audit trails. `update()` refreshes the current
manager in place and returns that same instance for chaining. `delete()`
invalidates the current manager instance for later field reads.

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

assert material.description == "Updated description"
latest_entry = material.history.order_by("-history_date").first()
```

Unknown write payload keys raise `UnknownFieldError`. Assignments that Django
rejects with `ValueError` are reported as `InvalidFieldValueError`; assignments
that raise `TypeError` are reported as `InvalidFieldTypeError`. Loading a missing
current row, or a missing historical row for `search_date`, propagates the
generated model's `DoesNotExist` exception.

Create and update payloads accept `creator_id` and `history_comment` alongside
model fields. The write path removes those metadata keys before field
normalization, validates payload keys, converts manager-valued foreign keys to
identifiers, and applies the atomic write contract described below. Deletes use
soft delete when enabled by setting `is_active=False`; otherwise they hard-delete
inside the configured database transaction. Both delete paths record history
comments and clear the same read cache. Internally the mutation capability returns
`{"id": pk}` to the manager layer. The public manager API turns that result into
the manager behavior described here: `create()` returns a manager instance,
`update()` refreshes and returns the same manager instance, and `delete()`
invalidates the current manager for later field reads.

### Atomic writes

For ordinary ORM `create()` and `update()` calls, the row save, audit and history
updates, and any many-to-many `<relation>_id_list` changes form one transaction
on the configured database alias. An exception during validation or saving,
history-reason handling, or many-to-many application leaves the complete
mutation rolled back. Upload-aware writes already use their dedicated atomic
upload transaction; their post-commit finalization behavior is unchanged.
Generated model history, including tracked many-to-many snapshots, follows the
same configured database alias as the live row.

After an update completes successfully, GeneralManager clears the saved row
from the run-scoped ORM read cache. If an update runs inside a caller-owned
transaction using the interface's database alias, fresh manager or interface
loads within that transaction see its current state without publishing the
uncommitted identity row to the run-scoped cache. After a rollback, subsequent
fresh loads therefore return the persisted state. The manager object updated in
place may retain transactional values if they were materialized before that
rollback; discard it and construct a fresh manager from its ID before
continuing.

For a task-oriented rollback pattern, see
[Keep ORM writes and history on one database](../../howto/orm_atomic_writes.md).
The [existing-model cookbook](../../examples/existing_model_interface.md#route-atomic-writes-and-history-to-another-database)
contains a directly usable multi-database example.

History-capable managers expose `manager.history` as the audit trail for that object's ID. The returned queryset lets you inspect or filter raw history entries directly:

```python
material.history.all()
material.history.filter(history_change_reason__icontains="import")
material.history.order_by("-history_date").first()
```

Use `search_date=...` when you want the manager itself, or a bucket of managers, to resolve as a point-in-time snapshot instead of returning raw history rows. For example, pass `search_date=...` to `filter()` or `exclude()` so the query resolves to historical manager state across many rows.

Reverse-relation filters accept snake_case roots for multi-word related models. For example, if `ChangeRequestFeasibility` points at `ChangeRequest`, prefer `ChangeRequest.filter(change_request_feasibility__id=...)`. The legacy Django-native reverse query root (for example `changerequestfeasibility__id`) remains supported for compatibility. This translation applies to relation roots, not bucket-style `_list` attribute names.

Generated interface attributes include stored model fields, foreign keys,
reverse one-to-one aliases, and collection relations. Collection relations use
`<name>_list` names, expose descriptor metadata with `relation_kind="collection"`
and `filter_lookup`, and resolve through the related GeneralManager when the
related model has one. The generated base name prefers the declared relation or
related model name, then the Django accessor fallback, then relation-field-based
fallback names. If every candidate would collide with an existing descriptor,
GeneralManager raises `DuplicateFieldNameError` during descriptor creation
instead of silently overwriting one relation. When a GeneralManager-backed
collection relation has no explicit relation lookup and no related field can be
found for the current model, accessing the generated descriptor raises
`MissingRelatedFieldsError`; if multiple related fields are found they are all
used as filter constraints.

### Soft deletes

New database-backed managers perform hard deletes by default. Add `use_soft_delete = True` to the interface's `Meta` class to keep the historical `is_active` flag and route `delete()` calls through a soft delete. When enabled GeneralManager automatically injects filtered managers (`objects` returns active rows, `all_objects` includes inactive ones), honours explicit `filter(is_active=…)` lookups, and preserves the existing history comments (`"… (deactivated)"`). Pass `include_inactive=True` to `filter()`/`exclude()` when you need the full dataset without touching the model's managers directly.

## Many-to-many relationships

Pass related IDs using the `<field>_id_list` convention. The interface saves the
main record before applying the relation, but both changes remain part of the
same create or update transaction.

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
from django.db.models import CharField, ForeignKey

from general_manager.interface import ReadOnlyInterface
from general_manager.manager import GeneralManager


class Country(GeneralManager):
    _data = [
        {"code": "US", "name": "United States"},
        {"code": "DE", "name": "Germany"},
    ]

    class Interface(ReadOnlyInterface):
        code = CharField(max_length=2, unique=True)
        name = CharField(max_length=50)
```

On startup the interface synchronises `_data` with the table, creating,
updating, or soft-deleting entries as needed (read-only interfaces force
`Meta.use_soft_delete = True`). Managers expose read operations only; write
attempts raise exceptions. Each row must include either a unique model field or
fields covered by `unique_together`/`UniqueConstraint`; those fields identify
the database row that should be updated or reactivated. Missing unique metadata
raises `MissingUniqueFieldError`, and missing unique values in a row raise
`InvalidReadOnlyDataFormatError`. When a model declares several unique fields
or constraints, GeneralManager treats the union of those fields as one
composite identity for synchronization, so every payload row must include every
field in that union.

`ReadOnlyInterface` itself is a capability shell. The parent manager owns
`_data`; the nested interface owns the generated Django field declarations. The
read-only lifecycle generates the backing model with `GeneralManagerBasisModel`,
registers the manager for startup schema checks and synchronization, and keeps
removed rows inactive rather than deleting them. Constructing a read-only
manager by id follows the normal ORM contract: the `id` is parsed with
`Input(int)`, optional `search_date` values support historical lookup, and
missing rows propagate the generated model's `DoesNotExist` exception.
Malformed `_data` payloads and unresolved relation lookups raise the dedicated
read-only errors documented in the API reference. The manager-level public API
is construction by id, attribute reads, `all()`, `filter()`, `exclude()`, and
history access; mutation entry points are inherited but unsupported because the
read-only bundle configures no create, update, or delete capability.

Duplicate composite identities in `_data` are not a validation error in the
full sync path. GeneralManager processes those rows in payload order against the
same database row, so later values can overwrite earlier values for that row.
Malformed relation lookup keys or values are passed to Django's query layer and
propagate its error; zero or multiple relation matches raise
`ReadOnlyRelationLookupError`.

Read-only `_data` can reference other read-only models. Foreign-key and
one-to-one values may be lookup dictionaries, including nested dictionaries
that are flattened into Django-style `__` lookups during synchronisation:

```python
class City(GeneralManager):
    _data = [
        {
            "code": "BER",
            "name": "Berlin",
            "country": {"code": "DE"},
        }
    ]

    class Interface(ReadOnlyInterface):
        code = CharField(max_length=3, unique=True)
        name = CharField(max_length=50)
        country = ForeignKey(Country.Interface._model, on_delete=models.PROTECT)
```

The startup hook orders related read-only interfaces before dependants, so
`Country` is synced before `City`. This ordering is based on direct
non-auto-created relation fields; transitive dependencies and cycles are
handled by the startup hook runner. Relation lookup dictionaries must match
exactly one related row; zero or multiple matches raise
`ReadOnlyRelationLookupError`. Many-to-many payloads use a list of lookup
dictionaries or identifiers and are applied after the main row is saved. Omit a
many-to-many key to leave the relation unchanged, set it to `None` to clear it,
or provide a list to replace it.

## Validation hooks

Override `clean()` or add rules in `Meta.rules` to validate data before it reaches the database. Use `constraints` for uniqueness and relational requirements—GeneralManager surfaces validation errors through GraphQL responses automatically.
