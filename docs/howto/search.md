# Search Tutorial

This tutorial walks through enabling search with Meilisearch, indexing data, and
querying via GraphQL. It assumes Django + the auto GraphQL schema are enabled.

## Reindex existing identity projections

After upgrading to identity-aware search transport, rebuild every affected
manager/index pair before accepting identity-filtered traffic. Existing
Meilisearch documents lack the new equality tokens, so declared identity filters
omit those rows until reindexing finishes. A partly rebuilt index returns only
rebuilt matching rows; it never broadens back to numeric comparison. Plan a
maintenance window, or build a separate application-owned index and switch
traffic after verifying it.

For one configured pair, use the supported indexer in the application process:

```python
from general_manager.search.indexer import SearchIndexer

SearchIndexer().reindex_manager_index(Project, "global")
```

This rebuilds current rows and removes stale documents belonging to that manager
type, preserving other types in the index. `python manage.py search_index
--reindex --manager Project` rebuilds all configured indexes for the selected
manager. Its `--index` option selects settings updates; it does not limit that
manager's reindex scope. Settings-only `search_index` calls preserve identity
filterability. Reconciliation fingerprints also track resolved identity types
and transport version, so enabled reconciliation marks affected pairs for
rebuilding. Verify exact equality/membership and authorized totals after repair.
No database migration or changes to native constructors are required.

When using a custom `to_document`, declare aliased native identity fields on
the index instead of stringifying all numbers:

```python
IndexConfig(
    name="global",
    fields=["customer_alias", "quantity"],
    filters=["customer_alias", "quantity"],
    identity_fields={"customer_alias": int},
)
```

The declaration describes the native identity kind, including scalar elements
of nullable/list fields. Unmarked `quantity` stays a business number. For direct
provider queries use `build_search_filters(Project, "global", filters)`; manual
documents and custom providers can use the explicit document/filter metadata
contract described in [the API reference](../api/search.md).

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

### Use lazy DevSearch hydration in development

For a local development server, replace the Meilisearch configuration above
with this complete disposable DevSearch configuration:

```python
GENERAL_MANAGER = {
    "SEARCH_BACKEND": {
        "class": "general_manager.search.backends.dev.DevSearchBackend",
        "options": {},
    },
    "SEARCH_AUTO_REINDEX": True,
}
```

When the selected backend is `DevSearchBackend` and
`SEARCH_AUTO_REINDEX=True`, the first search for each index in that serving
process lazily rebuilds that index from configured managers. If that rebuild
fails, the search raises the source error and the next search retries it.
Subsequent synchronous manager lifecycle updates in the same process upsert or
delete the relevant documents normally.

DevSearch is process-local. Running `python manage.py search_index --reindex`
in another process cannot fill the in-memory backend of an already running
server; restart that server so its next search hydrates its own projection.
Directly constructed `DevSearchBackend` instances stay inert until constructed
with `auto_reindex=True` or enabled later with
`backend.configure_auto_reindex(True)`.

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

### Invalidate fields owned by related managers

When a project document contains customer or investment data, put the rule on
`Project`, the manager that owns the document:

```python
from general_manager import GeneralManager, IndexConfig, SearchInvalidationRule
from investments.managers import InvestNumber


def affected_projects(change, owner_class):
    return owner_class.filter(customer=change.instance)


class Project(GeneralManager):
    class SearchConfig:
        indexes = (
            IndexConfig(name="global", fields=("customer__name",)),
        )
        invalidation_rules = (
            SearchInvalidationRule(
                source="customers.managers.Customer",
                resolve=affected_projects,
                indexes=("global",),
            ),
            SearchInvalidationRule(
                source=InvestNumber,
                resolve=lambda change, owner: owner.filter(
                    invest_numbers=change.instance
                ),
                relation="invest_numbers",
                indexes=("global",),
            ),
        )
```

The resolver must return `Project` manager instances. Use a dotted source path
to avoid import cycles when useful. Omit `indexes` to invalidate every project
index. `resolve=None` does not discover individual projects; it marks the
selected project/index pairs dirty for reconciliation.

The optional `relation` enables exact M2M invalidation for related-manager
operations. Both auto-created and custom through models work with forward and
reverse `add()`, `remove()`, `clear()`, and `set()`. The owner must have exactly
the standard `id` input, and each through foreign key must target its endpoint
primary key. Django checks report violations as
`general_manager.search.E007` through `E009`. Self-symmetrical relations,
direct through-table writes, raw SQL, and bulk operations are not observed. Run
`search_reconcile --once` after any such write.

Bound each event and its task payloads in settings:

```python
GENERAL_MANAGER = {
    **GENERAL_MANAGER,
    "SEARCH_INVALIDATION_MAX_TARGETS": 1000,
    "SEARCH_INVALIDATION_BATCH_SIZE": 100,
}
```

Both settings must be positive integers. Resolver errors, invalid result types,
or overflow discard that rule's targeted work and leave the exact pairs dirty;
partial resolver output is never dispatched.

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
Meilisearch document IDs use a hash-based physical key. If upgrading from an
earlier version that preserved safe IDs, perform a fresh-index cutover before
reindexing. Reconciliation compares logical document IDs and cannot distinguish
old and new physical records that retain the same original ID, so ordinary
upserts, `reindex_manager()`, and `reindex_manager_index()` cannot clean them
up. Old and new physical records must not coexist.
For DevSearch, run this command in the same process only when manually managing
that process's in-memory projection; a separate command process cannot populate
a running server's DevSearch backend.

## Step 4: Query via GraphQL

Example query:

```graphql
query SearchProjects($filters: JSONString) {
  search(index: "global", query: "alpha", filters: $filters, orderBy: [{field: name}]) {
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

For a configured identity filter such as the real `leader_id` foreign-key
alias, send the GraphQL ID string inside the JSON-encoded filter:

```json
{"filters": "{\"leader_id__in\": [\"9007199254740993\"]}"}
```

Default equality and `__exact` work the same way. UUID references convert to
native UUIDs; string references such as `"007"` keep their exact spelling. For a
native scalar or collection projection, declare its identity contract with
`@graph_ql_property(is_identifier=True)` and add its name to `IndexConfig.filters`.
This search declaration does not require a generated ORM `filterable` property
or `query_annotation`. Keep indexed values native and retain business numbers
as JSON numbers. ID-like names and annotations alone do not opt into conversion.

No database or index migration is required when upgrading this normalization.
If the model PK is named `id`, use an explicitly declared property (for example,
`record_key`) for search filters because `id` is reserved by the search document
envelope. A custom `to_document` must preserve the native type promised by the
manager's identity metadata.

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
- Run reconciliation periodically; it repairs dirty fallbacks and accepted task
  failures.
- Apply migration `0011_search_index_state_dirty_generation` before enabling
  lifecycle invalidation workers.
