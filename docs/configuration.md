# Configure an application

GeneralManager uses your Django project's settings. Start with the
[quickstart](quickstart.md), then enable the integrations your application
needs. You do not need to configure search, chat, uploads, or a workflow worker
to use a database-backed manager.

## Keep application settings together

Use a single `GENERAL_MANAGER` dictionary in `settings.py`. For example, an
application that exposes GraphQL can start with:

```python
GENERAL_MANAGER = {
    "AUTOCREATE_GRAPHQL": True,
    "GRAPHQL_URL": "graphql/",
    "READ_ONLY_SYNC_ON_STARTUP": False,
}
```

This creates the GraphQL endpoint and disables automatic synchronization of
read-only reference data before management commands. Run explicit maintenance
when the application uses that data; see [startup maintenance](howto/startup_maintenance.md).
The setting does not disable manager discovery or schema construction.

Guides show the settings relevant to their topic. Merge those entries into your
existing dictionary: assigning a new `GENERAL_MANAGER = {...}` later in the
file replaces the earlier configuration. For example, add pagination limits
without losing the endpoint settings:

```python
GENERAL_MANAGER.update({
    "GRAPHQL_DEFAULT_PAGE_SIZE": 25,
    "GRAPHQL_MAX_PAGE_SIZE": 100,
})
```

Keep Django settings such as `INSTALLED_APPS`, `DATABASES`, `CACHES`, `STORAGES`,
and `LOGGING` at Django's top level. They are not entries in `GENERAL_MANAGER`.
Declare manager-specific fields, permissions, search indexes, and capabilities
on the manager or its interface, as shown in the corresponding guides.

## Find settings by task

The links below explain prerequisites, accepted values, and defaults. This map
is a starting point, not a second copy of every integration's settings table.

| Task | Configuration to look for | Guide |
| --- | --- | --- |
| Expose a GraphQL API | `AUTOCREATE_GRAPHQL`, `GRAPHQL_URL` | [First query and mutation](howto/expose_via_graphql.md) |
| Bound list queries | `GRAPHQL_DEFAULT_PAGE_SIZE`, `GRAPHQL_MAX_PAGE_SIZE` | [Filters and pagination](concepts/graphql/filters_pagination.md) |
| Synchronize read-only data at deployment | `READ_ONLY_SYNC_ON_STARTUP` | [Startup maintenance](howto/startup_maintenance.md) |
| Reuse calculations across runs | Django `CACHES`; per-call cache scope and timeout | [Caching](concepts/caching.md) and [worked example](howto/cache_dependent_calculation.md) |
| Limit memory within a calculation run | `RUN_CONTEXT_CACHE_MAX_BYTES` | [Run context storage](concepts/caching.md#run-context-storage) |
| Record permission decisions | `AUDIT_LOGGER` | [Audit logging](concepts/observability/audit_logging.md) |
| Collect GraphQL metrics | `GRAPHQL_METRICS_ENABLED`, `GRAPHQL_METRICS_BACKEND` | [Metrics](concepts/observability/graphql_metrics.md) |
| Add full-text search | `SEARCH_BACKEND`; manager `SearchConfig` | [Search setup](howto/search.md) |
| Repair persistent search indexes | `SEARCH_RECONCILE_*` settings | [Reconciliation](howto/search_reconciliation.md) |
| Route events and run workflows | `WORKFLOW_MODE`, `WORKFLOW_EVENT_REGISTRY`, `WORKFLOW_ENGINE` | [First event handler](howto/workflow_events.md), then [operations](howto/workflow_ops.md) |
| Accept private file uploads | `FILE_UPLOADS`; Django storage configuration | [Upload setup and settings](howto/graphql_file_uploads.md) |
| Add optional chat | `CHAT` | [Installation](howto/install_llm_chat.md), then [providers and roles](howto/configure_chat.md) |
| Consume an external API or workbook | Interface attributes and capabilities | [HTTP requests](howto/request_interface.md), [remote managers](howto/remote_manager_interface.md), or [Excel](howto/excel_interface.md) |

Some integrations create tables, register routes, or require worker processes.
Changing their settings alone does not perform those deployment steps. Follow
the setup guide before using the feature, and use the
[operations map](howto/index.md#operate-and-maintain-an-application) for recurring
maintenance commands.

## Understand legacy setting names

Prefer the nested dictionary for new configuration. Settings read through
`general_manager.conf.get_setting` use this order:

1. `GENERAL_MANAGER["KEY"]`, when `GENERAL_MANAGER` is a dictionary.
2. The legacy prefixed setting `GENERAL_MANAGER_KEY`.
3. The legacy top-level setting `KEY`.
4. The feature's default.

For those lookups, an explicitly present value such as `False` or `None` wins
over a legacy value. Its meaning is then determined by the feature. For example,
the metrics guide's `GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED = True` can be
written as `GENERAL_MANAGER["GRAPHQL_METRICS_ENABLED"] = True` instead.

This is not a universal loader for every subsystem. Audit logger, search backend,
and workflow configuration have their own loaders: their guides document nested
settings and top-level fallbacks. Do not assume a `GENERAL_MANAGER_` prefixed
alias is supported for those settings.

When a setting appears ineffective, check for an earlier value in the nested
dictionary, a later reassignment of the whole dictionary, and the settings
module used by the running process. Apply changes through your normal application
restart or deployment; settings are not a live configuration API, and several
backends are initialized or cached per process.
