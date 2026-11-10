# Cookbook

Recipes solve a focused problem and state the managers, data, or configuration
they expect. Adapt them after the [quickstart](../quickstart.md); they are not
independent application scaffolds. Use the [tutorials](../howto/index.md) for a
guided setup and the [concepts](../concepts/index.md) for the underlying model.

## Query and calculate

- [Project volume curve](project_volume_curve.md) and [derivative aggregation](derivatives_groups.md).
- [GraphQL query patterns](graphql_queries.md), [typed output objects](graphql_output_types.md), and [persisted item ordering](graphql_preferred_item_order.md).
- [Historical snapshots](historical_queries.md) and [bulk change notifications](bulk_data_change_notifications.md).

## Validate and authorize

- [Permission patterns](permission_patterns.md) and [permission cookbook](permission_cookbook.md).
- [Field and non-field validation errors](rule_validation.md).
- [Safe mutation errors](graphql_error_handling.md) and [file uploads](graphql_file_upload.md).

## Integrate data sources

- [Existing Django models](existing_model_interface.md).
- [Request interface](request_interface_end_to_end.md), [remote manager](remote_manager_interface_end_to_end.md), and [Excel interface](excel_interface_end_to_end.md).

## Operate integrations

- [Production reference-data maintenance](production_maintenance.md) and [ordered startup hooks](startup_hooks.md).
- [Workflow input snapshots](workflow_input_snapshot.md).
- [Related search invalidation](search_invalidation.md) and [DevSearch hydration](dev_search_lifecycle.md).
- [Custom audit logging](audit_logger_custom.md).
- [Chat queries and client recipes](planned_chat_orchestration.md) and [chat evaluation commands](chat_eval_cli.md).
