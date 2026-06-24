# Request Interfaces

`RequestInterface` lets a manager read and query data from a remote HTTP-style service while keeping the familiar GeneralManager API (`filter()`, `get()`, `exclude()`, `all()`, manager attribute access, and named collection operations).

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

1. Manager API: callers use `Project.filter(status="active")`, `Project.get(id=7)`, `Project.exclude(...)`, `Project.all()`, or `Project.Interface.query_operation("search", ...)`.
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
search_results = RemoteProject.Interface.query_operation(
    "list",
    status="active",
    page=1,
)
project = RemoteProject(id=42)

for item in active_projects:
    print(item.name, item.status)

print(project.name)
```

`RemoteProject.Interface.query_operation(name, **lookups)` is the public escape
hatch for named collection operations declared in `Meta.query_operations`. It
returns the same lazy `RequestBucket` shape as `filter()` and raises
`UnknownRequestOperationError` for unknown operation names, `NotImplementedError`
when the active query capability cannot execute named operations, and request
configuration/schema errors when planning or normalization fails.
`get_query_operation(None)` and `get_query_operation("")` resolve through
`default_query_operation`. If that default is not declared, the interface creates
a fallback query operation with `path=""` and the interface-level filters; unknown
non-default operation names raise `UnknownRequestOperationError`. Declared
operations inherit interface-level filters when their own `filters` value is
`None`; operations with an explicit filter mapping use that mapping instead.
`RequestField` attributes are collected across the interface inheritance chain,
and declaring create, update, or delete operations adds the matching request
mutation capability.

During manager-class creation, request capabilities validate the declaration
before the generated manager is published. A request interface must declare at
least one `RequestField` and a `detail` query operation. Rules must be
`Rule` instances and require at least one mutation operation. Configured
serializers must be callable, `auth_provider.apply` must be callable when an
auth provider is present, and `RequestRetryPolicy` values must be internally
consistent: `max_attempts >= 1`, non-negative base backoff, positive multiplier,
`max_backoff_seconds` no lower than base backoff when set, `0 <= jitter_ratio <=
1`, and idempotency-key header/factory configured together. Filter keys are
validated for both interface-level and operation-local filters; operation-local
filters take precedence for that operation when present, and declared operations
inherit interface filters only when `filters=None`. Every filter must produce a
remote fragment, custom compiler fragment, or local fallback predicate.
Operation-local filters cannot duplicate interface-level filter keys, operation
references must exist, an `exclude_remote_name` requires
`supports_exclude=True`, and exclude requests without remote exclude support
require a local fallback.

`filter()`, `exclude()`, and `all()` return a `RequestBucket`. While the bucket
is still lazy, chained calls compile into a new request plan for the same query
operation. Iterating a lazy bucket caches its fetched items but does not change
that chaining behavior. Buckets built from concrete items, such as slices,
unions, and `none()`, have no request plan; their follow-up `filter()` and
`exclude()` calls validate the same lookup declarations before filtering the
contained manager instances in memory. Concrete buckets still preserve the
source `operation_name` as metadata. Materialized in-memory filtering uses the
same request lookup operators as local fallback predicates. Missing attributes
simply do not match. Materialized `filter()` combines lookup keys with AND
semantics. Materialized `exclude()` removes an item when any supplied lookup
matches, so a missing attribute leaves the item in the result. Bare or unknown
lookup suffixes are exact matches; supported suffixes include comparisons,
`contains`, `icontains`, `in`, and `isnull`; incompatible comparisons return
`False`. `sort(key, reverse=False)` always materializes the bucket, accepts one
attribute name or a tuple of attribute names, and raises
`RequestBucketSortAttributeError` if an item lacks any requested sort attribute.
Tuple keys sort lexicographically by resolved values, nested attribute paths are
not parsed, and Python `TypeError` propagates for incomparable values.
Lazy `filter()`, `exclude()`, and `all()` compile a new request plan and can
raise the query capability's validation and planning errors, including unknown
or unsupported filters, unsupported exclude lookups, required local fallbacks,
fragment conflicts, and unsupported request locations. Any method that
materializes a lazy bucket, including iteration, `len()`, `count()`, `first()`,
`last()`, `get()`, indexing, slicing, membership, sorting, equality against a
concrete bucket, and concrete follow-up filters, can propagate request
execution, response-shape, permission, and validation errors from the underlying
request interface.

Every lazy request query compiles lookup maps into a `RequestQueryPlan`, records
a `request_query` dependency keyed by operation plus compiled filters/excludes,
and defers transport execution until the bucket materializes. Filter values are
stored as tuples by wrapping scalar keyword values in a one-item tuple; tuple
values are preserved as supplied so repeated lookups on the same key are
preserved. Remote
fragments merge into query params, headers, path params, or body; conflicting
duplicate keys in the same request location raise `RequestPlanConflictError`.
Custom filter compilers receive a `RequestFilterBinding` containing lookup key,
value, action, operation name, and filter spec. Local fallback predicates are
kept on the plan and applied after the response is fetched. A local-only filter
without fallback raises `RequestLocalFallbackRequiredError`; an unsupported
exclude raises `RequestExcludeNotSupportedError`; unsupported fragment locations
raise `UnsupportedRequestLocationError`.

Unioning request buckets or adding one compatible manager instance produces a
concrete item bucket. `bucket | other` keeps left items followed by right items
and does not deduplicate. Combining incompatible bucket types raises
`RequestBucketTypeMismatchError`; combining request buckets for different
manager classes raises `RequestBucketManagerMismatchError`. Bucket equality
compares manager class and operation name first. If both buckets have request
plans, it compares the plan plus compiled filters/excludes; otherwise it
materializes both sides and compares the ordered sequence of item identification
mappings.

Pickle-restored buckets keep operation and request-plan metadata but do not
execute a request during unpickling. Iteration after unpickling uses serialized
items; follow-up query methods on a restored bucket with a request plan compile a
new lazy request bucket. `get()` applies any extra filters and then requires
exactly one item, raising `RequestSingleItemRequiredError` otherwise. `count()`
always materializes first. After materialization the current count override
wins; lazy materialization installs the upstream `total_count` when available,
installs the local fallback item count when local predicates are applied, and
otherwise falls back to the number of materialized items. A constructor
`count_override` on a still-lazy bucket can therefore be replaced by the count
observed during request execution. Local fallback predicates reject partial
remote pages with `RequestLocalPaginationUnsupportedError` when the upstream
`total_count` does not match the number of returned items. Raw
response mappings stored on a bucket are restored into new manager instances
during construction or unpickling, so field reads use the cached payload instead
of immediately issuing a detail request. If a restored bucket has request-plan
metadata but no serialized items or raw payloads, iteration uses the empty
serialized item set; follow-up query methods can still compile a new lazy
request bucket. Python pickle errors for unserializable manager instances
propagate unchanged. Slices, unions, and `none()` set their count override to
their concrete item count, and `all()` returns a new bucket rather than `self`.
Membership checks use normal tuple containment over materialized manager
objects, not request lookup semantics.

Request-backed manager instances cache the raw response payload used for field
reads. `set_request_payload_cache(payload)` installs that mapping, and passing
`None` clears it. `resolve_payload_value(payload, field_name)` reads a declared
`RequestField` through its source path, returns a configured default for optional
missing values, applies the field normalizer when present, and raises
`MissingRequestPayloadFieldError` for required missing values. Undeclared names
resolve directly from the payload key. `extract_identification(payload)` applies
the same resolution rules for every name in `identification_fields`.

When a request-backed attribute is first read without a cached payload, the
`detail` operation is executed with the manager identification as path params.
The detail response must contain exactly one item; otherwise
`RequestSingleResponseRequiredError` is raised. The returned mapping is cached on
the interface instance and reused for subsequent field reads until the cache is
cleared or replaced. The uncached detail fetch emits observability operation
`request.read.detail` with service, operation, method, path, and identification
key metadata.

Request lifecycle hooks clone the declared interface for the generated manager.
When `input_fields` is empty, `Input` descriptors are collected by walking the
interface MRO from base classes to subclasses. The clone copies request fields,
filters, operations, transport/auth/retry configuration, serializers, and rules;
syncs configured capabilities; stores `attrs["_interface_type"]`; installs the
clone as `attrs["Interface"]`; and later assigns `_parent_class` during
post-create. These lifecycle hooks emit `request.pre_create` and
`request.post_create` observability events.

`execute_request_plan(plan)` is the shared execution hook used by request-backed
query and mutation capabilities. Plans whose action is `create`, `update`, or
`delete` dispatch through the corresponding mutation operation; other actions
use the query operation named on the plan. The hook forwards `plan.path_params`
to the transport as request identification, normalizes raw transport payloads
into `RequestQueryResult`, and applies `response_serializer` to each result item
when one is configured. Serializer outputs must be mappings; otherwise
`RequestSchemaError` is raised.

The query capability's direct `execute_plan()` wrapper emits observability
operation `request.query.execute` with service, operation, method, path, sorted
query/path/header/body key lists, and local predicate lookup keys before calling
`execute_request_plan(plan)`.

Create and update manager methods validate configured request `Rule` objects
before sending the request. Create rules see the submitted kwargs. Update rules
see existing payload values, the manager identification, and submitted kwargs
merged together; only the submitted kwargs are serialized into the update body.
`create_serializer` and `update_serializer` customize those request bodies. Both
create and update require exactly one response item and return the identification
extracted from that item; otherwise `RequestSingleResponseRequiredError` is
raised. Update also refreshes the instance payload cache with the merged response
payload. Delete sends only the identification-derived path parameters and returns
`None`. Request-backed create/update/delete accept `creator_id` and
`history_comment` for compatibility with the common manager API, but the request
capabilities ignore those values.

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
- retry policy through `Meta.retry_policy`
- capped backoff and jitter through `RequestRetryPolicy`
- structured logging and request IDs
- optional metrics and trace hooks through `RequestTransportConfig`
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
