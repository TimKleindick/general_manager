# Migration Gotchas

GeneralManager integrates tightly with Django migrations. Keep the following tips in mind to avoid unexpected issues.

## Import order

Always import manager modules in `apps.py` (inside `AppConfig.ready()`). If Django does not evaluate your interfaces, it cannot generate migrations for the derived models.

## Model renames

When you rename a manager attribute or interface field, update existing migrations accordingly. Django may consider it a field deletion plus addition otherwise.

## Measurement fields

`MeasurementField` stores value and unit columns. When you change the base unit, create a data migration that rescales stored values to the new unit to avoid inconsistent data.

## Read-only interfaces

Read-only data synchronises `_data` entries during startup. If you remove entries, they are soft-deleted (`Meta.use_soft_delete = True` is forced for read-only managers). Write data migrations when you need permanent removal.

## Historical data

Database interfaces integrate with `django-simple-history`. When you backfill migrations, ensure history tables are populated before managers expect them.

## Built-in workflow migrations

GeneralManager's built-in workflow migrations are historical schema files. The
initial workflow migrations create the workflow event, outbox, delivery-attempt,
and execution tables plus their indexes; they contain no custom `RunPython`
operations, public helper methods, `Any` annotations, or `type: ignore`
comments. Do not edit those generated migration files to silence drift. The
built-in workflow index alignment is maintained by current model metadata, not
by rewriting `0001_initial.py` or
`0002_workflow_outbox_scaling_indexes.py`. Keep the current workflow model
`Meta.indexes` names aligned with the checked-in migration names so Django does
not generate no-op index-rename migrations.
`0003_workflow_execution_correlation_constraint.py` depends on
`0002_workflow_outbox_scaling_indexes.py` and contains one schema operation: it
adds `general_manager_workflow_exec_active_corr_uniq` to
`WorkflowExecutionRecord`. That partial unique constraint covers
`(workflow_id, correlation_id)` only when `correlation_id` is not `NULL`, is not
an empty string, and `state` is one of `pending`, `running`, `waiting`, or
`completed`. The migration freezes those active-plus-completed state strings in
the migration file itself and exposes no public helper methods. Do not import
runtime workflow engine constants from that historical migration; future
state-vocabulary changes must not alter old partial-constraint semantics.
Schema-application failures come from Django or the database and are not
wrapped by the migration file.
For this built-in alignment, no edits to `0001_initial.py` or
`0002_workflow_outbox_scaling_indexes.py` are required or expected. Confirm that
with:

```bash
git diff -- src/general_manager/migrations/0001_initial.py src/general_manager/migrations/0002_workflow_outbox_scaling_indexes.py
```

## Built-in search migrations

`0004_search_index_state.py` uses the Django dependency tuple
`("general_manager", "0003_workflow_execution_correlation_constraint")` and
creates the durable `SearchIndexState` table. It performs six schema operations:
create the model, add the unique constraint, and add four indexes. The model has
`id`,
`manager_path`, `index_name`, `schema_fingerprint`, `initialized_at`,
`last_reconciled_at`, `dirty_since`, `dirty_reason`, `claim_token`,
`claimed_at`, `claim_expires_at`, `last_error`, `created_at`, and `updated_at`
fields. `dirty_reason` defaults to an empty string and allows
`("initialization", "Initialization")`,
`("schema_changed", "Schema changed")`, `("data_changed", "Data changed")`,
and `("forced", "Forced")`. It adds the
`general_manager_search_state_manager_index_uniq` unique constraint on
`(manager_path, index_name)` plus four operational indexes:
`general_man_dirty_s_71fc00_idx` on `(dirty_since, index_name)`,
`general_man_claim_t_3aaacc_idx` on `claim_token`,
`general_man_claim_e_1fa228_idx` on `claim_expires_at`, and
`general_man_last_re_81038c_idx` on `last_reconciled_at`. Keep the current
`SearchIndexState.Meta.indexes` names aligned with those checked-in migration
names so Django does not generate no-op index-rename migrations. The migration
contains no public helper methods, accepts no application input, returns no
application output, and leaves Django/database schema errors unwrapped.

When checking migration drift for this repository, ensure the worktree package is
on `PYTHONPATH`; otherwise Django may inspect an installed package copy:

```bash
PYTHONPATH=src python -m django makemigrations --check --dry-run --settings=tests.test_settings
```

A clean check prints `No changes detected`.

## Rolling back

Because managers call `full_clean()` during operations, old migrations can fail if data violates new validation rules. Plan rollback steps by capturing export snapshots before applying schema changes.
