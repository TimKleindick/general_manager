# Tutorials

These tutorials walk you through common development tasks when working with GeneralManager. Each guide builds on the core concepts and demonstrates practical workflows.

1. [Create a project scaffold](create_project.md)
2. [Add a manager that stores measurements](add_model_with_measurement.md)
3. [Define permission rules](add_permission_rule.md)
4. [Enforce permissions step by step](permission_walkthrough.md)
5. [Expose data over GraphQL](expose_via_graphql.md)
6. [Run historical queries](historical_queries.md)
7. [Batch data-change notifications](bulk_data_change_notifications.md)
8. [Cache dependent calculations](cache_dependent_calculation.md)
9. [Write a custom interface](write_custom_interface.md)
10. [Configure a request interface safely](request_interface.md)
11. [Expose and consume a RemoteAPI safely](remote_manager_interface.md)
12. [Generate test data in bulk](factory_bulk_generate.md)
13. [Create related objects with factories](factory_related_objects.md)
14. [Testing strategies](testing_guides.md)
15. [Analyse permission audit logs](audit_logger_analysis.md)
16. [Handle database migrations](migrations_gotchas.md)
17. [Build workflow event triggers](workflow_events.md)
18. [Operate workflow outbox/dead letters](workflow_ops.md)
19. [Run search reconciliation](search_reconciliation.md)
20. [Upload files through GraphQL](graphql_file_uploads.md)
21. [Use S3 uploads through the authenticated proxy](graphql_file_uploads_s3.md)
22. [Add LLM chat to a GeneralManager project](install_llm_chat.md)
23. [Run the installed chat evaluation suite](run_chat_evals.md)
24. [Keep ORM writes and history on one database](orm_atomic_writes.md)
25. [Write validation rules with reliable fallback errors](write_validation_rules.md)

26. [Read and write Excel workbooks](excel_interface.md)

Follow them in order when you start a new project, or jump to the topic relevant to your current task.

## Chat integration

Follow [installation](install_llm_chat.md), [provider/role configuration](configure_chat.md),
[client integration](integrate_chat.md), [operations](operate_chat.md) and
[evaluation](run_chat_evals.md) for a complete deployment path.
