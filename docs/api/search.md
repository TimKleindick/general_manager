# Search API

`general_manager.search` re-exports the configuration types, backend protocol
and data/error types, backend registry helpers, `SearchIndexer`, and concrete
backend classes listed in the public API snapshot. Reconciliation, task, async
task, registry, and utility helpers are public through their canonical
submodules shown below, but they are not re-exported from `general_manager.search`.

::: general_manager.search.config.FieldConfig

::: general_manager.search.config.IndexConfig

::: general_manager.search.config.SearchConfigProtocol

::: general_manager.search.config.SearchConfigSpec

::: general_manager.search.config.SearchChange

::: general_manager.search.config.SearchInvalidationRule

::: general_manager.search.config.resolve_search_config

::: general_manager.search.config.iter_index_names

`FieldConfig` and `IndexConfig` validate numeric boost/min-score bounds and
reject the reserved document-envelope names `id`, `gm_document_id`, `type`,
`identification`, `data`, `gm_identity`, and `gm_numeric_tokens` at construction
time. `FieldConfig.boost` and `IndexConfig.boost` must be positive when provided;
`IndexConfig.min_score` must be non-negative when provided.
`IndexConfig.iter_fields()` converts plain string field entries to
`FieldConfig(name=...)` while preserving field order, and `field_boosts()` keeps
only explicit field boosts.

`SearchConfig.field_dependencies` is an optional mapping from configured document
keys to nonempty sequences of exact native source paths. Resolution freezes it
as a mapping of tuples. For GraphQL search, custom `to_document` projections and
native properties require complete provenance for every configured searchable,
filterable, and sortable key. It is a trusted application assertion covering all
branches and indirect reads, not an assertion that output values equal source
values. Native projections always authorize their actual serialized fields in
addition to the declared paths. Custom native fallback fields must be included
in their own dependencies; terminal relations may instead declare every native
identity leaf serialized by that fallback. Invalid or incomplete provenance fails
before backend search. Declared dependencies contribute to the index's schema
fingerprint, so the existing reconciliation pipeline handles declaration changes.
Indexes without declarations retain their previous fingerprint.
See [projection dependencies](../concepts/search.md#projection-dependencies).

`SearchInvalidationRule` declares a source manager whose lifecycle changes can
invalidate search documents owned by the manager containing the rule. Sources
may be manager classes or dotted import paths; dotted paths remain unchanged in
the frozen declaration and are resolved lazily by startup validation. Optional
`indexes` select a non-empty subset of the owner's configured indexes. Optional
`relation` names a supported owner-side many-to-many relation. Resolver
callbacks receive a frozen `SearchChange` and the owner manager class. Resolved
configurations normalize `invalidation_rules` to a tuple, defaulting to an empty
tuple.

`SearchChange` exposes `action` (`"create"`, `"update"`, or `"delete"`),
`phase` (`"before"` or `"after"`), the source manager `instance`, and its
`database_alias`. Create invokes resolvers after the mutation, update invokes
them before and after, and delete invokes them before. Resolver results must be
instances of the owner manager. The framework copies each result's
identification before scheduling it. `resolve=None`, resolver errors, invalid
targets, and bounded overflow mark the selected owner/index pairs dirty without
dispatching partial targeted work.

`GENERAL_MANAGER["SEARCH_INVALIDATION_MAX_TARGETS"]` is the per-event resolver
ceiling and defaults to `1000`.
`GENERAL_MANAGER["SEARCH_INVALIDATION_BATCH_SIZE"]` controls exact-index task
chunks and defaults to `100`. Each must be a positive, non-boolean integer;
invalid values force dirty fallback for the affected work. The standard
GeneralManager setting lookup order also permits legacy prefixed and top-level
settings.

Relation bindings support auto-created and custom Django through models when
the owner has exactly the `id` input and both through foreign keys target their
endpoint primary keys. Startup checks use these IDs:

- `general_manager.search.E000`: invalid declaration/configuration;
- `E001`: source does not resolve to a manager;
- `E002`: resolver is not callable;
- `E003`: selected indexes are invalid;
- `E004`/`E005`: owner/source is not ORM-backed;
- `E006`: relation is not the expected owner M2M field;
- `E007`: owner does not use exactly the standard `id` input;
- `E008`: relation is self-symmetrical;
- `E009`: a through foreign key does not target the endpoint primary key.

The M2M bridge observes related-manager `add`, `remove`, `clear`, and `set`
signals in forward and reverse directions. It does not observe direct through
model writes, raw SQL, or bulk operations. These are unsupported and require
explicit reconciliation.

`SearchConfigSpec.document_id` is a callable that receives the manager instance
being indexed and returns a stable document id string. The same value is
captured before deletion, so applications must keep it stable across updates and
related invalidation. `SearchConfigSpec.to_document`
receives the manager instance and returns a mapping of document field names to
payload values. `SearchConfigSpec.indexes` is required when constructing the
spec directly. `resolve_search_config()` copies `indexes`, `document_id`,
`type_label`, `to_document`, `update_strategy`, and `invalidation_rules`
attributes from arbitrary config objects; missing `indexes` and
`invalidation_rules` attributes become empty tuples and missing optional
attributes become `None`. It does not validate that callable attributes
are callable or that every `indexes` entry is an `IndexConfig`; invalid values
fail later when the registry or indexer uses them. Attribute access errors from
unusual config objects propagate. `iter_index_names()` returns a concrete list
of names, or an empty list for `None`.

::: general_manager.search.registry.SearchIndexSettings

::: general_manager.search.registry.iter_searchable_managers

::: general_manager.search.registry.get_search_config

::: general_manager.search.registry.get_index_config

::: general_manager.search.registry.iter_index_configs

::: general_manager.search.registry.get_type_label

::: general_manager.search.registry.get_searchable_type_map

::: general_manager.search.registry.collect_index_settings

::: general_manager.search.registry.get_index_names

::: general_manager.search.registry.get_filterable_fields

::: general_manager.search.registry.validate_filter_keys

::: general_manager.search.registry.InvalidFilterFieldError

The search registry reads manager classes from `GeneralManagerMeta.all_classes`
and resolves each manager's `SearchConfig`. Helpers skip managers without a
search config or without indexes and preserve `GeneralManagerMeta.all_classes`
order while iterating. Index lookup helpers return the first matching
`IndexConfig` for a manager/index pair; duplicate type labels in
`get_searchable_type_map()` are resolved by the later registered manager
overwriting the earlier one without a warning or error.
`collect_index_settings()` preserves searchable field first-seen order, sorts
filterable and sortable fields, always includes the synthetic `"type"` filter,
and keeps the highest configured boost per searchable field. Missing boosts do
not create boost entries, and boosts are collected only from fields yielded by
`IndexConfig.iter_fields()`. `validate_filter_keys()` validates only filter
mapping keys, not values; lookup suffixes after the first `"__"` are ignored for
the filterability check. Aside from `InvalidFilterFieldError`, registry helpers
do not wrap search-configuration errors: malformed `SearchConfig` declarations
raise whatever `resolve_search_config()` raises.

::: general_manager.search.models.SearchIndexState

`SearchIndexState` is the durable reconciliation row for one
`(manager_path, index_name)` pair. It stores the schema fingerprint,
dirty-marker fields, worker claim fields, the latest reconciliation error, and
timestamps. Its operational index names are declared explicitly to match the
checked-in `0004_search_index_state.py` migration and avoid no-op generated
index-rename migrations.

::: general_manager.search.backend.SearchDocument

::: general_manager.search.identity.SearchIdentityFilterGroup

::: general_manager.search.identity.build_search_filters

Explicit identity provenance travels separately from native document/filter
values. `SearchIndexer` inherits actual ORM PK/reference metadata and
`@graph_ql_property(is_identifier=True)` declarations for configured fields,
including terminal identity fields in configured relation paths.
`IndexConfig.identity_fields={"customer_alias": int}` declares aliases supplied
by custom document mappers. Supported scalar types are `int`, `str`, and `UUID`;
nullable/collection identities use the scalar element type. Unsupported types,
unconfigured alias names, and overrides conflicting with manager metadata fail
with `general_manager.search.identity.SearchIdentityConfigurationError`.

`build_search_filters(manager_class, index_name, filters)` returns dict-compatible
native filter groups carrying identity types and the manager's type label.
Caller keys, values, AND/OR group boundaries, and permission constraints remain
unchanged. Generated GraphQL search attaches this metadata after merging native
user and permission filters. For manual documents, provide both
`SearchDocument.identity_fields` and a `SearchIdentityFilterGroup`; the latter's
optional `type_label` scopes a shared index. These helpers are also re-exported
from `general_manager.search`.

Index settings carry native contracts separately from ordinary configured
filter names. For manual documents, configure their filterability explicitly:

```python
backend.ensure_index("owned", {
    "filterable_fields": ["type", "customer_alias"],
    "identity_fields": {"Record": {"customer_alias": int}},
})
```

`identity_fields` in settings is keyed by document type label, so shared indexes
retain each owner's native kinds. Meilisearch derives its shadow fields from
declared names that are also filterable. Other backends receive original
`filterable_fields` and can consume or ignore the additional native contracts.
Document and filter metadata must agree on the native kind.

## Generated GraphQL search authorization

When at least one registered manager has search configuration, GraphQL adds a
global `search` field with the following public contract:

```graphql
search(
  query: String!
  index: String
  types: [String]
  filters: JSONString
  orderBy: [SearchOrderBy!]
  totalMode: String
  page: Int
  pageSize: Int
): SearchResult
```

The result exposes `results`, `total`, `totalIsExact`, `pageInfo`, and `tookMs`.
Omitted `index`, `page`, and `pageSize` use `global`, `1`, and `10`; non-positive
`page` or `pageSize` values are `BAD_USER_INPUT`. `totalMode` is `exact` by
default for compatibility or `bounded` when selected per request or by
`GENERAL_MANAGER["GRAPHQL_SEARCH_TOTAL_MODE"]`. Both modes raise
`SEARCH_SCAN_LIMIT_EXCEEDED` when the server-owned
`GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT` is exhausted before source exhaustion is
established, including a full final backend page at the boundary. Neither returns
partial results. **Compatibility change:** bounded mode no longer returns a
partial count or page; `totalIsExact` remains in the schema and is always `true`
on success. The positive candidate budget defaults to `1000` and is shared
across selected manager types; denied and stale hits consume it too. A backend
can explicitly attest exhaustion even on a full final page. An explicitly
incomplete short or empty page never establishes an exact result.

The environment variable `GENERAL_MANAGER_GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT`
provides a positive-integer fallback when no explicit Django scan-limit setting
is present. Existing nested, prefixed and top-level Django settings retain their
precedence over the environment; the default remains `1000`. Invalid environment
values raise `BAD_USER_INPUT`. Restart application workers after deployment
environment changes. Meilisearch's per-index `pagination.maxTotalHits` is an
independent provider cap and is not changed by this variable.

`orderBy` is present when the registered searchable managers expose sortable
fields; its `SearchOrderBy` input uses the generated sortable-field enum and
`ASC`/`DESC` directions.

Schema construction classifies each manager/index projection from immutable
definitions. Proven absence of additional relevant field restrictions enables
fast routing; extra field restrictions and unknown/custom executable permission
rules retain thorough checks. Rebuild the schema after restarting with changed
definitions. This classification caches no user or object decisions.

All serialized search/filter/sort keys are authorized, even if unused by request
operands. Only native `DatabaseBucket` sources with standard `id` identification
use fast hit-batch hydration; calculation and remote sources retain full-domain
checks and remote completeness requirements. Hits must match canonical authorized
native instances. Missing or out-of-domain identities, malformed identification,
and unexpected types are skipped. On fast related hits, denial of a referenced
object removes the whole owner projection before count, sorting, and pagination;
the thorough route raises on denied required operand access.

Every hit must supply stored `SearchHit.data` with exactly the configured
searchable, filterable, and sortable keys. Missing stored data raises an error;
extra or missing keys skip the hit pending reindexing, preventing removed
restricted fields from influencing search. The separate indexing/invalidation
pipeline owns value freshness, including obsolete content from former relation
targets. Search does not reserialize documents or compare their values with
current source values. Custom projection filters are not
native permission pushdown because declared sources need not equal mapped values.

The synthetic `type` filter is removed from operand authorization unless the
manager exposes an actual field named `type`; an actual field remains checked.
The `types` argument selects manager classes, while `type` filter matching retains
the backend's native semantics. Invalid configured filter keys and ordering terms
are GraphQL errors. Backend, source authorization, permission, and sort errors
propagate.

DevSearch honors explicit groups without changing its existing scalar/list
comparison rules; plain mappings keep raw comparisons. Meilisearch binds plain
structured filters to registered index/manager contracts, and explicit groups
also work without registration. Its declared identity `exact`/`in` filters use
bounded tokens under the reserved `gm_identity` envelope, preserving adjacent
large integers, UUIDs, opaque text case, and leading zeros. Original data stays
stored for retrieval and sorting; UUID/set conversion occurs only in the JSON
payload copy. Other lookups and unmarked business fields retain adapter behavior.
Custom backends continue receiving mappings/native values and must honor identity
metadata when their provider has lossy comparison semantics. A raw Meilisearch
`filter_expression` bypasses structured identity transport.

Existing persistent documents require a full reindex. Rows lacking tokens are
omitted from declared identity filters; mixed indexes never fall back to numeric
equality. See [the migration procedure](../howto/search.md#reindex-existing-identity-projections).

::: general_manager.search.backend.SearchHit

::: general_manager.search.backend.SearchResult

::: general_manager.search.backend.SearchBackend

::: general_manager.search.backend.SearchBackendError

::: general_manager.search.backend.SearchBackendNotConfiguredError

::: general_manager.search.backend.SearchBackendNotImplementedError

::: general_manager.search.backend.SearchBackendClientMissingError

::: general_manager.search.backend_registry.configure_search_backend

::: general_manager.search.backend_registry.configure_search_backend_from_settings

::: general_manager.search.backend_registry.get_search_backend

::: general_manager.search.indexer.SearchIndexer

::: general_manager.search.reconciliation.SearchIndexTarget

::: general_manager.search.reconciliation.SearchStateEnsureResult

::: general_manager.search.reconciliation.SearchReconcileResult

::: general_manager.search.reconciliation.InvalidSearchReconciliationManagerPathError

::: general_manager.search.reconciliation.manager_import_path

::: general_manager.search.reconciliation.build_search_schema_fingerprint

::: general_manager.search.reconciliation.iter_search_index_targets

::: general_manager.search.reconciliation.ensure_search_index_states

::: general_manager.search.reconciliation.mark_search_indexes_dirty

::: general_manager.search.reconciliation.reconcile_search_indexes

::: general_manager.search.tasks.search_reconcile_enabled

::: general_manager.search.tasks.search_reconcile_interval_seconds

::: general_manager.search.tasks.configure_search_reconcile_beat_schedule_from_settings

::: general_manager.search.tasks.reconcile_search_indexes_task

::: general_manager.search.async_tasks.SearchIndexAction

::: general_manager.search.async_tasks.SearchIdentification

::: general_manager.search.async_tasks.InvalidSearchIndexActionError

::: general_manager.search.async_tasks.InvalidSearchManagerPathError

::: general_manager.search.async_tasks.index_instance_task

::: general_manager.search.async_tasks.delete_instance_task

::: general_manager.search.async_tasks.dispatch_index_update

## DevSearch compatibility and lifecycle notes

`DevSearchBackend` is the built-in process-local backend for development. Its
existing no-argument construction remains inert and compatible with callers
that manage indexes explicitly. The new keyword-only
`DevSearchBackend(*, auto_reindex: bool = False)` option opts a directly
constructed backend into first-search hydration. The public
`auto_reindex_enabled: bool` property reports the current setting, and
`configure_auto_reindex(enabled: bool) -> None` changes it for later searches.

When `SEARCH_AUTO_REINDEX` is truthy in the nested `GENERAL_MANAGER` mapping,
or in the top-level setting when the nested key is absent, a selected
`DevSearchBackend` hydrates each index on its first search. The setting is
ignored for external backends and does not require Django `DEBUG=True`. Each
configured manager is reindexed in the serving process; source and reindexing
exceptions propagate to the triggering search, and a failed index remains
retryable. Same-process synchronous invalidation continues to update a hydrated
projection, while writes or `search_index --reindex` in another process cannot
populate a running DevSearch instance.

Multi-term DevSearch queries now require every distinct lowercased query term to
equal or prefix a token from at least one indexed field. Terms may match
different fields, repeated terms do not inflate the score, and empty-query
behavior is unchanged. External backend query semantics are unaffected.

::: general_manager.search.backends.dev.DevSearchBackend

::: general_manager.search.backends.meilisearch.MeilisearchBackend

`MeilisearchBackend.ensure_index(index_name, settings) -> None` creates the
index with primary key `id` when needed, then reads effective settings before
submitting changes. Ranking rules and searchable fields compare in order;
plain filterable and sortable fields compare by membership. Identity contracts
add `gm_identity.*` shadow fields only for declared filterable identities.
For each explicit searchable field, numeric aliases under `gm_numeric_tokens.*`
are appended after the original searchable attributes, preserving their priority.
An empty searchable list remains empty; `['*']` still searches all attributes.
Omitted searchable, filterable, and sortable setting keys remain unmanaged,
while explicit empty lists clear their setting; ranking rules are always
normalized with `sort` first. Each submitted task is awaited before the next
one. A settings-read, configuration, client, or task failure propagates; a later
call can retry any settings that were not successfully applied. The method
accepts the documented
`index_name: str` and `settings: Mapping[str, object]` parameters and returns
`None`. Malformed `identity_fields` contracts raise
`SearchIdentityConfigurationError`; failed or canceled task results and
timeouts in the fallback `get_task()` polling loop raise
`MeilisearchTaskFailedError`. Exceptions from the client's `wait_for_task()`
method, including its timeout errors, propagate unchanged.

`upsert()` derives complete ASCII digit runs from serialized string values,
including collection and mapping values, and adds both the original and
zero-stripped forms. For example, `Porsche_P0375_Strangpressprofile` gains
`0375` and `375`. Mapping keys and non-string scalars are not expanded. At most
two aliases are produced per run, without integer conversion or substring
enumeration. Shadow field names encode their source field, so filter-/sort-only
fields do not become searchable. Source data, filter/sort values, identification
and GraphQL read authorization are unchanged. This is Meilisearch-specific;
the development backend and custom adapters retain their existing tokenization.

After upgrading, run `python manage.py search_index --index global --reindex`
(repeat for other persistent indexes). Updating settings alone cannot add tokens
to existing documents; reindexing is required for consistent numeric matching.

`MeilisearchBackend.search()` reads settings once per backend call, sharing the
read between ordering validation and completeness checks. Its optional
keyword-only `metadata_client` supplies that authoritative settings read; it
defaults to `client`. Search, index hydration, and writes always remain on
`client`, so deployments can use a `search` + `indexes.get` key there and a
separate `indexes.get` + `settings.get` metadata key for `metadata_client`.
Both clients must address the same trusted server and index. When
`searchCutoffMs` is absent, the metadata client must also authorize
`get_version()` and attest an exact stable `1.7.x` `pkgVersion`; that is the
supported pre-cutoff compatibility range. Unknown, malformed, or newer versions
cannot establish completeness.

A short page is attested as exhausted only below `pagination.maxTotalHits` with
valid response provenance. Reaching the cap cannot establish a complete
authorized total. Degraded responses, responses reaching the search cutoff,
missing timing proof and raw page overdelivery fail explicitly. This inspects
backend pagination and response metadata; it does not check indexed content
freshness.

A provider may return a smaller effective page than requested. When Meilisearch
echoes a valid limit and the requested offset, and the raw response fills that
effective page without discarded entries, the adapter attests its
`SearchResult.next_offset`. GraphQL can then continue from that offset while
counting every returned hit against the existing scan budget. A full effective
page does not establish the final total; a later page must still prove exhaustion.
Missing or inconsistent pagination evidence, unproven short pages and cap-boundary
responses retain the existing fail-closed behavior.

Custom adapters can set `SearchResult.exhausted` to attest complete enumeration
(`True`) or to prevent a short page being treated as complete (`False`). The
compatibility default `None` retains the existing short-page contract. Adapters
must reject a truncated intermediate page themselves: a later terminal page
cannot repair an earlier incomplete response.
An optional `next_offset` can accompany `exhausted=False` to prove that a
nonempty page is complete at a smaller effective limit. It must be an integer
(not a boolean) equal to the requested offset plus the number of normalized hits,
with no raw entries omitted. It permits continuation without changing the
exhaustion contract. The default `None` leaves legacy adapters unchanged.

::: general_manager.search.backends.opensearch.OpenSearchBackend

::: general_manager.search.backends.typesense.TypesenseBackend
