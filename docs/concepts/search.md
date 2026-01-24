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
        indexes = [IndexConfig(name="global", fields=["name"]) ]
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
`index` name, a `query` string, optional `types`, and optional `filters`, and
returns a mixed list of managers via a GraphQL union.
