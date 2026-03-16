# Request Interface End-to-End

This recipe shows the recommended production-facing request-interface shape:

- class-attribute `RequestField` declarations
- `Interface.Meta` for request config
- built-in `UrllibRequestTransport`
- provider-style auth with `BearerTokenAuthProvider`
- framework retry policy with `RequestRetryPolicy`
- outbound mutation shaping with `FieldMappingSerializer`

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
