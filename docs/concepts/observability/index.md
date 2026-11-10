# Observability Overview

GeneralManager emits structured events for logs, audits, and cache changes so you can monitor manager activity without sprinkling instrumentation across your codebase. Observability relies on existing Django features (logging, signals, middleware) and keeps the framework agnostic to your preferred stack (ELK, OpenTelemetry, CloudWatch, etc.).

Choose the signal that answers your question:

| Question | Start here |
| --- | --- |
| What happened during a request or write? | [Application logging](logging.md) |
| Which permission check allowed or denied an operation? | [Permission audit logging](audit_logging.md) |
| Which GraphQL operations or resolvers are slow? | [GraphQL metrics](graphql_metrics.md) |
| When may an external observer act on a write? | [Data-change lifecycle](../../howto/bulk_data_change_notifications.md#observe-the-data-change-lifecycle) |

These mechanisms observe different boundaries. Logs and metrics depend on
their configuration; audit records cover evaluated permission checks, not all
Python reads. Mutation signals can occur inside a transaction, so external
side effects need the documented commit boundary.

## Goals

- Surface every meaningful manager lifecycle change (`create`, `update`, `delete`) with consistent metadata (`manager`, `identification`, `creator_id`, touched fields).
- Make permission evaluations, cache invalidations, and rule executions observable through their configured logging paths.
- Allow projects to plug additional telemetry (traces, metrics) on top of the emitted signals without modifying GeneralManager internals.

## Built-in instrumentation

- **Structured logging** powered by `general_manager.logging.get_logger`, used across API, manager, cache, rule, and interface modules. Each log entry carries the `component` name and an optional `context` mapping so downstream pipelines can filter or aggregate events.
- **Django signals** (`general_manager.cache.signals.pre_data_change` / `post_data_change`) wrapping manager mutations. Signal receivers fuel cache invalidation and can be extended to feed metrics, search indexes, or analytics pipelines.
- **Permission audit events** emitted through `general_manager.permission.audit` when audit logging is enabled. They capture the actor, action (`create`, `read`, `update`, `delete`), affected attributes, and granted/denied status.
- **Rule evaluation traces** logged via `general_manager.rule.engine` with variable sets and outcomes, helpful when rules gate financial or safety-critical operations.
- **GraphQL metrics** (optional) measure request outcomes and latency, with resolver timing available when enabled.

## Integration tips

1. Configure Django's `LOGGING` dictionary to route `general_manager` namespaces to your preferred handlers (JSON, SIEM, serverless log sinks).
2. Propagate correlation identifiers with middleware (request IDs, Celery task IDs) so log entries and audit events can be joined to API calls or background jobs.
3. Subscribe to the cache change signals if you need metrics (e.g. Prometheus counters for invalidations) or side effects (event-driven projections).
4. Enable audit logging when regulatory reporting is required, and forward the resulting events to long-term storage.
5. Pair log parsing with alerting (e.g. detect repeated `permission denied` or cache invalidation storms) to catch regressions early.

## Continue reading

- [Logging & Observability](logging.md): complete guide to logger names, configuration, and emitted fields.
- [Permission audit logging](audit_logging.md): configure persistent trails for granted and denied permission checks.
- [GraphQL metrics](graphql_metrics.md): enable request and resolver metrics for Prometheus/Grafana.
- [Caching internals](../caching.md): understand how dependency tracking and invalidation signals keep cached resolvers fresh.
