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

## Development with Celery

Use the same settings and commands as production. This is the best local setup
when you want to exercise deployment behavior.

## Development without Celery

Run one initialization pass:

```bash
python manage.py search_reconcile --once
```

Keep the in-memory development backend initialized while you work:

```bash
python manage.py search_reconcile --watch --interval 5
```

The watch command uses the same reconciliation service as Celery Beat, so
behavior stays consistent without adding request-time side effects.

## Force a rebuild

Use `--force` after large imports, backend recovery, or manual index deletion:

```bash
python manage.py search_reconcile --once --force
```

## Troubleshooting

- If searches return no results after startup, run
  `python manage.py search_reconcile --once`.
- If reconciliation keeps retrying, inspect `SearchIndexState.last_error`.
- If production never reconciles, confirm Celery Beat is running and
  `SEARCH_RECONCILE_ENABLED` is `True`.
- If normal mutations are not reflected quickly, confirm `SEARCH_ASYNC` and the
  Celery worker are configured correctly.
