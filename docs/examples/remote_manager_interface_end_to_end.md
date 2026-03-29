# Remote Manager Interface End-to-End

`RemoteManagerInterface` lets one GeneralManager-based service consume another service's opt-in REST exposure without hand-writing request operations.

The server side opts in per manager with `RemoteAPI`. The client side uses `RemoteManagerInterface` with `base_url`, `base_path`, and `remote_manager`.

## Server

```python
from typing import ClassVar

from django.db.models import CharField

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import AdditiveManagerPermission


class Project(GeneralManager):
    class Interface(DatabaseInterface):
        name = CharField(max_length=200)
        status = CharField(max_length=50)

    class Permission(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        __create__: ClassVar[list[str]] = ["public"]
        __update__: ClassVar[list[str]] = ["public"]
        __delete__: ClassVar[list[str]] = ["public"]

    class RemoteAPI:
        enabled = True
        base_path = "/internal/gm"
        resource_name = "projects"
        allow_filter = True
        allow_detail = True
        allow_create = True
        allow_update = True
        allow_delete = True
        websocket_invalidation = True
        protocol_version = "v1"
```

This generates these endpoints:

- `POST /internal/gm/projects/query`
- `GET /internal/gm/projects/<id>`
- `POST /internal/gm/projects`
- `PATCH /internal/gm/projects/<id>`
- `DELETE /internal/gm/projects/<id>`

If `websocket_invalidation = True`, the service also exposes:

- `WS /internal/gm/ws/projects?version=v1`

The websocket channel only emits invalidation events. Clients still refetch over REST.

## Client

```python
from general_manager.interface import (
    RemoteManagerInterface,
    RequestField,
    UrllibRequestTransport,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input


class RemoteProject(GeneralManager):
    class Interface(RemoteManagerInterface):
        id = Input(type=int)
        name = RequestField(str)
        status = RequestField(str)

        class Meta:
            base_url = "https://project-service.example.com"
            base_path = "/internal/gm"
            remote_manager = "projects"
            protocol_version = "v1"
            websocket_invalidation_enabled = True
            transport = UrllibRequestTransport()
```

Optional websocket helpers on the client interface:

```python
ws_url = RemoteProject.Interface.get_websocket_invalidation_url()

RemoteProject.Interface.handle_invalidation_event(
    {
        "protocol_version": "v1",
        "base_path": "/internal/gm",
        "resource_name": "projects",
        "action": "update",
        "identification": {"id": 42},
        "event_id": "evt-123",
    }
)
```

`handle_invalidation_event()` only invalidates local remote-query caches when the
event matches the interface's `protocol_version`, `base_path`, and
`remote_manager`.

For direct runtime invalidation, use `RemoteInvalidationClient` with manager
classes, not interface classes:

```python
import asyncio

from general_manager.api import RemoteInvalidationClient


async def main() -> None:
    client = RemoteInvalidationClient([RemoteProject])
    await client.connect()
    try:
        await client.run()
    finally:
        await client.close()


asyncio.run(main())
```

`RemoteInvalidationClient` resolves `RemoteProject.Interface` internally, opens
the websocket subscription using `get_websocket_invalidation_url()`, and
dispatches incoming invalidation events back through
`handle_invalidation_event(...)`.

## Usage

```python
active_projects = RemoteProject.filter(status="active")
project = RemoteProject(id=42)

created = RemoteProject.create(name="Gamma", status="active")
updated = created.update(status="inactive")
updated.delete()

print(project.name)
```

## Notes

- Exposure is opt-in. Managers without `RemoteAPI.enabled = True` are not reachable.
- `base_path` defaults to `"/gm"` on both server and client.
- Protocol versions must match exactly in `v1`.
- Websocket invalidation is optional and minimal by design:

```json
{
  "protocol_version": "v1",
  "base_path": "/internal/gm",
  "resource_name": "projects",
  "action": "update",
  "identification": {"id": 42}
}
```
