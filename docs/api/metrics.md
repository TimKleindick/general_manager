# Metrics API

This reference describes the opt-in GraphQL metrics backend, resolver
middleware and label helpers. For installation, settings, emitted metrics and
operational limits, follow [GraphQL metrics](../concepts/observability/graphql_metrics.md).
The [observability overview](../concepts/observability/index.md) distinguishes
metrics from application logs and permission audit records.

::: general_manager.metrics.graphql.GraphQLMetricsBackend

::: general_manager.metrics.graphql.GraphQLMetricsSettings

::: general_manager.metrics.graphql.NoopGraphQLMetricsBackend

::: general_manager.metrics.graphql.PrometheusGraphQLMetricsBackend

::: general_manager.metrics.graphql.GraphQLResolverTimingMiddleware

::: general_manager.metrics.graphql.get_graphql_metrics_backend

::: general_manager.metrics.graphql.reset_graphql_metrics_backend_for_tests

::: general_manager.metrics.graphql.build_graphql_middleware

::: general_manager.metrics.graphql.graphql_metrics_enabled

::: general_manager.metrics.graphql.graphql_metrics_resolver_timing_enabled

::: general_manager.metrics.graphql.normalize_operation_name

::: general_manager.metrics.graphql.normalize_operation_type

::: general_manager.metrics.graphql.normalize_error_code

::: general_manager.metrics.graphql.normalize_field_name

::: general_manager.metrics.graphql.resolve_operation_type

::: general_manager.metrics.graphql.extract_error_code
