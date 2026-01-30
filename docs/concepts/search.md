# Search

GeneralManager ships configuration primitives and a development backend for search.
Production deployments are expected to use an external search service.

## Overview

Search is configured per manager and aggregated per index name. Each manager can
contribute documents to one or more indexes. Index settings (searchable fields,
filterable fields, sortable fields, and field boosts) are derived from all
managers that declare the same index name.

Search documents include:
- A stable, type-scoped document id.
- A `type` label and `identification` mapping for reconstruction.
- A `data` payload built from configured fields (and filter fields).

## Search configuration model

### IndexConfig and FieldConfig

Use `IndexConfig` entries to describe which fields should be indexed and how
they should be filtered and sorted. Fields can optionally include per-field
boosts. Sortable fields must be declared on the index to be sortable.

```python
from general_manager import FieldConfig, IndexConfig

class Project(GeneralManager):
    class SearchConfig:
        indexes = [
            IndexConfig(
                name="global",
                fields=[
                    "name",
                    FieldConfig(name="leader__name", boost=2.0),
                ],
                filters=["status", "leader_id"],
                sorts=["name", "status"],
                boost=1.2,
            )
        ]
```

Field and index rules:
- `fields`: searchable fields for full-text matching.
- `filters`: filterable fields allowed in `filters` (plus the built-in `type`).
- `sorts`: sortable fields allowed for `sort_by` / `sortBy`.
- `FieldConfig.boost`: per-field boost (must be > 0).
- `IndexConfig.boost`: per-index boost (must be > 0; used by DevSearch).
- `IndexConfig.min_score`: reserved for backend-specific use (not applied by
  built-in backends today).

### Optional extras

These helpers are optional and only required if your adapter needs them.

- `document_id`: Callable used to produce a stable document identifier.
- `type_label`: Explicit label for multi-manager search unions.
- `to_document`: Callable that serializes a manager instance into a document.
- `update_strategy`: Adapter-specific hook for sync vs async (not used by built-in
  backends today).

```python
class Project(GeneralManager):
    class SearchConfig:
        indexes = [IndexConfig(name="global", fields=["name"])]
        type_label = "Project"

        @staticmethod
        def document_id(instance: "Project") -> str:
            return f"Project:{instance.id}"

        @staticmethod
        def to_document(instance: "Project") -> dict:
            return {
                "name": instance.name,
                "status": instance.status,
            }
```

`to_document` should only return keys configured on the `IndexConfig` used for
indexing. Filter fields listed in `filters` are included automatically if not
present in the returned mapping.

## Document identity and permissions

Search documents always include the manager `identification` mapping. The
default document id is derived from that identification plus the manager type,
so ids remain stable across database and non-database interfaces. If you override
`type_label`, keep it stable; it is part of the id and is used to segment
results by manager type.

GraphQL search applies `get_read_permission_filter()` to the search query and
then re-checks permissions on instantiated results. User filters are merged with
permission filters and may expand into OR groups when multiple permission
filters are present.

## GraphQL search API

When GraphQL is auto-created, a global `search` query is added. It accepts:
- `query`: the full-text query string.
- `index`: index name (defaults to `global`).
- `types`: optional list of manager class names to restrict results.
- `filters`: JSON string or list of filter items.
- `sortBy` / `sortDesc`: optional sort field and direction.
- `page` / `pageSize`: pagination controls.

Results are returned as a union of manager GraphQL types:
- `results`: list of manager instances.
- `total`: total number of matching hits.
- `took_ms`: backend search time in milliseconds (if reported).
- `raw`: backend-specific raw response payload(s).

Note: GraphQL currently keys `types` off manager class names. If you override
`type_label`, keep it aligned with the class name when using `types` filters.

Example query:

```graphql
query SearchProjects($filters: JSONString) {
  search(index: "global", query: "alpha", filters: $filters, sortBy: "name") {
    total
    results {
      __typename
      ... on ProjectType { id name status }
      ... on ProjectTeamType { id name status }
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

### Filters and operators

Filters can be provided as:
- A JSON object: `{"status": "public"}`.
- A JSON list of filter items: `[{"field": "status", "value": "public"}]`.

List items support `field`, optional `op`, and either `value` or `values`. If
`values` is provided and `op` is omitted, `op` defaults to `in`.

Supported lookup operators in the filter parser:
- `exact` (default)
- `lt`, `lte`, `gt`, `gte`
- `contains`, `startswith`, `endswith`
- `in`

Example list format (OR groups are created from list entries):

```json
[
  {"field": "status", "value": "public"},
  {"field": "status", "op": "in", "values": ["draft", "archived"]}
]
```

### Backend support by operator

- **DevSearch** supports all operators listed above.
- **Meilisearch** translates filters to equality and `in` only. Other operators
  are treated as equality checks. For advanced expressions, call the backend
  directly with `filter_expression` (Python usage only).

## Index lifecycle

Use the management command to create/update index settings and reindex data:

```bash
python manage.py search_index
python manage.py search_index --reindex
python manage.py search_index --index global --reindex
python manage.py search_index --manager Project --reindex
```

Use `--reindex` after schema changes (field list, filters, or sort fields).

## Async indexing

Set `GENERAL_MANAGER["SEARCH_ASYNC"] = True` (or `SEARCH_ASYNC = True`) to
dispatch index updates through Celery. When disabled, updates run inline.

Celery is required for production async indexing; development can remain sync.

## Development auto-reindex (optional)

To avoid manual reindexing when using the in-memory dev backend, enable:

```python
GENERAL_MANAGER = {
    "SEARCH_AUTO_REINDEX": True,
}
```

When enabled (and `DEBUG=True`), GeneralManager reindexes once on the first
request in the runserver process. This keeps dev search results available
without running `search_index --reindex` manually.

## Backends

### DevSearch backend (service-free)

For local development, the built-in DevSearch backend stores documents in
memory and supports basic term matching with per-field boosts. It does **not**
provide typo tolerance and should not be used in production.

### External backends

To opt into another backend, configure `GENERAL_MANAGER["SEARCH_BACKEND"]` or
`SEARCH_BACKEND` in Django settings to point at a backend class or factory.

Your adapter can resolve the configuration via
`general_manager.search.config.resolve_search_config()` and apply it to the
backend of your choice.

Meilisearch is the primary production adapter today. Typesense and OpenSearch
adapters are present as stubs and will raise `SearchBackendNotImplementedError`.

## Non-GraphQL usage

If you do not use the auto GraphQL schema, call the backend directly:

```python
from general_manager.search.backend_registry import get_search_backend

backend = get_search_backend()
result = backend.search("global", "alpha", filters={"status": "public"})
```

To (re)index directly in Python:

```python
from general_manager.search.indexer import SearchIndexer

SearchIndexer().reindex_manager(Project)
```

## Operations and troubleshooting

- **Missing filters**: filter fields must be listed in `IndexConfig.filters`.
- **Sorting fails**: sort fields must be listed in `IndexConfig.sorts` and be
  marked sortable by the backend.
- **No results**: verify the index was created (`search_index`) and reindexed
  after config changes.
- **Permission gaps**: ensure `get_read_permission_filter()` returns correct
  rules and that your GraphQL context is populated.
- **Meilisearch auth errors**: confirm API key and URL are in sync with the
  configured backend settings.

## Meilisearch setup (local or production)

Use the Meilisearch backend by configuring the search backend and connection
settings. The backend reads `MEILISEARCH_URL` and optional `MEILISEARCH_API_KEY`.

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

Local Docker example (dev keyless):

```bash
docker run --rm -p 7700:7700 --name meilisearch \
  -e MEILI_NO_ANALYTICS=true \
  getmeili/meilisearch:v1.30.0
```

Production notes:
- Set `MEILISEARCH_API_KEY` (or a master key) and pass the same value in your
  deployment environment.
- Ensure your index settings are created with `python manage.py search_index`
  and reindex with `--reindex` after schema changes.
- Keep the Meilisearch image pinned to a known-good version to avoid drift.

## Meilisearch test recipe

To run the optional Meilisearch integration test locally or in CI, start a
Meilisearch container and provide the URL via `MEILISEARCH_URL` (and optionally
`MEILISEARCH_API_KEY`):

```bash
docker run --rm -p 7700:7700 --name meilisearch-test \
  -e MEILI_NO_ANALYTICS=true \
  getmeili/meilisearch:v1.30.0
```

Then run the test with:

```bash
MEILISEARCH_URL=http://127.0.0.1:7700 python -m pytest \
  tests/integration/test_meilisearch_search.py
```
