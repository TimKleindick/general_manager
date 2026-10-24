# Expose and consume a RemoteAPI safely

Use `RemoteAPI` when a manager should expose a small REST contract to another
GeneralManager service. Use `RemoteManagerInterface` in the consuming service.
Enable websocket invalidation only when the client can maintain an authenticated
ASGI session; websocket events are hints that trigger a REST refetch.

## Configure the server

```python
from typing import ClassVar

from django.db.models import CharField

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.permission import AdditiveManagerPermission


class Project(GeneralManager):
    class Interface(DatabaseInterface):
        name = CharField(max_length=200)

    class Permission(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["isAuthenticated"]

    class RemoteAPI:
        enabled = True
        base_path = "/internal/gm"
        resource_name = "projects"
        allow_filter = True
        allow_detail = True
        allow_create = True
        allow_update = True
        websocket_invalidation = True
        protocol_version = "v1"
```

Install Django authentication and ASGI middleware before enabling the websocket
route. `request.user` controls REST read authorization; the protocol-version
header only selects the wire format and never authenticates a caller. Detail
reads return `403/permission_denied` when the current row is not readable,
denied fields are omitted without evaluating them, and a successful write may
return `items: []` when its resulting row is unreadable.

## Configure the client

```python
from general_manager.interface import RemoteManagerInterface, RequestField
from general_manager.manager import GeneralManager, Input


class RemoteProject(GeneralManager):
    class Interface(RemoteManagerInterface):
        id = Input(int)
        name = RequestField(str)

        class Meta:
            base_url = "https://project-service.example.com"
            base_path = "/internal/gm"
            remote_manager = "projects"
            protocol_version = "v1"
            websocket_invalidation_enabled = True
```

Client websocket connections must provide the same authenticated session as
the server expects; the default URL-only connection factory does not copy HTTP
cookies or authorization headers. Use a custom `connection_factory` when the
websocket library needs explicit credentials, and pass manager classes to
`RemoteInvalidationClient`.

## Use bounded queries

Every RemoteAPI query is finite: omitted controls mean `page=1` and
`page_size=10`. Configure positive
`GENERAL_MANAGER["REMOTE_API_DEFAULT_PAGE_SIZE"]` and
`GENERAL_MANAGER["REMOTE_API_MAX_PAGE_SIZE"]` values when the defaults need to
change; the default cannot exceed the maximum, and an oversized, non-positive,
or non-integer request returns HTTP 400 before execution. Request later pages
explicitly when more rows are needed.

Read permissions are applied before local ordering, counting, and slicing. An
incomplete upstream response that needs per-instance checks omits
`total_count`; a request-backed manager with multiple permission alternatives
rejects global pagination or ordering because the server cannot prove a global
page. RemoteManager forwards matching page controls upstream and rejects
incomplete or overlong pages rather than presenting them as complete.

See the [RemoteAPI concept](../concepts/interfaces/remote_api.md), the
[end-to-end cookbook](../examples/remote_manager_interface_end_to_end.md), and
the [interface and GraphQL API references](../api/interface.md)
for the complete callable contracts.
