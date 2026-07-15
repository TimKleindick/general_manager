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

REST routes are generated during startup in query, item, then create order for
the operations you enabled. Duplicate `(base_path, resource_name)` exposures are
rejected while the RemoteAPI registry is built, before new routes from that pass
are appended. Repeated startup registration skips already marked query, item, and
create routes; cleanup removes only GeneralManager-marked REST routes and leaves
user-defined URL patterns intact. Route registration and cleanup both return
without changes when `ROOT_URLCONF` is unset. `base_path` defaults to `/gm`, is
normalized with one leading slash and no trailing slash, rejects `/`, empty
paths, double-slash segments, and non-slug path segments. `resource_name` strips
surrounding slashes before lowercase-slug validation.

Every REST view accepts the `X-General-Manager-Protocol-Version` header. If the
header is present, it must match `RemoteAPI.protocol_version`; omitted headers are
accepted. `POST /query` reads an object body with optional `filters`, `excludes`,
`ordering`, `page`, and `page_size`. The view starts with `manager_cls.all()`,
applies `filter(**filters)` only when `filters` is truthy, applies
`exclude(**excludes)` only when `excludes` is truthy, then applies ordering,
computes `total_count` before pagination, and returns a JSON envelope with
`items`, `metadata`, and `total_count`. `ordering` may be one field name or a
list of field names; a leading `-` sorts that field descending, and multi-field
ordering is applied in reverse order through chained bucket sorts. Pagination is
applied only when both `page` and `page_size` are positive integers; other
values are left in response metadata but do not slice the bucket.
`GET /<id>` returns one item, `PATCH /<id>` requires an object body and returns
the updated item, `DELETE /<id>` returns an empty `items` list, and
`POST /<resource>` creates an item and returns HTTP `201`. URL identifiers are
coerced to `int` only when the manager interface `id` input type is exactly
`int`; subclasses or other numeric types leave URL identifiers as strings.
Disabled operations and unsupported item methods return HTTP `405` without
constructing a manager for that request. Coercion failures are mapped to the
standard remote error envelope.

Success envelopes include `items`, `metadata.protocol_version`,
`metadata.request_id`, response header `X-Request-ID`, optional metadata extras
such as query controls, and `total_count` only when supplied by the endpoint.
RemoteAPI error responses use sanitized messages in a stable JSON object with
`error`, `error_code`, and `metadata`; `details` may be included for structured
errors. `ObjectDoesNotExist` maps to `404/not_found`, `PermissionError` to
`403/permission_denied`, `ValidationError` to `400/validation_error`,
`RuntimeError` to `500/internal_error`, and caught `AttributeError`,
`LookupError`, `RemoteAPIConfigurationError`, `TypeError`, `ValueError`, and
`RemoteAPIRequestError` subclasses map to `400/invalid_request`. The
`X-Request-ID` response header matches the metadata request id. Incoming
`X-Request-ID` is reused when supplied; otherwise GeneralManager generates a
deterministic id for item routes or a UUID-backed id for collection routes.

If `websocket_invalidation = True`, the service also exposes:

- `WS /internal/gm/ws/projects?version=v1`

The websocket channel only emits invalidation events. Clients still refetch over REST.
GeneralManager installs these routes during app startup through
`ensure_remote_invalidation_route()`. The helper reads `ASGI_APPLICATION`,
preserves existing websocket routes, skips duplicate RemoteAPI routes for the
same `(base_path, resource_name)`, and returns without changes when Channels or
ASGI settings are unavailable. `clear_remote_invalidation_routes()` removes only
generated RemoteAPI websocket routes and rebuilds the websocket router.
Generated routes use `<base_path>/ws/<resource_name>/?` with an optional trailing
slash. Clearing detects only routes marked by GeneralManager's route metadata,
so GraphQL and user-defined websocket routes keep their relative order.
Repeated route installation is idempotent for the resolved `(base_path,
resource_name)` pair; if a later configuration changes the same pair, the
existing route is preserved. Missing or non-mutable `websocket_urlpatterns` are
replaced with a new list during installation, while clearing returns without
changes unless the patterns are mutable.

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

Remote query calls compile into the generated `POST <base_path>/<remote_manager>/query`
endpoint. Normal filters are placed under `filters`, excludes under `excludes`,
and single-value lookups are serialized as scalars while multi-value lookups are
serialized as lists. The reserved filters `ordering`, `page`, and `page_size`
become top-level query body controls instead of remote filter keys; only their
first supplied value is used, and the capability does not add extra validation
beyond the normal request lookup/input casting path. Reserved names are special
only in `filters`; the same names in `exclude(...)` remain ordinary exclude keys.
Operation names are resolved through the interface's declared query operations.
`operation_name=None` and `operation_name=""` are intentional default-operation
forms and resolve through the interface default operation, normally `list`.
`operation_name="list"` also omits the `operation` body key. Operation names
other than `list`, for example `operation_name="search"`, add
`"operation": "search"` to the body. Lookup names and lookup suffixes are
forwarded to the remote service;
the remote manager query capability does not reject malformed lookup names
locally. The protocol version is declared with `Meta.protocol_version`, defaults
to `"v1"` when omitted, must resolve to a non-empty string, and is sent by the
generated operation headers as `X-General-Manager-Protocol-Version`.
`websocket_invalidation_enabled` is coerced with `bool(...)` during interface
class setup. The `RemoteManagerInterface` class hook configures direct
subclasses only; indirect subclasses inherit the already generated operations
unless they also inherit directly from `RemoteManagerInterface`.

Lookup values are normalized before the remote payload is built. Regular keyword
arguments become one-item tuples. If a lookup map is supplied directly, tuple
length controls serialization: one item becomes a scalar, zero or multiple items
become a list. Reserved controls use only the first tuple value, so an empty
reserved-control tuple is omitted, and a string value such as `ordering="-name"`
is kept as the scalar string `"-name"`.

For example:

```python
RemoteProject.filter(
    status="active",
    id__in=(1, 2),
    ordering="-name",
    page=1,
    page_size=50,
).exclude(status="archived")
```

compiles to this request body:

```json
{
  "filters": {"status": "active", "id__in": [1, 2]},
  "excludes": {"status": "archived"},
  "ordering": "-name",
  "page": 1,
  "page_size": 50
}
```

The compiled plan is tracked as a request-query dependency before a lazy
`RequestBucket` is returned.

Remote query responses may be a JSON list of object items or an object envelope:

```json
{
  "items": [{"id": 42, "name": "Gamma", "status": "active"}],
  "total_count": 1,
  "metadata": {"page": 1}
}
```

The client normalizer turns lists and envelope `items` into the
`RequestQueryResult.items` tuple; an envelope that omits `items` returns an empty
tuple. It carries an optional integer `total_count` and merges metadata in this
order: plan metadata, transport metadata, transport `status_code`, transport
`retry_count` defaulting to `0`, transport `x-request-id` as
`metadata["request_id"]`, then envelope `metadata`. Later sources replace earlier
values when keys collide. Non-object payloads, non-list `items`, non-object item
entries, non-object `metadata`, and non-integer `total_count` raise
`RequestSchemaError`. Envelopes containing an `error` key raise
`RequestConfigurationError` because remote errors are not mapped onto local
request operations.

Server-side RemoteAPI views require request bodies to decode to JSON objects.
Empty bodies are treated as `{}`. Malformed JSON and valid JSON values such as
arrays, strings, numbers, booleans, or `null` are returned as remote API request
errors. Generated REST URL patterns are marked internally so repeated startup
registration can skip existing query/item/create routes and cleanup can remove
only GeneralManager-generated RemoteAPI routes.

If a `RemoteManagerInterface` subclass supplies its own `transport_config`, the
remote-manager class setup keeps its timeout, auth provider, retry policy,
metrics backend, and trace backend, while replacing the base URL and response
normalizer with the values required by the remote-manager REST contract.

The public aliases `RemoteManagerOperationName`, `RemoteManagerLookupValues`,
`RemoteManagerLookupMap`, `RemoteManagerQueryControls`, and
`RemoteManagerQueryPayload` can be imported from
`general_manager.interface.capabilities.remote_manager` when annotating helpers
around this query compiler. `RemoteManagerLookupValues` is
`tuple[object, ...]` because lookup values may be any request-serializable
object after the normal request filter casting path. `RemoteManagerLookupMap` is
`Mapping[str, RemoteManagerLookupValues]`. `RemoteManagerQueryPayload` is the
POST body shape: it always includes `filters: dict[str, object]` and
`excludes: dict[str, object]`, and may include `ordering: object`,
`page: object`, `page_size: object`, and `operation: str` depending on the
rules above.

| Input location | Key shape | Full request body |
| --- | --- | --- |
| `filter(status="active")` | normal filter | `{"filters": {"status": "active"}, "excludes": {}}` |
| `filter(id__in=(1, 2))` | multi-value filter | `{"filters": {"id__in": [1, 2]}, "excludes": {}}` |
| `filter(ordering="-name")` | reserved filter control | `{"filters": {}, "excludes": {}, "ordering": "-name"}` |
| `filter(page=1, page_size=50)` | reserved pagination controls | `{"filters": {}, "excludes": {}, "page": 1, "page_size": 50}` |
| `exclude(page=1)` | reserved name in excludes | `{"filters": {}, "excludes": {"page": 1}}` |

`RemoteManagerInterface` subclasses must declare at least one `RequestField`
attribute such as `name = RequestField(str)`. An empty field declaration,
missing `remote_manager`, missing `base_url`, an effective blank protocol
version, non-HTTP(S) URL, invalid lowercase slug, or invalid `base_path` raises
`RequestConfigurationError` when the class is defined.
Unknown query operation names raise `UnknownRequestOperationError` before a
request bucket is returned.

`base_url` must parse as `http://...` or `https://...` with a host. Paths,
ports, credentials, query strings, fragments, and trailing slashes are not
rejected by the client metadata validator. `base_path` is optional on the
client and defaults to `/gm` when omitted. Remote manager names and base path
segments use the slug grammar `^[a-z0-9]+(?:-[a-z0-9]+)*$`: lowercase ASCII
letters, digits, and single hyphens between alphanumeric runs. Underscores,
uppercase letters, leading/trailing hyphens, and doubled hyphens are invalid.
`base_path` examples:
`/internal/gm` and `/gm-v1/projects` are valid; `/`, `internal/gm`,
`/Internal/gm`, and `/internal//gm` are invalid.

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
`remote_manager`. It returns `True` after requesting invalidation for the parent
manager, returns `False` for non-matching events, ignores `action`,
`identification`, and `event_id`, and propagates cache backend errors.
`get_websocket_invalidation_url()` derives the client URL from `base_url`,
normalized `base_path`, and `remote_manager`: class creation has already
validated `base_url` as HTTP(S), `https` becomes `wss`, `http` becomes `ws`, any
path prefix already present on `base_url` is stripped of trailing slashes and
kept before `<base_path>/ws/<remote_manager>`, and the protocol version is added
as the `version` query parameter. Query and fragment components on `base_url`
are not preserved in the websocket URL. The helper itself does not check
`websocket_invalidation_enabled`; `RemoteInvalidationClient` performs that check
before connecting.

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

`RemoteInvalidationClient` resolves `RemoteProject._interface` first and then
`RemoteProject.Interface`, opens the websocket subscription using
`get_websocket_invalidation_url()`, and dispatches incoming invalidation events
back through the synchronous `handle_invalidation_event(...)` method. Event field
semantics are owned by `RemoteManagerInterface.handle_invalidation_event(...)`;
the client forwards the decoded mapping it received from the websocket.

Pass manager classes, not interface classes. Every manager must use
`RemoteManagerInterface` with websocket invalidation enabled; otherwise
construction raises `RemoteInvalidationConfigurationError`. Managers that share
the same websocket URL reuse one connection, and each matching interface receives
the decoded event in first-seen manager order. Duplicate interface classes for
the same URL are dispatched once, keeping the first occurrence. `listen_once()`
waits for one event across all configured URLs and returns the number of
interfaces whose handler returned a truthy value; handlers that return `None`
are not counted. The custom connection protocol is structural: a
`connection_factory` only needs to return or await to an object with async
`recv()` and `close()` methods, plus optional async `connect()`. No private
connection protocol type is a public import target. String messages are parsed
as JSON, and bytes messages are decoded as UTF-8 before JSON parsing. Payloads
must decode to an object; non-object payloads raise
`RemoteInvalidationConfigurationError` at receive time. Malformed JSON and
invalid UTF-8 bytes propagate as their native Python exceptions. Runtime
non-object payloads intentionally use `RemoteInvalidationConfigurationError` for
compatibility, even though malformed JSON, invalid UTF-8, and handler failures
propagate differently. If any
completed receive/decode task fails, `listen_once()` closes cached connections
for all configured URLs and re-raises that error. Pending receive tasks are
cancelled after the first URL completes and gathered with exceptions collected.
When multiple URL tasks complete at the same time, normal `asyncio.gather(...)`
exception precedence decides which error is raised. Handler exceptions are not
swallowed or logged by `listen_once()`. Handlers are called synchronously; async
handler returns are not awaited. For long-running workers, call `connect()`
before `run()` when your connection object needs its optional `connect()` hook.
`run()` intentionally does not call that optional hook itself. It can create
websocket connections lazily, then starts a listener per URL and reconnects
failed receive/decode/handler loops after `reconnect_delay` seconds until
`close()` is called. It calls `close()` from its `finally` block when it exits,
including external cancellation. Invalid connection objects are
rejected when a connection is first created, which can happen from `connect()`,
`listen_once()`, or `run()`. `close()` can be called repeatedly during normal
shutdown; it cancels current listener tasks and closes cached connections. It
does not directly cancel a `listen_once()` call that is already waiting; that
one-off receive finishes according to the connection's receive/close behavior.

Server-side event emission is handled by `emit_remote_invalidation()` after data
changes. It sends no message when the manager has no RemoteAPI config, websocket
invalidation is disabled, or no Channels layer is configured. Explicit delete
identification metadata wins over instance identification; UUID, date, and
datetime values are serialized as strings, and other non-JSON values fall back
to `str(value)`. The action string is forwarded as supplied. Messages are sent
with fields `protocol_version`, `base_path`, `resource_name`, `action`,
`identification`, and `event_id`; missing identification is sent as `null`.
Serialization is shallow, so nested lists or mappings also fall back to
`str(value)`. The channel-layer event type is `gm.remote.invalidation` and
`event_id` is a UUID4 string. The ASGI consumer closes unknown or disabled
resources with `4404`, protocol-version mismatches with `4406`, and missing
channel-layer connections with `1011`. Missing `version` is accepted, multiple
`version` query values use the first parsed value, non-disconnect inbound
websocket messages are ignored, and client disconnect cleans up the
channel-layer group subscription.

For bulk writes, use the public notification context outside the transaction so
the commit completes before refresh delivery:

```python
from django.db import transaction

from general_manager.api import bulk_data_change_notifications


with bulk_data_change_notifications():
    with transaction.atomic():
        for project in projects:
            project.update(status="archived")
```

The context emits one invalidation per affected RemoteAPI resource with
`action = "refresh"`, `identification = null`, and a UUID4 `event_id`. Clients
should treat it as resource-wide invalidation and requery the resource over
REST. The context is not transaction-aware by itself and flushes even when its
body exits exceptionally; the nesting above ensures that flush happens after
the transaction has committed or rolled back. Outside the context, immediate
row-level events remain unchanged.

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
