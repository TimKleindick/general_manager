# Search configuration

GeneralManager ships configuration primitives and an optional development backend.
Production deployments are expected to use an external search service.

## IndexConfig + FieldConfig

Use `IndexConfig` entries to describe which fields should be indexed and how
they should be filtered. Fields can optionally include per-field boosts.

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
            ),
            IndexConfig(
                name="project_selection",
                fields=["name"],
                filters=["status"],
                boost=1.5,
            ),
        ]
```

## Identifiers

Search documents always include the manager `identification` mapping. The
default document ID is derived from that identification plus the manager type,
so IDs remain stable across database and non-database interfaces.

## Optional extras

These helpers are optional and only required if your adapter needs them.

- `document_id`: Callable used to produce a stable document identifier.
- `type_label`: Explicit label for multi-manager search unions.
- `to_document`: Callable that serializes a manager instance into a document.
- `update_strategy`: String used by your adapter to decide sync vs async updates.

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
indexing.

## DevSearch backend (service-free)

For local development, the built-in DevSearch backend stores documents in
memory and supports basic term matching with per-field boosts. It does **not**
provide typo tolerance and should not be used in production.

To opt into another backend, configure `GENERAL_MANAGER["SEARCH_BACKEND"]` or
`SEARCH_BACKEND` in Django settings to point at a backend class or factory.

Your search adapter can resolve the configuration via
`general_manager.search.config.resolve_search_config()` and apply it to the
backend of your choice.

## GraphQL search query

When GraphQL is auto-created, a global `search` query is added. It accepts an
`index` name, a `query` string, optional `types`, optional `filters`, and
optional `sort_by`/`sort_desc`, and returns a mixed list of managers via a
GraphQL union.

Search filters can be supplied as JSON (`filters`) or as typed filter items
(`filters` as a list). Typed items use `{field, op, value}` or
`{field, values}` with `op="in"` for list matching.

## Index lifecycle

Use the management command to create/update index settings and reindex data:

```bash
python manage.py search_index
python manage.py search_index --reindex
python manage.py search_index --index global --reindex
python manage.py search_index --manager Project --reindex
```

## Async indexing

Set `GENERAL_MANAGER["SEARCH_ASYNC"] = True` (or `SEARCH_ASYNC = True`) to
dispatch index updates through Celery. When disabled, updates run inline.

Celery is required for production async indexing; development can remain sync.

## Meilisearch test recipe

To run the optional Meilisearch integration test locally or in CI, start a
Meilisearch container and provide the URL via `MEILISEARCH_URL` (and
optionally `MEILISEARCH_API_KEY`):

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

## Meilisearch setup (local or production)

Use the Meilisearch backend by configuring the search backend and connection
settings. The backend reads `MEILISEARCH_URL` and optional
`MEILISEARCH_API_KEY`.

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
