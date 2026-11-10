# Tutorials and Task Guides

Start with the short core path, then choose the guide for your next task.
These pages are not one long tutorial: search, chat, uploads, workflows, and
external data are optional integrations with their own prerequisites.

## Build the core application

1. [Complete the quickstart](../quickstart.md).
2. [Create and update application data](create_project.md).
3. [Define your first permission rule](add_permission_rule.md).
4. [Expose queries and mutations through GraphQL](expose_via_graphql.md).
5. [Choose application testing strategies](testing_guides.md).

For the reasoning behind these steps, follow the [learning paths](../learning_paths.md).

## Model and change data

- [Use a custom user model](custom_user_model.md).
- [Store measurements](add_model_with_measurement.md) or [work with measurement dataframes](dataframe_measurements.md).
- [Write validation rules](write_validation_rules.md) and [apply advanced permission policies](permission_walkthrough.md).
- [Read historical snapshots](historical_queries.md).
- [Create records in bounded batches](create_many.md), [benchmark imports](benchmark_bulk_imports.md), and [batch change notifications](bulk_data_change_notifications.md).
- [Keep writes and history atomic](orm_atomic_writes.md).
- [Cache dependent calculations](cache_dependent_calculation.md).
- [Generate test data](factory_bulk_generate.md) and [create related fixtures](factory_related_objects.md).

## Connect data and clients

- [Configure an HTTP request interface](request_interface.md).
- [Expose and consume a remote GeneralManager service](remote_manager_interface.md).
- [Read and write Excel workbooks](excel_interface.md).
- [Add GraphQL file uploads](graphql_file_uploads.md), including [S3 storage](graphql_file_uploads_s3.md).
- [Configure search](search.md).
- [Build workflow event triggers](workflow_events.md).

For a backend that needs its own implementation, follow
[custom interfaces](write_custom_interface.md), [interface types](create_custom_interface_type.md),
and [capabilities](create_custom_capability.md).

## Add chat

Follow [installation](install_llm_chat.md), [provider and role configuration](configure_chat.md),
[client integration](integrate_chat.md), [operations](operate_chat.md), and
[evaluation](run_chat_evals.md) in that order.

## Operate and maintain an application

| Task | Guide and command entry point |
| --- | --- |
| Change database schema or upgrade the library | [Migrations](migrations_gotchas.md), [API upgrades](migrate-to-stable-api.md) |
| Synchronize reference data after migration | [Startup maintenance](startup_maintenance.md): `sync_read_only` |
| Build or repair search indexes | [Search](search.md): `search_index`; [reconciliation](search_reconciliation.md): `search_reconcile` |
| Drain durable work or retry failures | [Workflow operations](workflow_ops.md): `workflow_drain_outbox`, `workflow_replay_dead_letters` |
| Clean up abandoned uploads | [Upload lifecycle](graphql_file_uploads.md): `cleanup_upload_intents` |
| Operate chat and retention | [Chat operations](operate_chat.md): `chat_cleanup` |
| Inspect access and runtime behavior | [Audit analysis](audit_logger_analysis.md), [observability](../concepts/observability/index.md) |

Run management commands with `python manage.py <command> --help` for the
installed version's options. The linked guides explain configuration,
deployment order, and when a command is appropriate.
