# Existing Model Interfaces

`ExistingModelInterface` (`general_manager.interface.ExistingModelInterface`) lets a manager wrap an existing Django model without generating new tables. It keeps the GeneralManager API intact—`create`, `update`, `delete`, factories, and history tracking all work the same—while pointing at the tables you already manage elsewhere. When the legacy model exposes an `is_active` column the interface enables soft delete automatically (`delete()` toggles the flag).

## When to use it

- You have legacy Django models and want GeneralManager orchestration without migrating data into freshly generated tables.
- Multiple apps already depend on a model definition and you need the manager layer to stay backward compatible.
- You want to incrementally adopt managers before refactoring model code.

If you control the schema and prefer GeneralManager to build models for you, stick with [`DatabaseInterface`](db_based_interface.md).

## Pointing a manager at a legacy model

Reference the model with either an import, `settings.AUTH_USER_MODEL`, or an app label string. Annotate the manager attributes you plan to read—GeneralManager derives field accessors from the legacy model instead of from interface field declarations.

```python
from django.conf import settings

from general_manager.interface import ExistingModelInterface
from general_manager.manager import GeneralManager


class User(GeneralManager):
    id: int
    username: str
    email: str | None

    class Interface(ExistingModelInterface):
        model = settings.AUTH_USER_MODEL


```

For wrappers that use the same class name as your Django model, keep the Django model in `models.py` and define the GeneralManager wrapper in `managers.py`. GeneralManager auto-imports `<app>.managers` for every installed app during startup before it initializes manager classes, which avoids import cycles and ensures the wrapper class is available for registration and `_general_manager_class` linking. Import the wrapper from `myapp.managers` in shells and app code so you do not confuse it with `myapp.models.User`; if the model is already imported, assign the wrapper class directly to `_general_manager_class` instead of relying on a string reference.

GeneralManager also customizes Django's `shell` command auto-import list. When
Django would auto-import a raw model that has a `_general_manager_class` type,
the shell command replaces that model import path with the wrapper import path.
Registered manager classes missing from the resolved import list are appended in
reverse registration order so same-name wrappers win like normal shell
auto-imports. If Django disables shell auto-imports, or if the underlying Django
command exposes no auto-import provider, GeneralManager preserves that `None`
result.

During manager class creation, GeneralManager resolves the `model` reference,
caches the resolved Django model on the concrete interface, and links the
generated manager class back onto the model as `_general_manager_class`. Model
references may be Django model classes or strings accepted by Django's app
registry, including `settings.AUTH_USER_MODEL` and `"app_label.ModelName"`.
Invalid or missing model references fail early with the existing-model
configuration errors. The lifecycle also registers database-aware
django-simple-history tracking when history is not already present and includes
local many-to-many fields in that registration. Generated history rows and
many-to-many snapshots then use the same database alias as the wrapped row. The
soft-delete capability is enabled when the resolved model exposes an `is_active`
attribute; deletes then toggle that flag instead of removing the row. For
soft-delete-aware models, the lifecycle sets up `objects` plus `all_objects`
managers. If the legacy model already has an unfiltered `all_objects`,
GeneralManager uses it. If it does not, GeneralManager falls back to the model's
`_default_manager`; that fallback follows the legacy model's default manager
behavior and is not guaranteed to include inactive rows when the default manager
filters them.

The simple-history marker checked by the lifecycle is
`model._meta.simple_history_manager_attribute`. Interface rules are the optional
`Meta.rules` sequence declared on the `ExistingModelInterface` subclass. The
interface class itself selects the existing-model lifecycle; create, update,
delete, query, history, and factory signatures remain the inherited
GeneralManager and writable ORM capability APIs shown in the manager examples
below.

The inherited constructor accepts the wrapped row `id` through the default
`Input(int)` field and optional `search_date: datetime | None`. Naive search
dates are made timezone-aware with Django's current timezone, and historical
lookup uses `OrmInterfaceBase.historical_lookup_buffer_seconds` to decide when
to read from history tables instead of the current row.

`ExistingModelInterface.get_field_type("field_name")` is available when you
need the type GeneralManager exposes for a legacy field or generated helper.
Calling it resolves the interface's own model first and then delegates to ORM
read support. Stored model fields return the Django field class, managed
relations return the related model's `_general_manager_class` when present, and
generated relation/custom descriptors return their metadata `type`. Unknown
names raise Django's `FieldDoesNotExist`. A managed relation is a Django
relation whose related model has `_general_manager_class`; generated descriptors
are ORM support descriptor-map entries for custom fields and generated relation
helpers. The model cache is local to the concrete interface class: if you
subclass one wrapper and declare a different `model`, the subclass resolves its
own declaration instead of reusing the parent wrapper's cached model.

If a legacy model already has django-simple-history tracking and its interface
selects a non-default `database`, declare the tracker with GeneralManager's
database-aware records class before defining the manager:

```python
from django.db import models

from general_manager.interface.utils.history import DatabaseAwareHistoricalRecords


class LegacyCustomer(models.Model):
    groups = models.ManyToManyField("auth.Group")
    history = DatabaseAwareHistoricalRecords(m2m_fields=["groups"])
```

GeneralManager cannot safely replace an existing simple-history tracker's
connected signal receivers. It therefore raises
`UnsafeHistoryConfigurationError` during manager class creation when a
pre-registered tracker lacks the database-aware marker on a non-default alias.
Existing trackers remain compatible for the default alias.

`DatabaseAwareHistoricalRecords` and `UnsafeHistoryConfigurationError` are
required implementation contracts for this non-default-alias case in 0.63.1,
but they are not stable `general_manager.interface` exports. Use the documented
setup below without treating their implementation-module paths as a broader
compatibility promise.

## Auditing and validation

- `create` and `update` assign `changed_by_id` when the model exposes that column and record `history_comment` values using `django-simple-history`.
- `delete` toggles `is_active` (when the column exists) and appends `" (deactivated)"` to the provided history comment; if your legacy model lacks that field the interface performs a hard delete instead. Use `filter(include_inactive=True)` when you need to surface soft-deleted rows explicitly.
- Define `Meta.rules` on the interface to add GeneralManager validation
  alongside any rules already declared on the model. The interface appends the
  interface rules after existing model rules and replaces `full_clean()` so the
  combined rule set runs consistently everywhere.
- Rule validation runs after Django's normal `full_clean()` field, uniqueness,
  and constraint checks. Rules that return `False` contribute their
  `get_error_message()` mapping; rules that return `True` or `None` do not add
  errors. When Django validation and rules report the same field, their messages
  are merged instead of replacing each other.

## Writing data through the manager

`ExistingModelInterface` reuses the same write helpers as `DatabaseInterface`:

See [Keep ORM writes and history on one database](../../howto/orm_atomic_writes.md)
for the complete configuration and rollback workflow.

- `create()` and `update()` share the
  [`DatabaseInterface` atomic write contract](db_based_interface.md#atomic-writes),
  including its upload-aware and transaction-aware cache behavior.
- `update()` refreshes the current manager in place and returns that same manager instance. Reassigning the return value is optional now, so `customer.update(name="New")` immediately makes `customer.name` reflect the saved value.
- `history` exposes the audit trail queryset for the wrapped legacy row, scoped to the current object's ID. Use it when you want raw historical entries rather than a manager snapshot.
- `delete()` invalidates the current manager instance for subsequent field reads. This applies to both hard deletes and soft deletes on models with `is_active`; the row may still exist for `include_inactive=True` lookups, but the deleted manager object itself should be treated as spent.

- Pass many-to-many identifiers with the `<relation>_id_list` convention or provide GeneralManager instances; the interface normalises and applies them as part of the same create or update transaction as the main record.
- Foreign key assignments accept manager instances or raw IDs.
- `filter`, `exclude`, and `all` return manager instances backed by the legacy rows. Use `search_date=...` on `filter()`/`exclude()` or `ManagerInterface(search_date=...)` when you want a point-in-time manager view instead of raw history records.

```python
customer.update(name="Acme International", history_comment="rename")
assert customer.name == "Acme International"
assert customer.history.order_by("-history_date").first().history_change_reason == "rename"

customer.delete(history_comment="manual block")
# `customer` is now invalid for attribute reads even when the row was soft-deleted.
```

No schema changes are generated by this interface—keep running your migrations and model definitions in the owning app.

## Factory support

An `AutoFactory` subclass is built automatically for each manager. It reuses any attributes you place on an inner `Factory` definition and populates missing fields using the legacy model metadata. Calling `User.Factory.create()` returns manager instances, so your tests keep existing fixtures while benefiting from the richer interface surface. Factory-created managers follow the same lifecycle contract as any other manager instance: updates mutate the current manager object in place, and deletes invalidate that object for later field reads.

If both the manager class and interface class provide a nested `Factory`, the
manager-level definition wins. Public attributes from the selected `Factory` and
its nested `Meta` are copied into the generated factory, but `Meta.model` is
always replaced with the resolved legacy model. Only directly declared non-dunder
attributes are copied from the selected factory definition; inherited attributes
are not copied from the prototype class dictionary. Re-running the lifecycle
rebuilds the generated factory and re-applies interface rules, so interface rule
lists are appended again rather than deduplicated.

Because history tracking is still attached to the underlying legacy row, in-place updates do not change audit behavior. You still get the same `changed_by_id` and `history_comment` entries as before; only the in-memory manager identity contract changed.

Looking for sample code? See the [Existing model interface cookbook recipes](../../examples/existing_model_interface.md) for end-to-end snippets you can drop into your project.
