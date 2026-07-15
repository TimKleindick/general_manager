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

`FieldConfig` and `IndexConfig` validate only numeric boost/min-score bounds at
construction time. `FieldConfig.boost` and `IndexConfig.boost` must be positive
when provided; `IndexConfig.min_score` must be non-negative when provided.
`IndexConfig.iter_fields()` converts plain string field entries to
`FieldConfig(name=...)` while preserving field order, and `field_boosts()` keeps
only explicit field boosts.

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

::: general_manager.search.backends.dev.DevSearchBackend

::: general_manager.search.backends.meilisearch.MeilisearchBackend

::: general_manager.search.backends.opensearch.OpenSearchBackend

::: general_manager.search.backends.typesense.TypesenseBackend
