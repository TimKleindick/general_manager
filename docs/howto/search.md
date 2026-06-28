# Search Tutorial

This tutorial walks through enabling search with Meilisearch, indexing data, and
querying via GraphQL. It assumes Django + the auto GraphQL schema are enabled.

## Step 1: Configure a backend

Add a search backend in `settings.py`:

```python
GENERAL_MANAGER = {
    "SEARCH_BACKEND": {
        "class": "general_manager.search.backends.meilisearch.MeilisearchBackend",
        "options": {
            "url": "http://127.0.0.1:7700",
            "api_key": None,
        },
    }
}
```

Start Meilisearch locally:

```bash
docker run --rm -p 7700:7700 --name meilisearch \
  -e MEILI_NO_ANALYTICS=true \
  getmeili/meilisearch:v1.34.0
```

You can use other backends by swapping the backend class and options; ensure
the backend is implemented and available in your environment.
`GENERAL_MANAGER["SEARCH_BACKEND"]` takes precedence over a top-level
`SEARCH_BACKEND` setting. The value may be a backend instance, class, factory,
dotted import path, or the mapping form shown above. Leave it unset to use the
in-memory `DevSearchBackend` fallback in local development.

## Step 2: Add SearchConfig to a manager

Define searchable, filterable, and sortable fields:

```python
from general_manager.search.config import IndexConfig

class Project(GeneralManager):
    class Interface(DatabaseInterface):
        name = CharField(max_length=200)
        status = CharField(max_length=50)

    class SearchConfig:
        indexes = [
            IndexConfig(
                name="global",
                fields=["name", "status"],
                filters=["status"],
                sorts=["name"],
            )
        ]
```

## Step 3: Create index settings and reindex data

```bash
python manage.py search_index
python manage.py search_index --reindex
python manage.py search_index --index global --reindex
python manage.py search_index --manager Project --reindex
```

Without `--index`, the command ensures every registered search index. Unknown
index names are written to stderr and ignored; if none of the requested names are
valid, the command exits without reindexing. `--manager` filters reindexing by
manager class name and is only used with `--reindex`. Backend configuration,
index setup, manager discovery, and reindexing errors propagate so CI or deploy
scripts fail visibly.

If you add or remove fields, filters, or sorts later, re-run with `--reindex`.

## Step 4: Query via GraphQL

Example query:

```graphql
query SearchProjects($filters: JSONString) {
  search(index: "global", query: "alpha", filters: $filters, sortBy: "name") {
    total
    totalIsExact
    results {
      __typename
      ... on ProjectType { id name status }
    }
  }
}
```

Variables:

```json
{
  "filters": "{\"status\": \"public\"}"
}
```

Filters can also be passed as a list of filter items:

```json
{
  "filters": "[{\"field\": \"status\", \"op\": \"in\", \"values\": [\"public\", \"draft\"]}]"
}
```

By default, GraphQL search returns exact post-permission totals. Exact totals
preserve existing behavior, but permission-filtered searches may need to scan
additional backend pages after the current result page is already filled.

To cap those scans per manager, enable bounded totals and choose a positive scan
limit:

```python
GENERAL_MANAGER = {
    **GENERAL_MANAGER,
    "GRAPHQL_SEARCH_TOTAL_MODE": "bounded",
    "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 1000,
}
```

Bounded mode caps backend hit scans per manager. The effective cap is at least
the requested page end (`page` / `pageSize`) so the current page can still be
filled where possible. In bounded mode, `total` is the authorized count found
before the cap and `totalIsExact` is `false` if the resolver hit the cap before
observing an empty or partial backend page.

Clients can override the setting per request:

```graphql
query {
  search(
    index: "global",
    query: "alpha",
    pageSize: 20,
    totalMode: "bounded"
  ) {
    total
    totalIsExact
    results {
      __typename
      ... on ProjectType { id name status }
    }
  }
}
```

Use `totalMode: "exact"` when a specific request needs the previous exact
total behavior even if the deployment default is bounded. Invalid `totalMode`
values are returned as GraphQL user-input errors.

## Step 5: Async indexing (optional)

Enable async updates via Celery:

```python
GENERAL_MANAGER = {
    **GENERAL_MANAGER,
    "SEARCH_ASYNC": True,
}
```

Run a Celery worker so index updates can be dispatched. When async is disabled,
updates run inline.

## Step 6: Keep indexes initialized and reconciled

For production and development reconciliation setup, see
[Run search reconciliation](search_reconciliation.md).

## Step 7: Non-GraphQL usage

You can call the backend directly in Python:

```python
from general_manager.search.backend_registry import get_search_backend

backend = get_search_backend()
result = backend.search("global", "alpha", filters={"status": "public"})
```

To reindex from code:

```python
from general_manager.search.indexer import SearchIndexer

SearchIndexer().reindex_manager(Project)
```

`SearchIndexer(backend=None)` uses `get_search_backend()` when no backend is
provided, with the same backend-registry reuse semantics as
`get_search_backend()`. The backend must implement the `SearchBackend` protocol.
`index_instance(instance)` writes one document per configured `IndexConfig` for
the instance's manager; `delete_instance(instance)` deletes the same document id
from every configured index. Both methods return without action when the manager
has no search configuration, process indexes in configured order, and are not
atomic across indexes.

`reindex_manager(Project)` ensures all configured indexes, iterates
`Project.all()`, and upserts grouped documents once per index that has current
documents. It does not delete stale backend documents. Use
`reindex_manager_index(Project, "global")` when a reconciler or maintenance job
needs stale cleanup for one manager/index pair. That method returns `0` without
action when the manager has no search configuration, raises
`MissingIndexConfigurationError` when the manager is search-enabled but the
requested index is unknown, upserts current documents first, then deletes stale
ids returned by `backend.list_document_ids(..., types=[type_label])`.
Duplicate `IndexConfig.name` entries are not a recommended configuration:
single-instance indexing repeats work for duplicates, full manager reindexing
collapses duplicates into one backend upsert per index name while still
serializing one document per duplicate config, and single-index reindexing uses
the first matching index config.

Backend configuration, manager iteration, custom document id/mapping, field
extraction, and backend write/list/delete errors propagate to the caller.

## Step 8: Production checklist

- Pin your Meilisearch version and set `MEILISEARCH_API_KEY`.
- Reindex after search schema changes.
- Confirm filterable/sortable fields are configured.
- Monitor indexing failures in logs and alert on task errors.
