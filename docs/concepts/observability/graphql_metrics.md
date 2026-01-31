# GraphQL metrics

GeneralManager can emit lightweight GraphQL metrics for Prometheus/Grafana
dashboards. The instrumentation is opt-in and avoids high-cardinality labels by
default.

## Enable metrics

Add the following to `settings.py`:

```python
GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED = True
GENERAL_MANAGER_GRAPHQL_METRICS_BACKEND = "prometheus"  # or "noop"
GENERAL_MANAGER_GRAPHQL_METRICS_OPERATION_ALLOWLIST = [
    "MyQuery",
    "MyMutation",
]
GENERAL_MANAGER_GRAPHQL_METRICS_MAX_OPERATION_LENGTH = 64
GENERAL_MANAGER_GRAPHQL_METRICS_MAX_LABEL_LENGTH = 128
GENERAL_MANAGER_GRAPHQL_METRICS_UNKNOWN_OPERATION_POLICY = "unknown"  # or "hash"
GENERAL_MANAGER_GRAPHQL_METRICS_RESOLVER_TIMING = False
```

If `prometheus-client` is unavailable, the backend falls back to a no-op implementation.

## Metrics emitted

Request-level metrics (always available when enabled):

- `graphql_requests_total{operation_name, operation_type, status}`
- `graphql_request_duration_seconds_bucket{operation_name, operation_type}`
- `graphql_errors_total{operation_name, code}`

Optional resolver timing (when `GENERAL_MANAGER_GRAPHQL_METRICS_RESOLVER_TIMING=True`):

- `graphql_resolver_duration_seconds_bucket{field_name}`
- `graphql_resolver_errors_total{field_name}`

## Label policies

- `operation_name` is taken from the GraphQL `operationName` and normalized to ASCII.
- When an allowlist is configured, any non-allowlisted operation is labeled as `unknown`.
- If `GENERAL_MANAGER_GRAPHQL_METRICS_UNKNOWN_OPERATION_POLICY` is set to `hash`,
  unknown operations are labeled as `op_<hash>` instead of `unknown`.
- `operation_type` is one of `query`, `mutation`, `subscription`, or `unknown`.
- `code` is sourced from GraphQL error `extensions.code` when present; otherwise `unknown`.
- `field_name` is `ParentType.field`, normalized and truncated.

## Performance notes

Resolver timing adds overhead because every field resolution is timed and
recorded. Keep it off unless you need resolver-level diagnostics.
