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
`GENERAL_MANAGER_GRAPHQL_METRICS_BACKEND = "noop"` deliberately selects that
no-op backend without warning. Unknown backend names also fall back to no-op and
emit a warning. The active backend is cached per process; use
`reset_graphql_metrics_backend_for_tests()` in tests that change metrics
settings.

## Metrics emitted

Request-level metrics (always available when enabled):

- `graphql_requests_total{operation_name, operation_type, status}`
- `graphql_request_duration_seconds_bucket{operation_name, operation_type}`
- `graphql_errors_total{operation_name, code}`

Optional resolver timing (when `GENERAL_MANAGER_GRAPHQL_METRICS_RESOLVER_TIMING=True`):

- `graphql_resolver_duration_seconds_bucket{field_name}`
- `graphql_resolver_errors_total{field_name}`

`PrometheusGraphQLMetricsBackend` registers collectors in the default Prometheus
registry on first construction and reuses existing collectors with the same
names in that process. Backend recording methods clamp negative, `NaN`, and
infinite durations to `0.0`. Prometheus registration or recording exceptions are
not swallowed by the backend itself; `GeneralManagerGraphQLView` and
`GraphQLResolverTimingMiddleware` catch metrics backend failures and log them at
debug level so request/resolver execution is not changed by telemetry failures.

## Label policies

- `operation_name` is taken from the GraphQL `operationName` and normalized to
  ASCII with `text_unidecode`, stripped, sanitized to `[0-9A-Za-z_.-]`, and
  truncated after sanitization when `GENERAL_MANAGER_GRAPHQL_METRICS_MAX_OPERATION_LENGTH`
  is positive.
- When an allowlist is configured, any non-allowlisted operation is labeled as `unknown`.
- If `GENERAL_MANAGER_GRAPHQL_METRICS_UNKNOWN_OPERATION_POLICY` is set to `hash`,
  unknown operations are labeled as `op_<sha256-prefix>` instead of `unknown`;
  the SHA-256 hex digest prefix is eight characters, and the final `op_...`
  label is still truncated by the operation length limit when positive.
- Any unknown operation policy other than `hash` behaves like `unknown`.
- `operation_type` from `resolve_operation_type(query, operation_name)` is one
  of `query`, `mutation`, `subscription`, or `unknown`. Invalid GraphQL syntax,
  missing queries, and ambiguous documents return `unknown`.
- `normalize_operation_type(...)` is more permissive for direct callers: it
  sanitizes any non-empty string and returns `unknown` only for missing or empty
  values.
- request `status` is `success` or `error`.
- `code` is sourced from a direct `GraphQLError.extensions["code"]` when
  present; wrapped/original exceptions are not unwrapped. Missing, empty, or
  punctuation-only codes become `unknown`, and codes are truncated to 64
  characters after normalization.
- `field_name` is `ParentType.field`, normalized and truncated with
  `GENERAL_MANAGER_GRAPHQL_METRICS_MAX_LABEL_LENGTH`. Missing, empty, or
  punctuation-only field names become `unknown`.
- Empty, whitespace-only, or punctuation-only labels normalize to `unknown`.

## Middleware behavior

`build_graphql_middleware()` always returns a list copied from
`graphene_settings.MIDDLEWARE`. When metrics or resolver timing are disabled,
the copied list is returned unchanged. When resolver timing is enabled, the
helper appends one `GraphQLResolverTimingMiddleware` unless the middleware class
or an instance is already present.

`GraphQLResolverTimingMiddleware` supports synchronous resolvers and awaitable
resolver results. It records duration for success and failure paths, records a
resolver error on exceptions, and re-raises resolver exceptions after recording.

## View behavior

`GeneralManagerGraphQLView` preserves Graphene-Django request parsing and
response shaping. Request objects, request payload data, `operationName`
handling, variable coercion, malformed query handling, batch ids, and
`show_graphiql` behavior are delegated to the parent `GraphQLView`.
GeneralManager does not narrow those concrete shapes: callers should treat
`request`, `data`, the encoded response object, and the outer shape of batched
response items as Graphene-Django contracts. The wrapper guarantees only that
`get_response(...)` returns `(encoded_response, status_code)` and adds the
metrics/rollback behavior described here.

When resolver timing is enabled, the view appends
`GraphQLResolverTimingMiddleware` after existing middleware so project
middleware runs first. If the base middleware is `None`, it returns a one-item
list. If timing is already present, it is not duplicated.

Request-level metrics are recorded after Graphene returns an execution result,
including GraphiQL requests that execute a query. Executions with any GraphQL
errors, including partial data plus errors, are labeled `error`; executions
without errors are labeled `success`. Request-level GraphQL errors without a
path keep Graphene-Django's `400` status behavior, while partial errors keep
status `200`. Mutation rollback follows Graphene-Django's
`MUTATION_ERRORS_FLAG` request attribute; the wrapper does not implement a
separate mutation detector.

Metrics backend failures, label-normalization failures, and error-code
extraction failures are logged at debug level and do not change the GraphQL
response. Non-metrics exceptions from Graphene-Django request parsing,
execution, formatting, encoding, rollback handling, and the calculation run
context still propagate normally.

## Performance notes

Resolver timing adds overhead because every field resolution is timed and
recorded. Keep it off unless you need resolver-level diagnostics.
