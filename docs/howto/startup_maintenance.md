# Run startup maintenance deliberately

`READ_ONLY_SYNC_ON_STARTUP` now defaults to Django `DEBUG`. This changes
production behavior for consumers that previously relied on the unset default
of `True`. Development (`DEBUG=True`) keeps automatic read-only synchronization
before management commands; production (`DEBUG=False`) requires explicit
maintenance. Existing stored data stays intact.

## Configure automatic synchronization

Merge explicit settings into the existing dictionary:

```python
GENERAL_MANAGER = {
    "READ_ONLY_SYNC_ON_STARTUP": False,
}
```

Supported precedence, including explicit `False`, is:

1. `GENERAL_MANAGER["READ_ONLY_SYNC_ON_STARTUP"]`
2. `GENERAL_MANAGER_READ_ONLY_SYNC_ON_STARTUP` (legacy prefixed setting)
3. `READ_ONLY_SYNC_ON_STARTUP` (legacy top-level setting)
4. Django `DEBUG` when none is set

Explicit `True` retains previous production behavior; explicit `False` disables
automatic synchronization in development too. Non-dictionary `GENERAL_MANAGER`
values are ignored by this lookup.

This setting controls only automatic read-only data sync. Manager/model registry
discovery, system checks, signal registration, remote APIs and configured GraphQL
construction continue. Checks can read database metadata and warn about missing
migrations. Custom startup hooks and consumer-owned initialization retain their
own behavior.

The runner wraps `BaseCommand.run_from_argv()`. `runserver` skips hooks in the
autoreload parent, runs them in the serving child, and runs them with
`--noreload`. `call_command()` invokes `execute()` directly and does not run
automatic hooks. Plain WSGI/ASGI `django.setup()` does not invoke this runner.

## Deploy in order

Run maintenance once per deployment from a designated job, after migrations and
before depending on updated reference data:

```bash
python manage.py migrate --noinput &&
python manage.py sync_read_only
```

Then reconcile search **when required**:

```bash
python manage.py search_reconcile --once
```

Keep `DEBUG=False` and automatic sync disabled or unset in the deployment job
as well as web and worker processes. An explicit legacy `True` would still sync
before `migrate`. Avoid performing maintenance in every replica or arbitrary
`manage.py` invocation.

`sync_read_only` creates, updates, reactivates and soft-deactivates rows to match
manager `_data`. It preflights all selected schemas and fails if migrations are
missing, schemas differ, dependencies form a cycle or synchronization raises.
`CommandError` produces CLI status 1 and propagates to Python callers. Completed
managers can remain updated if a later manager fails; repair the named failure
and rerun. There is no global transaction across managers or databases.

## Select managers

The default synchronizes all registered read-only managers. Repeat `--manager`
to select roots; their transitive foreign-key, one-to-one and many-to-many
read-only dependencies are included and synchronized first:

```bash
python manage.py sync_read_only --manager Country --manager Currency
python manage.py sync_read_only --manager your_app.managers.Country
```

Short names must be unique. Use the exact `module.qualname` path shown in
ambiguous-name errors. Unknown or ambiguous selections fail before sync. An
empty programmatic manager list is invalid; a project with no read-only
managers and no filter succeeds with a zero-manager summary.

The command works with automatic sync disabled and suppresses duplicate
automatic read-only sync during its own CLI invocation. Other startup hooks
continue. Configured management capabilities and database routers/aliases are
retained; dependencies may be checked again by the existing recursive sync API.

```python
from django.core.management import call_command

call_command("sync_read_only")
call_command("sync_read_only", managers=["your_app.managers.Country"])
```

## Choose search maintenance

There is no general full-search reindex in GeneralManager `AppConfig.ready()`.
`SEARCH_AUTO_REINDEX` defaults to `False`; enabling it lazily hydrates
`DevSearchBackend` when an index is first accessed. External backends ignore
that development option. `SEARCH_RECONCILE_ENABLED` defaults to `False`; ready
configures a Celery Beat schedule when enabled, without running a sweep there.
Neither setting is a production startup reindex hook or controlled by the
read-only synchronization setting.

`search_reconcile --once` initializes new states and rebuilds dirty or
configuration-changed manager/index pairs, removing stale documents for those
pairs. It fails nonzero if a claimed pair fails; inspect logs and
`SearchIndexState.last_error`. `--watch` reports failed sweeps to stderr and
keeps retrying. A sweep can leave work owned by an active claim or a newer dirty
generation; success is not a concurrency barrier. `--max-states N` bounds the
sweep and can leave additional dirty states pending.

`search_index --reindex` only upserts current documents and does **not** remove
stale ones. Read-only sync writes via the ORM and does not guarantee that normal
GeneralManager mutation events dirty its search indexes. After a searchable
read-only payload change, explicitly dirty each affected searchable manager's
indexes and reconcile, or plan a forced repair. Include owner managers whose
documents derive values from changed read-only rows: their declared related
invalidation rules also depend on lifecycle events and will not fire for these
ORM writes. For a change affecting only Country's own documents:

```python
from your_app.managers import Country
from general_manager.search.reconciliation import mark_search_indexes_dirty

mark_search_indexes_dirty(Country, reason="read_only_deployment")
```

```bash
python manage.py search_reconcile --once
# Operator-selected full repair after imports, backend loss or stale documents:
python manage.py search_reconcile --once --force
```

`--force` marks every configured pair dirty and can rebuild the full corpus.
Assess its cost and concurrency; it is not mandatory on every deployment.
Failed rebuilds can have partial backend effects; dirty state remains for retry.
See [search reconciliation](search_reconciliation.md) for details.

## Migrate consumers, including KnowledgeHub

When adopting the release containing this change:

1. Audit dictionary and both legacy settings. Preserve deliberate overrides;
   remove unintended production `True` values when adopting explicit maintenance.
2. Add `sync_read_only` after `migrate` in one deployment job and fail that job on
   command errors. Changes to `_data` no longer apply merely by running ordinary
   production commands.
3. Audit consumer-owned search startup code separately. Identify both searchable
   read-only sources and owners whose documents derive their values; dirty every
   affected owner before targeted reconciliation, or plan a forced repair when
   that scope cannot be enumerated. Retain regular workers/Beat where used.
4. Verify reload/no-reload development, programmatic commands, migrations,
   reference-data availability and search freshness against the selected version.

These are the supported GeneralManager integration points for KnowledgeHub.
KnowledgeHub settings/deployment changes follow separately after the PR and
released version are available.
