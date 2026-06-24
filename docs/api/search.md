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

::: general_manager.search.config.resolve_search_config

::: general_manager.search.config.iter_index_names

`FieldConfig` and `IndexConfig` validate only numeric boost/min-score bounds at
construction time. `FieldConfig.boost` and `IndexConfig.boost` must be positive
when provided; `IndexConfig.min_score` must be non-negative when provided.
`IndexConfig.iter_fields()` converts plain string field entries to
`FieldConfig(name=...)` while preserving field order, and `field_boosts()` keeps
only explicit field boosts.

`SearchConfigSpec.document_id` is a callable that receives the manager instance
being indexed and returns a stable document id string. `SearchConfigSpec.to_document`
receives the manager instance and returns a mapping of document field names to
payload values. `SearchConfigSpec.indexes` is required when constructing the
spec directly. `resolve_search_config()` copies `indexes`, `document_id`,
`type_label`, `to_document`, and `update_strategy` attributes from arbitrary
config objects; a missing `indexes` attribute becomes an empty tuple and missing
optional attributes become `None`. It does not validate that callable attributes
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
