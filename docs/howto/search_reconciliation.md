# Run search reconciliation

Search reconciliation initializes and repairs search indexes without tying work
to a user request. It works alongside normal per-object indexing from manager
create, update, and delete operations.

## What reconciliation does

Each searchable manager/index pair gets a durable state row. The reconciler:

- creates missing state rows and marks them for first initialization
- detects search configuration changes with a schema fingerprint
- rebuilds dirty manager/index pairs
- clears dirty state only after a successful backend write
- keeps the dirty marker and records `last_error` when reconciliation fails

The schema fingerprint includes the manager import path, index fields, filters,
sorts, boosts, min score, type label, update strategy, and custom
`document_id`/`to_document` callable paths. Stored manager paths must import to a
`GeneralManager` class; otherwise reconciliation records the validation error on
that state and continues with other claimed rows.
Fingerprint payloads keep configured field/filter/sort order, preserve
duplicates, sort JSON object keys before hashing, and use callable
`module.qualname` paths when available, falling back to `module.__name__` and
then `repr`.

## Production with Celery Beat

Enable async indexing and reconciliation:

```python
GENERAL_MANAGER = {
    "SEARCH_BACKEND": {
        "class": "general_manager.search.backends.meilisearch.MeilisearchBackend",
        "options": {
            "url": "http://meilisearch:7700",
            "api_key": MEILISEARCH_API_KEY,
        },
    },
    "SEARCH_ASYNC": True,
    "SEARCH_RECONCILE_ENABLED": True,
    "SEARCH_RECONCILE_INTERVAL_SECONDS": 60,
}
```

Run a Celery worker and Celery Beat. This example assumes the Django project
package is `mysite`; replace only the Celery app path if the project already
uses a different package name.

```bash
celery -A mysite worker -l info
celery -A mysite beat -l info
```

Celery Beat schedules
`general_manager.search.tasks.reconcile_search_indexes_task`. When no index
state is dirty, the task exits quickly. If an index is dirty, one worker claims
the state row, reindexes the affected manager/index pair, and clears the marker
after success.

`ensure_search_index_states(force=False)` creates or refreshes durable state rows
and returns `created`, `updated`, and `unchanged` counts. `force=True` marks all
configured rows dirty, and `updated` includes both forced dirtying and schema
changes. Obsolete rows for removed managers or indexes are not deleted.
`mark_search_indexes_dirty(manager_class, reason=...)` marks every configured
index entry for one manager and returns the number of entries processed.
Duplicate configured index names address the same durable row but still
increment loop counts once per config entry. Marking an already-dirty row
preserves the first `dirty_since` timestamp and overwrites `dirty_reason`.

`reconcile_search_indexes(force=False, max_states=None)` returns `created`,
`updated`, `skipped`, `claimed`, `reconciled`, `failed`, and `documents`.
`claimed`, `reconciled`, and `failed` count durable state rows; `documents`
counts successful `SearchIndexer.reindex_manager_index()` document counts.
`skipped` is populated only when no dirty rows are claimed and counts clean rows.
`max_states` limits claimed dirty rows for the current sweep; unclaimed dirty
rows remain dirty and are not counted as skipped. Expired claims are eligible
like unclaimed dirty rows, while unexpired claims are left for their owner.
Ensure counts are included in the result for both skipped and claimed sweeps.
Successful reconciliation clears `last_error`; a new per-row import, validation,
backend, or serialization failure overwrites it. Database errors while ensuring,
claiming, releasing, or clearing states propagate.

Call `configure_search_reconcile_beat_schedule_from_settings()` during Celery
app startup when the deployment wants GeneralManager to install the periodic
reconciliation task. `GENERAL_MANAGER["SEARCH_RECONCILE_ENABLED"]` and
`GENERAL_MANAGER["SEARCH_RECONCILE_INTERVAL_SECONDS"]` take precedence over the
top-level Django settings with the same names. Missing enabled settings default
to `False`; missing or invalid intervals default to `60` seconds; valid
integer-like intervals are clamped to at least one second.

The Beat helper returns `True` after writing or replacing the schedule entry
named `general_manager.search.reconcile`. It returns `False` when reconciliation
is disabled, Celery is unavailable, or the module-level Celery `current_app` is
`None`. Existing Beat schedule mappings are preserved, malformed non-mapping
schedule values are treated as empty, and Celery app access or assignment errors
propagate. The installed entry uses task
`general_manager.search.tasks.reconcile_search_indexes_task`, a float seconds
schedule, no args or kwargs, and `options={"queue": "search.reconciliation"}`.

`reconcile_search_indexes_task()` returns exactly `reconciled`, `failed`, and
`documents` counts from one reconciliation sweep. Exceptions from the
reconciliation service propagate to the Celery worker or direct caller.

## Development with Celery

Use the same settings and commands as production. This is the best local setup
when you want to exercise deployment behavior.

## Development without Celery

Run one initialization pass:

```bash
python manage.py search_reconcile --once
```

The command requires exactly one run mode: `--once` runs one sweep and exits,
while `--watch` repeats until interrupted. Service errors propagate so command
runners and CI fail visibly.

Keep the in-memory development backend initialized while you work:

```bash
python manage.py search_reconcile --watch --interval 5
```

The watch command uses the same reconciliation service as Celery Beat, so
behavior stays consistent without adding request-time side effects. Intervals are
parsed as seconds and clamped to at least one second.

## Force a rebuild

Use `--force` after large imports, backend recovery, or manual index deletion:

```bash
python manage.py search_reconcile --once --force
```

In watch mode, `--force` applies to the first sweep only; later sweeps reconcile
normally. Use `--max-states N` to cap each sweep to a positive number of dirty
states.

## Troubleshooting

- If searches return no results after startup, run
  `python manage.py search_reconcile --once`.
- If reconciliation keeps retrying, inspect `SearchIndexState.last_error`.
- If production never reconciles, confirm Celery Beat is running and
  `SEARCH_RECONCILE_ENABLED` is `True`.
- If normal mutations are not reflected quickly, confirm `SEARCH_ASYNC` and the
  Celery worker are configured correctly.

`SearchIndexState` is the durable row behind reconciliation. It is unique per
`manager_path` and `index_name`, stores the current `schema_fingerprint`, and
uses dirty fields plus claim fields to coordinate workers. `mark_dirty(reason)`
preserves the first `dirty_since` timestamp while replacing `dirty_reason` with
the provided string; pass one of `SEARCH_INDEX_DIRTY_REASON_INITIALIZATION`,
`SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED`,
`SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED`, or
`SEARCH_INDEX_DIRTY_REASON_FORCED` because the method does not pre-validate
choices. `clear_dirty()` records a successful run, sets `initialized_at` on
first success, updates `last_reconciled_at`, clears dirty fields, releases claim
fields, and clears `last_error`. Both methods use `django.utils.timezone.now()`
for timestamps. The reconciliation planner creates and updates
`schema_fingerprint`; claim acquisition and expiration are handled by the
reconciler helpers rather than direct model methods. Database save errors
propagate from both methods.
