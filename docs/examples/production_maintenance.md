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

## Reconcile Meilisearch settings directly

After configuring `MeilisearchBackend`, callers that own an index can apply its
settings directly. Repeating the same call reads the effective settings and
submits no redundant settings tasks; changing an ordered list such as
`searchable_fields` or a set-like list such as `filterable_fields` submits only
the affected setting. Identity contracts add `gm_identity.*` shadow fields
only for identity names included in `filterable_fields`.

```python
import os

from general_manager import MeilisearchBackend


backend = MeilisearchBackend(
    url="http://127.0.0.1:7700",
    api_key=os.environ.get("MEILISEARCH_API_KEY"),
)
settings = {
    "searchable_fields": ["name", "status"],
    "filterable_fields": ["type", "status", "id"],
    "sortable_fields": ["name"],
    "identity_fields": {"Project": {"id": int}},
}

backend.ensure_index("global", settings)
backend.ensure_index("global", settings)  # no settings tasks when unchanged
```

Omit a searchable, filterable, or sortable setting to leave it unmanaged; pass
an explicit empty list to clear it. Ranking rules are always normalized with
`sort` first. Each submitted task completes before the next setting is
considered, and a read or task failure propagates so the next invocation can
retry.

For deliberate full backend recovery, an operator can select
`search_reconcile --once --force`. This can rebuild every configured pair and
remove stale documents; assess its cost before use. `search_index --reindex`
only upserts and cannot substitute for stale-document removal.

See the [settings and migration guide](../howto/startup_maintenance.md) for
precedence, `call_command()` usage and consumer adoption notes.
