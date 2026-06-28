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
filters are present. In each group, permission `"filter"` keys override matching
user filter keys before the backend search runs. Permission `"exclude"` mappings
are not sent to the backend prefilter; they are enforced during the later
per-instance authorization pass. Empty permission constraints such as `{}`,
`{"filter": {}}`, or `{"exclude": {}}` create an unrestricted backend group
relative to the user filters, with any exclude-only restriction still handled by
the instance check. When a manager has no permission filter alternatives, the
user filter mapping is passed through unchanged.

## GraphQL search API

When GraphQL is auto-created, a global `search` query is added. It accepts:
- `query`: the full-text query string.
- `index`: index name (defaults to `global`).
- `types`: optional list of manager class names to restrict results.
- `filters`: JSON string or list of filter items.
- `sortBy` / `sortDesc`: optional sort field and direction.
- `page` / `pageSize`: pagination controls.

Results are returned as a union of manager GraphQL types:
- `results`: nullable GraphQL list field containing the authorized manager instances as the generated union type.
- `total`: nullable integer field containing the post-permission authorized hit count, not the backend raw total.
- `took_ms`: nullable integer field containing accumulated backend search time in milliseconds when reported.
- `raw`: nullable JSON string field containing a list of backend-specific raw response payloads.

Omitted `page` defaults to `1` and omitted `pageSize` defaults to `10`.
Explicit `0` or negative pagination values are rejected as GraphQL
`BAD_USER_INPUT` errors. The resolver searches each selected manager type,
instantiates managers from hit identification, then applies read permission
filters and any required per-instance read checks before counting and returning
results. Manager search order follows GraphQL's manager registry order filtered
to managers with search config. When `types` is supplied, unknown manager class
names are ignored. Malformed hit identification that raises `TypeError`,
`ValueError`, or `KeyError` while constructing the manager is skipped. Invalid
configured filter keys are reported as GraphQL errors. Backend lookup/search
errors, other manager construction errors, permission errors, and sort
comparison errors propagate. `sortBy` is not validated ahead of time; sorting
reads each hit's `data[sortBy]` when present and otherwise sorts that hit as
`null`/last before applying `sortDesc`.

Note: GraphQL currently keys `types` off manager class names. If you override
`type_label`, keep it aligned with the class name when using `types` filters.
Generated search unions also look up GraphQL object types by manager class name
in the GraphQL type registry.

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
- A malformed JSON string or decoded non-object value, which normalizes to an
  empty filter set.
- A Python/object mapping supplied directly by internal resolver calls; that
  mapping is returned unchanged rather than copied.

List items support `field`, optional `op`, and either `value` or `values`. If
`values` is provided, it takes precedence over `value`; if `op` is also omitted,
`op` defaults to `in`. Empty `values` is preserved as the filter value. The
lookup key is `field` when `op` is blank and `field__op` otherwise; parsing does
not validate operator names. Malformed list entries are skipped.

Nested relation filters use the generated GraphQL relation input shape. Direct
relations are metadata entries with `relation_kind="direct"` and flatten to the
relation's `filter_lookup` prefix. Collection relations use
`relation_kind="collection"` and support `any` and `none`: `any` becomes a
positive backend filter and `none` becomes an exclude. Nested excludes from
`none` are inverted back into positive filters. Relation filter generation is
limited by the configured relation depth; depth zero stops nested relation input
creation.
Equality-style `id`, `id__exact`, and list/tuple-shaped `id__in` values are cast
through the target manager's `id` input field when available.
Only Python list and tuple values are element-cast for `id__in`; other iterable
shapes pass through unchanged.

Search permission constraints use the same shape as other GraphQL read
prefilters: an ordered list of entries containing optional `filter` and
`exclude` mappings. The search backend receives the user filters merged with
each entry's `filter` mapping, with permission keys overriding user keys in that
alternative. The paired `exclude` mapping is evaluated after the hit is
instantiated. Empty entries (`{}`, `{"filter": {}}`, or `{"exclude": {}}`) mean
that one alternative is unrestricted relative to the user filters. If the plan
still requires instance checks, `can_read_instance()` must pass for that hit.

Filter-key validation runs against the parsed top-level search filters before
permission filters or relation normalizers are applied. Exclude-derived
permission keys are not validated by the GraphQL search helper.

Supported lookup operators in the filter parser:
- `exact` (default)
- `lt`, `lte`, `gt`, `gte`
- `contains`, `startswith`, `endswith`
- `in`

Filter evaluation traverses attributes with `getattr` only; missing attributes
evaluate to `False`. String containment, starts-with, and ends-with checks are
case-sensitive. Incompatible comparisons return `False`; exact comparisons
compare `None` like any other value.

Generated GraphQL filter input fields follow the attribute category:

- `id`: `id`, `id__exact`, `id__in`, plus `id__gt`, `id__gte`, `id__lt`, and `id__lte`.
- `Measurement`: the base field plus `__gt`, `__gte`, `__lt`, and `__lte`, all using the measurement scalar.
- Numbers, dates, and datetimes: the base field plus `__gt`, `__gte`, `__lt`, and `__lte`.
- Strings: the base field plus `__exact`, `__icontains`, `__contains`, `__in`, `__startswith`, and `__endswith`; `__in` is a list of the mapped base scalar and honors a string `graphql_scalar` metadata override.
- Manager relations: direct or collection relation inputs are generated through the relation filter rules above, subject to relation depth.

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

Without `--index`, every registered index is ensured. Unknown index names are
reported to stderr and ignored; if none are valid, the command exits before
reindexing. `--manager` filters reindexing by manager class name and is only
used when `--reindex` is set. Backend configuration, index setup, manager
discovery, and reindexing errors propagate to the command caller.

Use `--reindex` after schema changes (field list, filters, or sort fields).

## Async indexing

Set `GENERAL_MANAGER["SEARCH_ASYNC"] = True` (or `SEARCH_ASYNC = True`) to
dispatch index updates through Celery. When disabled, updates run inline.
The nested `GENERAL_MANAGER` value takes precedence over the top-level setting,
and missing settings default to inline updates.

Celery is required for production async indexing; development can remain sync.
`dispatch_index_update(action=...)` accepts only `"index"` and `"delete"`.
When async indexing is enabled and Celery is available, the Celery task is
queued and a provided in-memory instance is ignored. Otherwise a provided
instance runs inline against the current backend; without an instance, the task
function runs synchronously and reconstructs the manager from the dotted
`manager_path` and `identification` keyword mapping. Import, construction,
backend, task enqueue, and indexer errors propagate to the caller. Celery task
payloads must use identification values supported by the deployment's Celery
serializer.

## Search reconciliation

GeneralManager keeps search fresh in two layers:

1. Manager create/update/delete operations update the affected search document.
2. The search reconciler periodically checks durable index state and rebuilds
   manager/index pairs that need initialization, schema updates, or recovery
   after missed writes.

The reconciler stores one state row per searchable manager/index pair. Missing
state means the pair has not been initialized. A changed schema fingerprint
means the configured fields, filters, sorts, boosts, type label, document id, or
document serializer, or update strategy changed. `SearchIndexState.mark_dirty()`
uses `timezone.now()` for a first dirty timestamp, preserves that timestamp on
later marks, and overwrites the reason. The reconciliation planner updates
`schema_fingerprint`; claim acquisition and expiration are handled by reconciler
helpers. `clear_dirty()` uses `timezone.now()`, marks the pair initialized on
first success, records the latest reconciliation time, clears dirty/error
fields, and releases any claim token.

Production deployments should enable
`GENERAL_MANAGER["SEARCH_RECONCILE_ENABLED"] = True` and run Celery Beat.
Development can either run the same Celery Beat path or use
`python manage.py search_reconcile --watch`.

Search initialization is not coupled to GraphQL traffic or WSGI/ASGI request
handling.

## Backends

### Backend contract and result models

`SearchBackend` is the extension point for adapters. Backends receive index
names, queries, filters, sorting, and pagination options, then return a
`SearchResult`. Adapters must also expose stored document IDs through
`list_document_ids()` so reconciliation can remove stale documents after a
manager/index reindex.

Search result models have stable responsibilities:

- `SearchDocument` represents the indexed payload, including the type label, identification mapping, and data fields.
- `SearchHit` denotes one matched document reference with its type label, identification mapping, optional score, index name, and returned data fields.
- `SearchResult` contains the paginated response with hits, totals, timing, and raw backend payloads.

Document identifiers are strings. Identification mappings and document data are
object-valued so adapters can carry normal manager IDs, enums, datetimes, lists,
and other serializer-supported values without using untyped payloads. Backend
settings and structured filters are also object-valued mappings. Concrete
adapters define which setting keys, filter operators, raw response shapes, and
unsupported-feature errors they accept; operational failures should use
`SearchBackendError` or a subclass when the adapter can normalize them.
Document ID uniqueness is scoped to one backend index unless an adapter
documents broader constraints. Indexer-managed `SearchDocument.index` values
match the `index_name` used for backend writes, but the protocol does not
require adapters to reject mismatches. Payload mappings are not copied by the
result models; callers should treat `identification`, `data`, boost mappings,
and `raw` backend responses as immutable/read-only after passing them across
the backend boundary. The dataclass field annotations are the validation
boundary for `SearchDocument`; the model does not coerce or validate runtime
values. `frozen=True` prevents attribute reassignment but does not make nested
mappings immutable or guarantee practical hashability when fields contain
unhashable values. Structured filters portably target `SearchDocument.data`;
`types` handles manager labels separately, and nested filter syntax is adapter-specific.
`SearchResult.hits` is the returned page after `limit` and `offset`; `total` is
the number of matches before pagination when the backend can report it, and
otherwise the adapter defines the best-effort value.
`SearchHit.data` is optional at the protocol level because some services omit
stored fields by default. When returned `SearchHit.data` or `SearchResult.raw`
references adapter-owned objects, callers should treat them as read-only. The
portable protocol is intentionally narrow: method shapes, document identity by
index/name/id, object-valued payloads, basic structured filter transport, type
restrictions, one-field sorting, paginated results, and the broad error boundary
above. All protocol methods are synchronous. `ensure_index()` requires a
settings mapping; use `{}` for no settings because `None` is not part of the
protocol. Adapter-specific behavior is part of each adapter's public contract, not
an omission from `SearchBackend`. That includes exact settings support,
grouped-filter semantics, `filter_expression` precedence, non-DevSearch
lookup/sort grammar beyond top-level data fields, negative `limit`/`offset`
handling, duplicate IDs inside one batch, batch atomicity, runtime validation,
settings merge-or-replace behavior, concurrency guarantees, concrete exception
classes, stable ordering for equal scores, and value serialization beyond
serializer-compatible object values. Adapters may validate inputs more strictly
than the dataclasses do. `filter_expression` unsupported-feature errors are
adapter-specific; DevSearch uses `NotImplementedError`. `list_document_ids()`
returns the same GeneralManager document ID strings passed as
`SearchDocument.id`, for example `"Project:{'id': 1}"`.

Backends may retain references to input document payloads unless their adapter
documents copy-on-write behavior, so callers should not mutate a
`SearchDocument` after handing it to `upsert()`. Unknown type labels, unknown
indexes, malformed filters, invalid sort fields, and invalid pagination on
non-DevSearch adapters are adapter-specific. `SearchResult.raw` is an optional
diagnostic escape hatch with no cross-adapter stability guarantee. Values in
`SearchDocument.identification` are object-shaped at this protocol layer; choose
serializer-compatible values when the selected backend, Celery, GraphQL, or
another integration serializes them. Adapters should normalize operational
failures to `SearchBackendError` only when doing so does not hide useful
backend-native context.

The backend registry functions `configure_search_backend`, `configure_search_backend_from_settings`, and `get_search_backend` centralize adapter selection and lookup for GraphQL, indexing tasks, and direct Python usage. `configure_search_backend()` sets or clears the process-local backend instance. `get_search_backend()` reuses that instance when present; otherwise it reads Django settings and, when settings leave search unset, installs one process-local `DevSearchBackend` fallback instance that later calls reuse.

### DevSearch backend (service-free)

For local development, the built-in DevSearch backend stores documents in
memory and supports basic term matching with per-field boosts. It does **not**
provide typo tolerance and should not be used in production.
Structured filters use AND within one mapping and OR across a sequence of
mappings. For `exact` and `in` checks against list/tuple/set document fields,
collection filter values match on intersection; scalar `in` filter values do
not match collection-valued document fields.
DevSearch writes documents into the `index_name` argument, stores
`SearchDocument.index` without validating it, and scopes document IDs to that
one in-memory index. Duplicate IDs inside one `upsert()` call are processed in
order, so the last document wins. Deletes are best-effort and duplicate IDs are
harmless. `ensure_index()` is idempotent and replaces the stored settings
mapping for the index. Operations are not transactional; mutations completed
before an exception remain in memory. Query matching lowercases and splits
strings on whitespace. It does not stem, parse phrases, or perform fuzzy matching. Indexed
tokens are built from every top-level `SearchDocument.data` value: `None`
produces no tokens, strings split on whitespace, lists/tuples/sets are processed
recursively, and all other values become `str(value).lower().split()`. Dict
values are not traversed and are tokenized from their string representation. A
token matches when it equals or prefixes an indexed field token. Empty queries
match every document that passes type and structured filters. The operation
order is type filtering, structured filtering, query scoring/matching, sorting,
and pagination. Structured filter keys target `SearchDocument.data` field names
and may use one lookup suffix separated by `__` as in `field__in`: `exact`, `lt`, `lte`, `gt`,
`gte`, `contains`, `startswith`, `endswith`, or `in`; keys without a suffix use
`exact`, and nested field traversal is not supported. DevSearch filter
comparisons use the shared `apply_lookup()` helper: string operations are
case-sensitive, missing fields behave like `None`, incompatible mixed-type
comparisons and invalid lookup/value combinations return `False`, and `None`
compares only through `exact`.

Scores sum matching field boosts and then multiply by `index_boost` when set.
Results sort by score descending unless `sort_by` names one raw
`SearchDocument.data` field. For field sorting, `None` and missing fields are
treated as missing and kept last for ascending and descending sorts. Booleans
follow Python numeric ordering because `bool` is an `int` subclass; other
numbers sort numerically, and every non-numeric, non-missing value sorts by
`str(value)`. Python's stable sort preserves insertion order for otherwise
equal keys. DevSearch includes stored data in returned hits, reports `total`
before pagination, slices as `results[offset:offset + limit]` with negative
values intentionally following Python slice behavior, stores document and
settings objects by reference, returns hit data from the stored
`SearchDocument.data` mapping, and keeps state only in the current process with
no persistence or synchronization for concurrent reads/writes. Mutating returned
DevSearch hit data can mutate the stored document payload.
DevSearch raises `NotImplementedError` for `filter_expression`; other
operational failures are not normalized and may surface as ordinary Python
exceptions.

### External backends

To opt into another backend, configure `GENERAL_MANAGER["SEARCH_BACKEND"]` or
`SEARCH_BACKEND` in Django settings. The nested `GENERAL_MANAGER` value takes
precedence over the top-level setting, including explicit `None` to clear the
configured backend.

Accepted `SEARCH_BACKEND` values are:

- a `SearchBackend` instance
- a dotted import path to a backend instance, class, or zero-argument factory
- a backend class or zero-argument factory callable
- a mapping with `class` and optional `options`, where options are passed as keyword arguments
- `None`, or a missing setting, to use the `DevSearchBackend` fallback from `get_search_backend()`

Import errors, factory errors, and constructor errors propagate. A mapping
`options` value must be a mapping. A non-`None` setting that resolves to a
non-backend object raises `SearchBackendNotConfiguredError`.

Your adapter can resolve the configuration via
`general_manager.search.config.resolve_search_config()` and apply it to the
backend of your choice.

Meilisearch is the primary production adapter today. Typesense and OpenSearch
adapters are present as stubs for configuration compatibility. Constructing
`TypesenseBackend` or `OpenSearchBackend`, or calling any backend method on an
instance created for testing, raises `SearchBackendNotImplementedError`.
Constructor arguments, index settings, and search filters are accepted only to
match the `SearchBackend` protocol and are not inspected by the stubs.

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

The indexer can also write or delete one instance with
`index_instance(instance)` and `delete_instance(instance)`. The indexer discovers
configuration from the manager class search registry and writes one document per
configured `IndexConfig`. The default document id is the same for indexing and
deletion: the manager type label plus `instance.identification`.

Default document IDs are built with
`general_manager.search.utils.build_document_id(type_label, identification)`.
It formats `"type_label:normalized_identification"`, where
`normalize_identification()` serializes the identification mapping as JSON with
sorted keys and converts non-JSON-native values with `str()`. The JSON encoder
uses Python defaults for every other option, so custom values need a stable
`__str__` for deterministic IDs. Mixed non-string key types follow
`json.dumps(sort_keys=True)` and may raise `TypeError` if Python cannot compare
them; `default=str` applies to values rather than unsupported mapping keys. The
helper accepts empty type labels and labels containing colons without
escaping or disambiguating them, and it does not enforce backend-specific ID
character limits; adapters such as Meilisearch perform their own backend-safe
normalization. For example, `build_document_id("Project:Archive", {"id": 1})`
starts with `Project:Archive:`. Field extraction for indexed data
uses `extract_value(obj, field_path)`: path components are separated by `__`,
`Bucket` instances are traversed before list/tuple/set collections, then
mappings use key lookup, then objects use attributes. Only `Bucket`, list,
tuple, and set values are expanded; arbitrary iterables are treated as plain
objects. Buckets and supported collections apply the remaining path to each
item and return a list; concrete bucket iteration defines item order and shape,
nested collections produce nested lists rather than flattened values, and set
ordering follows the set's own iteration order. Missing paths return `None`;
mapping keys or attributes present with value `None` are indistinguishable from
missing values after lookup. Empty paths return the normalized root object
itself without collection traversal, so non-manager buckets and collections are
returned unchanged, while a root `GeneralManager` becomes its exact
`identification` mapping object as stored on the instance, such as `{"id": 1}`
for a single-id manager. Mapping lookup uses the string path component with
`mapping.get()` and does not fall through to attribute lookup after a missing
key. Empty path components are literal key or attribute names:
`"a__"` looks for an empty key or attribute after `a`, and `"__a"` starts with
an empty key or attribute. Attribute traversal uses `hasattr()` followed by
`getattr()`, so property `AttributeError` can look like a missing attribute and
other descriptor exceptions propagate. Final `GeneralManager` values become
their identification mapping.
Exceptions from JSON serialization, bucket iteration, supported collection
iteration, or property/descriptor access propagate unchanged. `Bucket` refers
to `general_manager.bucket.Bucket`, and `GeneralManager` refers to manager
instances from `general_manager.manager.GeneralManager`.

`reindex_manager(Project)` ensures every configured index and upserts current
documents grouped by index, but it does not delete stale backend documents. Use
`reindex_manager_index(Project, "global")` when a reconciler or maintenance job
needs stale cleanup for one manager/index pair. It returns the number of current
documents serialized, lists existing ids with
`backend.list_document_ids("global", types=[get_type_label(Project)])`, upserts
current documents before stale deletion, and deletes only stale ids reported for
that manager type.

Indexer methods return without action when a manager has no search
configuration, except `reindex_manager_index()` raises
`MissingIndexConfigurationError` when the manager is search-enabled but the
requested index name is not configured. Indexer operations are not atomic across
indexes or phases; earlier backend writes remain if a later step fails. Backend
and serialization errors propagate.

Duplicate `IndexConfig.name` entries are not deduplicated consistently across
operations: single-instance indexing repeats the configured work, full manager
reindexing emits one backend upsert per first-seen index name while still
serializing one document per duplicate config and instance, and
`reindex_manager_index()` uses the first matching config for the requested name.

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

Adapter behavior:
- The backend stores the original GeneralManager document id in
  `gm_document_id` and writes a deterministic Meilisearch-safe `id` primary key.
  Safe ids matching `^[A-Za-z0-9_-]{1,511}$` are kept unchanged. Every other id,
  including empty strings, whitespace, slashes, and Unicode, becomes
  `"gm_" + sha256(str(id)).hexdigest()`. The value is stable and
  collision-resistant, but not reversible. Deletions normalize incoming
  GeneralManager ids the same way before calling Meilisearch.
- `ensure_index()` applies `searchable_fields`, `filterable_fields`, and
  `sortable_fields` when those settings are iterable values such as lists,
  tuples, or sets. Strings and bytes are treated as invalid scalar values and
  ignored. Values inside an accepted iterable are converted with `str(...)`.
  Index creation uses primary key `id`, and index creation/settings updates wait
  for Meilisearch tasks to finish.
- `upsert(index, [])` still ensures the index exists but does not submit an
  `add_documents` task. Non-empty documents are stored with `id`,
  `gm_document_id`, `type`, `identification`, nested `data`, and non-reserved
  `document.data` keys copied to the top level. Reserved top-level keys are
  `id`, `gm_document_id`, `type`, `identification`, and `data`.
  `delete(index, [])` is a no-op and does not access Meilisearch;
  `delete(index, [""])` treats the empty string as a real document id and
  deletes its normalized value.
- Clients exposing `wait_for_task()` use that method. Clients without it fall
  back to polling `get_task()` every 0.1 seconds with exponential backoff capped
  at one second. Polling stops on `succeeded`, `failed`, or `canceled`; unknown
  or missing statuses keep polling until the five-second timeout raises
  `MeilisearchTaskFailedError`. Mapping task responses read `status` and
  `error` keys; object task responses read attributes with the same names. Task
  payloads without a task UID are treated as already complete.
- `list_document_ids()` reads `id`, `gm_document_id`, and `type` in pages of
  1000. `types=None` and `types=[]` include every type. Type filtering is exact,
  `gm_document_id` is preferred over `id`, falsey `gm_document_id` values such
  as `""` fall back to `id`, duplicates collapse into a set, and malformed
  documents without either id field are ignored.
- Structured filters are translated to equality and `in` clauses. Fields inside
  one mapping are ANDed, multiple mappings are ORed, `types` are ORed and then
  ANDed with structured filters, and values are rendered with `str(...)` after
  escaping backslashes and double quotes. The exact comparison syntax is
  `field = "escaped_value"` for every value type: `None` renders as `"None"`,
  booleans as `"True"`/`"False"`, and numbers as decimal strings. Empty `in`
  lists produce `()`. A raw `filter_expression` takes precedence over
  structured filters and `types`, so `types` is ignored when
  `filter_expression` is provided.
- `sort_by` is treated as one raw field name. The backend appends `:asc` or
  `:desc`; it does not validate, split, or escape sort fields.
- Search hit parsing is defensive: malformed hit entries are skipped and
  missing `id`/`gm_document_id` and `type` become empty strings, missing
  `identification` and `data` become empty mappings, and missing
  `_rankingScore` becomes `None` in `SearchHit`.

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
