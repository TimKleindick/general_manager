# Request Interfaces

`RequestInterface` lets a manager read and query data from a remote HTTP-style service while keeping the familiar GeneralManager API (`filter()`, `exclude()`, `all()`, manager attribute access, and named collection operations).

If both services use GeneralManager, prefer `RemoteManagerInterface` for the client side and `RemoteAPI` on the server side. That layer builds on top of `RequestInterface` and synthesizes the standard GeneralManager REST contract for you.

Unlike `DatabaseInterface`, a request interface does not create a Django model. Instead, you declare:

- manager fields as class attributes
- request configuration inside `Interface.Meta`
- explicit query and mutation operations
- how a compiled request plan is executed, usually through a shared transport

This keeps the public API familiar while making remote-service behavior explicit and strict.

## When to use a request interface

Use `RequestInterface` when:

- the source of truth lives in another service
- you want manager-style reads and queries without mirroring that service into your database
- the upstream API has a stable resource model such as `projects`, `documents`, or `work_orders`

Do not use it as a generic ad hoc HTTP client. Request interfaces are resource-first and declaration-driven.

## Mental model

A request-backed manager has five layers:

1. Manager API: callers use `Project.filter(status="active")`, `Project.exclude(...)`, `Project.all()`, or `Project.Interface.query_operation("search", ...)`.
2. Field schema: `RequestField` class attributes define the remote resource shape exposed by the manager.
3. Filter and operation config: `Interface.Meta` declares filters, query operations, mutation operations, auth provider, retry policy, and serializers.
4. Request plan: GeneralManager builds a `RequestQueryPlan` with method, path, query params, headers, body, path params, and optional local predicates.
5. Execution hook: the interface hands that plan to a shared transport, which turns it into a real HTTP call.

The key design point is that callers never pass raw HTTP details. They only use declared manager filters and operations.

## Core pieces

### `RequestField`

`RequestField` declares a manager attribute and where it comes from in a remote payload.

```python
RequestField(int)
RequestField(str, source="displayName")
RequestField(str, source=("owner", "name"))
```

Common options:

- `field_type`: expected Python type
- `source`: payload key or dotted/nested path
- `default`: fallback for optional fields
- `is_required`: whether missing payload data should raise an error
- `normalizer`: optional payload-to-Python conversion

### `RequestFilter`

`RequestFilter` maps a GeneralManager lookup to remote request semantics.

```python
RequestFilter(remote_name="state", value_type=str)
RequestFilter(remote_name="modifiedAfter", value_type=datetime)
RequestFilter(remote_name="q", location="body", value_type=str)
```

Important options:

- `remote_name`: upstream parameter name
- `location`: where the value goes: `"query"`, `"headers"`, `"path"`, or `"body"`
- `value_type`: strict input validation
- `serializer`: optional value transformation before sending
- `supports_exclude`: whether `exclude()` is safe for this filter
- `exclude_remote_name`: upstream negation parameter if `exclude()` uses a different key
- `allow_local_fallback`: opt-in client-side filtering after the remote response
- `operation_names`: restrict a filter to specific collection operations
- `compiler`: custom request-plan compiler for non-standard cases

### `RequestQueryOperation`

`RequestQueryOperation` declares a named remote operation.

```python
RequestQueryOperation(name="list", method="GET", path="/projects")
RequestQueryOperation(name="detail", method="GET", path="/projects/{id}")
RequestQueryOperation(name="search", method="POST", path="/projects/search")
```

Use operations when the upstream service exposes multiple collection shapes, for example:

- a normal list endpoint
- a POST-based search endpoint
- a special status report endpoint

### Shared transport

`RequestInterface` now supports a first-class shared transport path. In the common case you declare:

- `transport`: an object implementing `SharedRequestTransport` or the `RequestTransport` protocol
- `transport_config`: base URL, timeout, retry policy, and optional response normalizer
- `auth_provider`: a provider-style hook on `Interface.Meta`

Then the default `execute_request_plan()` implementation delegates to that transport.

For most HTTP integrations, start with the built-in `UrllibRequestTransport`. Keep a custom `SharedRequestTransport` subclass only when the upstream service needs special request signing, non-JSON wire behavior, or custom response parsing.

The shared transport is responsible for:

- building the outbound request from the `RequestQueryPlan`
- merging static operation query params, headers, and body fragments
- enforcing timeout configuration
- applying auth through `Meta.auth_provider`
- applying framework retry policy from `Meta.retry_policy`
- adding idempotency keys for retried non-idempotent requests when configured
- normalizing transport responses into `RequestQueryResult`
- mapping upstream status failures into stable request exceptions

```python
RequestQueryResult(
    items=({"id": 1, "name": "Alpha"},),
    total_count=1,
)
```

## Minimal example

The example below shows a manager backed by a remote project service.

For a fuller cookbook-style version of the same pattern, see the [request interface end-to-end recipe](../../examples/request_interface_end_to_end.md).

```python
from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from general_manager.interface import (
    BearerTokenAuthProvider,
    FieldMappingSerializer,
    RequestField,
    RequestFilter,
    RequestInterface,
    RequestMutationOperation,
    RequestRetryPolicy,
    RequestTransportConfig,
    RequestQueryOperation,
    UrllibRequestTransport,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input


class RemoteProject(GeneralManager):
    class Interface(RequestInterface):
        id = Input(type=int)

        name = RequestField(str)
        status = RequestField(str, source="state")
        updated_at = RequestField(datetime, source="modifiedAt")

        class Meta:
            filters: ClassVar[dict[str, RequestFilter]] = {
                "status": RequestFilter(
                    remote_name="state",
                    value_type=str,
                    supports_exclude=True,
                    exclude_remote_name="state_not",
                ),
                "name__icontains": RequestFilter(
                    remote_name="search",
                    value_type=str,
                ),
                "updated_at__gte": RequestFilter(
                    remote_name="modifiedAfter",
                    value_type=datetime,
                    serializer=lambda value: value.isoformat(),
                ),
                "page": RequestFilter(remote_name="page", value_type=int),
                "page_size": RequestFilter(remote_name="pageSize", value_type=int),
            }
            query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                "detail": RequestQueryOperation(
                    name="detail",
                    method="GET",
                    path="/projects/{id}",
                ),
                "list": RequestQueryOperation(
                    name="list",
                    method="GET",
                    path="/projects",
                ),
            }
            create_operation = RequestMutationOperation(
                name="create",
                method="POST",
                path="/projects",
            )
            update_operation = RequestMutationOperation(
                name="update",
                method="PATCH",
                path="/projects/{id}",
            )
            transport = UrllibRequestTransport()
            transport_config = RequestTransportConfig(
                base_url="https://service.example.com/api",
                timeout=10,
            )
            auth_provider = BearerTokenAuthProvider(token=lambda: "replace-me")
            retry_policy = RequestRetryPolicy(
                max_attempts=3,
                base_backoff_seconds=0.25,
                max_backoff_seconds=2.0,
                jitter_ratio=0.25,
            )
            create_serializer = FieldMappingSerializer(
                {"name": "name", "state": "status"}
            )
            update_serializer = FieldMappingSerializer({"state": "status"})
```

Usage:

```python
active_projects = RemoteProject.filter(status="active", page=1, page_size=50)
project = RemoteProject(id=42)

for item in active_projects:
    print(item.name, item.status)

print(project.name)
```

## Practical examples

### Example 1: standard list filters

A typical GET list endpoint maps directly from manager lookups to query parameters.

```python
RemoteProject.filter(
    status="active",
    name__icontains="alpha",
    updated_at__gte=datetime(2026, 3, 1, 0, 0, 0),
    page=2,
    page_size=25,
)
```

This compiles into a request plan roughly like:

```python
RequestQueryPlan(
    operation_name="list",
    action="filter",
    method="GET",
    path="/projects",
    query_params={
        "state": "active",
        "search": "alpha",
        "modifiedAfter": "2026-03-01T00:00:00",
        "page": 2,
        "pageSize": 25,
    },
)
```

### Example 2: safe `exclude()`

Only filters that explicitly support negation may be used with `exclude()`.

```python
inactive_projects = RemoteProject.exclude(status="inactive")
```

If a filter does not declare `supports_exclude=True`, `exclude()` raises an error instead of guessing.

### Example 3: operation-specific search

Some APIs use a different endpoint for full-text search. In that case, declare a named operation and operation-specific filters.

```python
class RemoteProject(GeneralManager):
    class Interface(RequestInterface):
        id = Input(type=int)

        name = RequestField(str)

        class Meta:
            query_operations = {
                "search": RequestQueryOperation(
                    name="search",
                    method="POST",
                    path="/projects/search",
                    filters={
                        "query": RequestFilter(
                            remote_name="q",
                            location="body",
                            value_type=str,
                        ),
                        "page": RequestFilter(
                            remote_name="page",
                            location="body",
                            value_type=int,
                        ),
                    },
                ),
            }
```

Usage:

```python
matches = RemoteProject.Interface.query_operation(
    "search",
    query="tower crane",
    page=1,
)
```

This keeps the manager API high-level while allowing endpoint-specific request shapes.

### Example 4: operation-restricted filters

You can keep a filter available only on selected operations.

```python
filters = {
    "archived": RequestFilter(
        remote_name="archived",
        value_type=bool,
        operation_names=frozenset({"list"}),
    ),
}
```

`RemoteProject.filter(archived=True)` works on the `list` operation, but trying to use the same filter on another operation raises an error.

### Example 5: local fallback filtering

Some upstream APIs cannot express every filter server-side. You can allow a strictly opt-in local fallback:

```python
filters = {
    "local_name__icontains": RequestFilter(
        value_type=str,
        allow_local_fallback=True,
    ),
}
```

Usage:

```python
RemoteProject.filter(local_name__icontains="alpha")
```

GeneralManager will fetch the declared remote operation first and then apply the predicate locally.

Important caveat:

- local fallback is intentionally strict
- it is not a substitute for full remote filtering support
- partial paginated pages are rejected when local fallback would make counts or page semantics incorrect

## Detail reads

Manager attribute access can lazily load a detail endpoint. If a manager is created with only its identification fields, GeneralManager resolves attributes by executing the `"detail"` request operation.

```python
project = RemoteProject(id=42)
print(project.name)
```

This works when:

- the interface declares a `"detail"` operation
- `execute_request_plan()` returns exactly one item for that operation

If the detail operation returns zero or multiple items, GeneralManager raises an explicit response-shape error instead of pretending the payload is valid.

## Strictness and failure behavior

Request interfaces intentionally fail early.

You should expect errors when:

- a caller uses an undeclared filter key
- a filter value has the wrong type
- `exclude()` is used on a filter that does not explicitly support negation
- a filter is restricted to a different operation
- two filters try to write conflicting values into the same request-plan location
- a required payload field is missing
- local fallback is attempted on a partial remote page

This is by design. Remote APIs vary too much for safe implicit behavior.

## Supported lookup vocabulary

Request filters currently support a bounded lookup vocabulary:

- `exact`
- `in`
- `contains`
- `icontains`
- `gt`
- `gte`
- `lt`
- `lte`
- `isnull`

You still declare every supported lookup explicitly in the `filters` mapping, for example `name__icontains` or `updated_at__gte`.

## Production guidance

The current request interface gives you the declaration, planning, and shared transport layer. For production use, configure your shared transport path to handle:

- provider-based authentication and token refresh
- mandatory timeouts
- retry policy for idempotent operations through `Meta.retry_policy`
- structured logging and request IDs
- response normalization and schema checks
- rate-limit handling
- secret masking in logs and errors

If multiple managers talk to the same upstream service, keep one shared transport and auth provider rather than duplicating request code inside each interface.

Relevant public transport and error types are available from `general_manager.interface`, including:

- `UrllibRequestTransport`
- `BearerTokenAuthProvider`
- `HeaderApiKeyAuthProvider`
- `QueryApiKeyAuthProvider`
- `BasicAuthProvider`
- `FieldMappingSerializer`
- `SharedRequestTransport`
- `RequestTransportConfig`
- `RequestRetryPolicy`
- `RequestTransportRequest`
- `RequestTransportResponse`
- `RequestMutationOperation`
- `RequestRemoteError`
- `RequestTransportError`
- `RequestAuthenticationError`
- `RequestAuthorizationError`
- `RequestNotFoundError`
- `RequestConflictError`
- `RequestRateLimitedError`
- `RequestServerError`

Mutation-specific configuration also lives in `Interface.Meta`:

- `create_operation`
- `update_operation`
- `delete_operation`
- `rules`
- `create_serializer`
- `update_serializer`
- `response_serializer`

For production use, configure your shared transport path to handle:

- provider-based authentication and token refresh
- mandatory timeouts
- retry policy for idempotent operations through `Meta.retry_policy`
- capped backoff and jitter through `RequestRetryPolicy`
- structured logging and request IDs
- optional metrics and trace hooks through `RequestTransportConfig`
- response normalization and schema checks
- rate-limit handling
- secret masking in logs and errors

## Current limitations

The request interface is intentionally narrow in v1.

Current limitations include:

- no generic arbitrary endpoint invocation from managers
- no ORM-style universal filtering across all remote APIs
- retry policy, metrics, and trace hooks are framework-managed only for transports that go through `SharedRequestTransport.execute()`
- request transports are normalized, but you still own service-specific provider auth logic and response shaping
- observability is structured and sanitized, but metric/tracing backend wiring depends on the host application

## Troubleshooting

- `RequestConfigurationError` at class definition time usually means `Interface.Meta` contains an invalid `auth_provider`, `retry_policy`, serializer, or legacy top-level request config.
- `RequestSchemaError` means the transport or serializer returned the wrong payload shape. Check the upstream JSON body, `response_normalizer`, and `response_serializer`.
- If retry behavior seems missing, confirm the integration uses `SharedRequestTransport.execute()` and not a custom transport path that bypasses it.
- If auth is missing, check both `Meta.auth_provider` and `transport_config.auth_provider`; the interface-level provider takes precedence.

## Recommended pattern

For most integrations, this pattern works well:

1. Start with one resource-oriented manager such as `RemoteProject`.
2. Declare a small, explicit set of `RequestField` class attributes.
3. Declare only the filters the upstream service truly supports.
4. Put filters, operations, transport config, auth provider, retry policy, rules, and serializers in `Interface.Meta`.
5. Add a `"detail"` and `"list"` operation first.
6. Add named operations such as `"search"` only when the upstream API has a genuinely different endpoint shape.
7. Keep `transport.send()` thin and move shared HTTP/auth/error logic into a reusable transport helper.

That keeps the GeneralManager API clean without hiding the real constraints of the remote service.
