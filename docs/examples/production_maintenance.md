# Production reference-data maintenance

For a read-only `Country` manager, use `DEBUG=False` and merge
`READ_ONLY_SYNC_ON_STARTUP=False` into existing `GENERAL_MANAGER` settings, or
leave the setting unset to follow `DEBUG`.

Use one deployment maintenance job:

```bash
set -e
python manage.py migrate --noinput
python manage.py sync_read_only --manager your_app.managers.Country
```

The command includes read-only dependencies. An unknown selector, stale schema
or sync failure fails the job. Restarting web processes or running `check`,
`shell` or `collectstatic` no longer applies `_data` automatically in production.

If Country is searchable and its payload changed, and no other manager's search
documents depend on those values, mark its configured indexes dirty after sync,
then reconcile:

```bash
python manage.py shell -c "from your_app.managers import Country; from general_manager.search.reconciliation import mark_search_indexes_dirty; mark_search_indexes_dirty(Country, reason='read_only_deployment')"
python manage.py search_reconcile --once
```

Replace the manager path with the actual registered path. The marking call must
finish before reconciliation. A failed one-shot sweep exits nonzero and leaves
the failed pair dirty for repair.
If other searchable owners include Country values, dirty those managers too:
read-only ORM synchronization does not emit the lifecycle events that trigger
related invalidation rules.

For deliberate full backend recovery, an operator can select
`search_reconcile --once --force`. This can rebuild every configured pair and
remove stale documents; assess its cost before use. `search_index --reindex`
only upserts and cannot substitute for stale-document removal.

See the [settings and migration guide](../howto/startup_maintenance.md) for
precedence, `call_command()` usage and consumer adoption notes.
