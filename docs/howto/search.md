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
  getmeili/meilisearch:v1.30.0
```

You can use other backends by swapping the backend class and options; ensure
the backend is implemented and available in your environment.

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
```

If you add or remove fields, filters, or sorts later, re-run with `--reindex`.

## Step 4: Query via GraphQL

Example query:

```graphql
query SearchProjects($filters: JSONString) {
  search(index: "global", query: "alpha", filters: $filters, sortBy: "name") {
    total
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

## Step 6: Non-GraphQL usage

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

## Step 7: Production checklist

- Pin your Meilisearch version and set `MEILISEARCH_API_KEY`.
- Reindex after search schema changes.
- Confirm filterable/sortable fields are configured.
- Monitor indexing failures in logs and alert on task errors.
