# Migration Gotchas

GeneralManager generates or wraps Django models; Django migrations remain the
record of their schema. This guide covers changes to a consumer application's
managers and the deployment steps needed when upgrading GeneralManager.

## Import order

Put manager definitions in `managers.py` inside an installed Django app.
GeneralManager imports those modules before initializing managers during
startup, including management commands. You do not need a second import in
`AppConfig.ready()`. If you split definitions across a `managers/` package,
import them from its `__init__.py` so discovery reaches each class.

A `DatabaseInterface` creates its backing model when the manager class is
defined. `ExistingModelInterface` wraps an existing model and may register
history models. Both must be discoverable before Django compares model state
with migrations. Calculation and request interfaces do not create tables.

For the `projects` app from the [quickstart](../quickstart.md), the normal
development workflow is:

```bash
python manage.py makemigrations projects
python manage.py migrate --plan
python manage.py migrate
```

Read the generated operations before applying them. Include the new migration
files with the manager changes. In deployment, apply the committed migrations
with `migrate --noinput`; do not generate a different migration in each
environment.

## Model renames

A persisted field declared on an ORM interface is part of the schema. A Python
method or computed GraphQL property is not itself a database column; renaming
one may change the API without requiring a schema migration.

Add a new migration when a change renames the generated model or a persisted
field. Renaming an `ExistingModelInterface` wrapper alone does not rename its
explicitly configured Django model, although its public API can change. Do not
rewrite an applied migration to match the new schema: other databases still
need a transition from the old schema.

Check that Django detected a `RenameModel` or `RenameField` when data should be
preserved. A deletion followed by an addition can lose data. Review generated
history models, relations, indexes, and constraints as well as the main model.
For a measurement, review both backing columns described below.

For a required new field on populated tables, use separate steps: add it as
nullable, backfill existing rows, then add the non-null constraint. If old and
new application processes overlap during deployment, keep the intermediate
schema compatible with both until the cutover is complete.

## Measurement fields

`MeasurementField(base_unit="kg")` exposes one Python attribute but stores
two columns. For a field named `weight`:

- `weight_value` holds the magnitude converted to the base unit, using
  `DecimalField(max_digits=30, decimal_places=10)`.
- `weight_unit` holds the public spelling of the assigned unit. Assigning
  `Measurement(500, "g")` stores a base magnitude of `0.5` and the unit
  `gram`; reading the attribute reconstructs `500 gram`.

Changing the base unit changes how existing magnitudes are interpreted.
Changing `kg` to `g` therefore requires multiplying the stored value by
`1000`; changing the unit label alone would corrupt the meaning. A schema
migration does not perform this conversion for you. Use a data migration with
the old and new units fixed in its code, and account for decimal precision,
nulls, and any existing history rows. Do not derive the old unit from the
current manager definition.

Review the logical field and its backing columns together in generated
migrations, including defaults and constraints. Plan the conversion and code
deployment as one cutover so processes using different base units do not
write to the same columns concurrently. See
[measurement fields](../concepts/measurement/field_design.md) for the runtime contract.

## Read-only interfaces

Read-only data synchronises `_data` automatically before management commands
when `READ_ONLY_SYNC_ON_STARTUP` is enabled (unset follows Django `DEBUG`). In
production, run `python manage.py migrate --noinput` before
`python manage.py sync_read_only` in one deployment job. Removed entries are
soft-deleted (`Meta.use_soft_delete = True` is forced for read-only managers).
Write data migrations for permanent removal. See
[startup maintenance and consumer migration](startup_maintenance.md) for
disabling startup synchronization and coordinating the deployment job.

## Historical data

Django's **historical migration models** and GeneralManager's **record
history** serve different purposes. Migration models describe the schema at
one point in the migration graph. Record history stores earlier versions of
application data through `django-simple-history`.

In a `RunPython` operation, use the supplied `apps.get_model()` registry.
Do not import the current manager or use `Project.Interface._model`: those
describe today's schema and can break when an old migration is replayed.
Use the migration connection's database alias explicitly.

For example, after a schema migration adds a nullable `display_name` to the
quickstart's `Project`, create a data migration:

```bash
python manage.py makemigrations projects --empty --name backfill_display_name
```

Keep its generated dependency on the migration that added the field. Add this
function and set the migration's `operations` to the list shown:

```python
from django.db import migrations
from django.db.models import F


def backfill_display_name(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    Project.objects.using(schema_editor.connection.alias).filter(
        display_name__isnull=True,
    ).update(display_name=F("name"))


# Inside the generated Migration class:
operations = [
    migrations.RunPython(backfill_display_name, migrations.RunPython.noop),
]
```

The reverse operation deliberately leaves the copied values in place; reversing
the earlier schema migration removes the column. Add dependencies for any
other app whose historical models the function reads. See
[Django's historical-model guidance](https://docs.djangoproject.com/en/5.2/topics/migrations/#historical-models).

Historical migration models do not provide the current manager's permission,
rule, or write lifecycle. The `update()` above also does not create
`django-simple-history` snapshots. Decide separately whether a backfill
should preserve existing snapshots, transform their stored representation,
or establish an initial snapshot. Review the generated history-table
migrations, including tracked many-to-many tables. Registering history for a
legacy model does not reconstruct past changes.

## Built-in workflow migrations

The installed package supplies the `general_manager` app's workflow schema.
Apply it through the project's normal `migrate` command before starting
workers that need new tables or constraints. Do not copy or edit the package's
old migrations to resolve a consumer schema change.

Workflow correlation uniqueness and state handling are part of the
[workflow contract](../concepts/workflow.md). For an upgrade, inspect the
pending operations with `python manage.py migrate --plan` and follow the
release notes. A database constraint failure needs investigation of the
existing rows; marking the migration as applied does not resolve it.

## Built-in search migrations

The same `migrate` command applies the durable search-state schema. In
particular, `0011_search_index_state_dirty_generation` adds
`dirty_generation` with default `0`. Search invalidation and reconciliation
use this counter to prevent stale work from clearing a newer mutation. When
upgrading from before that migration, apply it before starting code or workers
that depend on the counter; no manual backfill is required.

After migrating a deployment that uses search, run one reconciliation sweep to
initialize or repair durable state before relying on incremental work:

```bash
python manage.py search_reconcile --once
```

Use `--force` if the deployment also performed direct M2M through-table,
raw SQL, or bulk writes that bypass supported invalidation signals. Reconcile
after those writes finish. See the
[search guide](../concepts/search.md) for backend configuration and worker
operation.

## Rolling back

Plan schema, code, and data rollback together. Reversing a schema migration
does not recover discarded rows or undo an external search-index change.
Keep a database backup before destructive changes and document any
irreversible data transformation. A forward repair may be safer than running
old code against a newer schema.

Current manager validation is not automatically applied by historical
migration models. Keep data transformations explicit and independent of
today's manager methods so both a fresh installation and an upgrade can
follow the same migration history.
