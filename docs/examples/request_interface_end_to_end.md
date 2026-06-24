# Request Interface End-to-End

This recipe shows the recommended production-facing request-interface shape:

- class-attribute `RequestField` declarations
- `Interface.Meta` for request config
- built-in `UrllibRequestTransport`
- provider-style auth with `BearerTokenAuthProvider`
- framework retry policy with `RequestRetryPolicy`
- outbound mutation shaping with `FieldMappingSerializer`

Application code usually declares fields, filters, query/mutation operations,
transport config, auth, and retry policy. Integration code usually implements a
custom `SharedRequestTransport.send()` method, a `RequestAuthProvider`, or
metrics/tracing backends.

```python
from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from general_manager.interface import (
    BearerTokenAuthProvider,
    FieldMappingSerializer,
    RequestField,
    RequestFilter,
    RequestInterface,
    RequestMutationOperation,
    RequestQueryOperation,
    RequestRetryPolicy,
    RequestTransportConfig,
    UrllibRequestTransport,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input


class Project(GeneralManager):
    class Interface(RequestInterface):
        id = Input(type=int)

        name = RequestField(str, source="displayName")
        status = RequestField(str, source="state")
        updated_at = RequestField(datetime, source="modifiedAt")

        class Meta:
            filters: ClassVar[dict[str, RequestFilter]] = {
                "status": RequestFilter(remote_name="state", value_type=str),
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
            transport = UrllibRequestTransport()
            transport_config = RequestTransportConfig(
                base_url="https://service.example.com/api",
                timeout=5,
            )
            auth_provider = BearerTokenAuthProvider(
                token=lambda: "replace-me"
            )
            retry_policy = RequestRetryPolicy(
                max_attempts=2,
                base_backoff_seconds=0.25,
                max_backoff_seconds=1.0,
                jitter_ratio=0.25,
            )
            create_serializer = FieldMappingSerializer(
                {"displayName": "name", "state": "status"}
            )
```

Typical usage:

```python
active_projects = Project.filter(status="active")
project = Project(id=1)
created = Project.create(
    name="Created Example",
    status="active",
    ignore_permission=True,
)

print(active_projects[0].name)
print(project.name)
print(created.id)
```

Notes:

- `Project.filter(...)` compiles into a request plan and runs through `UrllibRequestTransport`.
- `Project(id=1).name` lazily resolves through the `"detail"` operation.
- `Project.create(...)` uses `FieldMappingSerializer` to shape the outbound JSON body.
- request input values such as `id` are readable through the normal manager attribute path as well as `identification`
- `ignore_permission=True` is only needed when your project enables mutation permission checks and you want a minimal standalone example.

## Contracts to rely on

- `RequestQueryOperation` is an alias of `RequestOperation` for read/query declarations. `RequestQueryPlan` is an alias of `RequestPlan` for readability and backward compatibility.
- `RequestOperation` requires `name` and `path`. Optional fields are `method`, `collection`, `filters`, `metadata`, `static_query_params`, `static_headers`, `static_body`, and `timeout`. Mutation serializers such as `create_serializer` live on `Interface.Meta`; response normalizers live on `RequestTransportConfig`.
- `RequestField(source=...)` accepts either a dotted string (`"owner.name"`) or a tuple path (`("owner", "name")`). Missing required payload values surface as request payload errors; optional fields can declare `default=...`.
- `FieldMappingSerializer` maps `{remote_key: local_key}`. Missing local keys raise `KeyError`, and extra local keys are ignored.
- Auth providers return a new `RequestTransportRequest`; they do not mutate the request passed to `apply()`.
- `RequestRetryPolicy.compute_backoff_seconds(retry_count=...)` uses a 1-based retry count. By default only `GET`, `HEAD`, `OPTIONS`, and `DELETE` retry. To retry `POST` or `PATCH`, set `retry_non_idempotent_methods=True` and configure an idempotency key header/factory.
- `RequestTransportResponse.payload` must be a decoded mapping or a list of mappings. The default normalizer turns a mapping into a one-item `RequestQueryResult` and preserves status code, retry count, response headers, and request id in result metadata.
- HTTP status errors map to stable exceptions: `401` to `RequestAuthenticationError`, `403` to `RequestAuthorizationError`, `404` to `RequestNotFoundError`, `409` to `RequestConflictError`, `429` to `RequestRateLimitedError`, and `5xx` to `RequestServerError`.
- `RequestSerializer` receives one resolved value and returns a serialized value. `RequestValidator` receives one lookup value and should raise when invalid. A `RequestResponseNormalizer` receives a raw response plus interface, operation, and plan, and must return `RequestQueryResult`.
