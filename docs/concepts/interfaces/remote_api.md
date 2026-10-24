# RemoteAPI Websocket Invalidation

## REST read authorization

Generated RemoteAPI query, detail, create, and update responses apply the
manager's read policy for `request.user`. A missing request user is treated as
anonymous. Install Django authentication middleware or your application's
authentication integration to supply the intended principal; the protocol
version header selects the wire format and does not authenticate callers.

Query responses apply permission filters and required instance checks before
local sorting, counting, and pagination. Only readable rows contribute to local
`total_count`. For an incomplete upstream response requiring local instance
checks, `total_count` is omitted because the authorized global count is unknown.
Request-backed managers with multiple permission alternatives reject global
pagination or ordering controls before fetching data. This includes the default
page: the branches cannot safely supply a global page.

Response serialization checks each field before evaluating its value. Denied
fields are omitted, including computed properties. A detail read denied by the
row policy returns HTTP 403 with `error_code: "permission_denied"`. Create and
update still enforce their write permissions independently; a successful write
whose resulting row is unreadable retains its success status and returns
`items: []`. Managers without a permission class retain the framework's default
public read behavior.

## Finite query pages

Every query defaults to page 1 with 10 items. Set
`GENERAL_MANAGER["REMOTE_API_DEFAULT_PAGE_SIZE"]` and
`GENERAL_MANAGER["REMOTE_API_MAX_PAGE_SIZE"]` to positive integers to change the
server default and maximum (100 by default); the default cannot exceed the
maximum. Explicit oversized, non-integer, or non-positive page sizes fail with
HTTP 400 (`invalid_request`) before executing the query. Clients that previously
omitted pagination must now request subsequent pages to read more than the
default page. Response metadata always reports the effective page and size.

RemoteManager forwards the effective page controls upstream. Incomplete upstream
pages must match those controls; otherwise the server returns
`unsupported_global_operation` rather than presenting a partial dataset as a
complete page. Even an upstream response that claims matching coordinates cannot
expand the serialized output beyond the effective page size. Read authorization
and authorized counts retain the semantics above. This is an output limit;
counting and per-instance authorization may still examine more rows, and generic
request interfaces may still fetch a complete upstream response.

## Websocket invalidation

RemoteAPI websocket invalidation is a commit-safe hint that a REST resource
changed. A manager opts in with `RemoteAPI.enabled = True` and
`RemoteAPI.websocket_invalidation = True`. Clients use the websocket event to
decide when to refetch the resource over REST; the event is not a second data
transport and does not contain a complete resource snapshot.

The websocket admission boundary requires an authenticated, active Django user
from the existing ASGI authentication middleware. Anonymous or inactive users
are closed with code `4401` before channel or group registration. Each
identified create/update event reloads that user and the current manager row,
then applies the manager's native read policy; missing, deleted, unreadable,
or failed reads are silently suppressed. A fresh authorization check does not
evict data already held by a client and does not revalidate arbitrary external
tokens.

`ensure_remote_invalidation_route()` preserves the existing ASGI security stack.
It updates an accessible Channels `URLRouter` in place through known middleware
wrappers, including origin validation and custom authentication. Opaque root
callables, custom `.app` wrappers, and closures are left unchanged with a
warning; configure the route explicitly inside those stacks. Only an unwrapped
Django `ASGIHandler` is automatically wrapped with a protocol router. Clearing
removes only GeneralManager-marked routes and preserves the wrappers and other
websocket routes.

## Mental model

The server-side flow has four stages:

1. A manager mutation emits `post_data_change`.
2. `emit_remote_invalidation()` resolves the normalized `RemoteAPIConfig`,
   deep-copies the selected identification, and registers a callback with
   Django's `transaction.on_commit()` for the changed database alias.
3. A successful outermost commit publishes the row-level invalidation. A full
   transaction rollback discards all callbacks. Rolling back to a savepoint
   discards only callbacks registered after that savepoint; callbacks registered
   before it remain queued for execution when the outer transaction commits.
4. `bulk_data_change_notifications()` can deduplicate committed changes into
   one `refresh` event per resource. Put that context outside the true
   outermost transaction so the callbacks run while the batch is open.

This ordering prevents clients from refetching data that is not committed yet.
When no transaction is active and the connection is in autocommit mode, Django
executes the `on_commit()` callback immediately, so ordinary non-transactional
mutations keep immediate delivery. If autocommit is disabled outside an
`atomic()` block, `on_commit()` raises `TransactionManagementError` instead of
registering or executing the callback. A callback observes the identification
from signal time rather than later mutations to the manager instance. Explicit
`identification` takes priority over `instance.identification`; omitted
identification is sent as `null`.

## Delivery contract

An ordinary event carries the resource's `base_path`, `resource_name`,
`protocol_version`, action, identification, and a UUID4 `event_id`. UUIDs,
dates, and datetimes in the top-level identification mapping are serialized as
strings; other non-JSON values fall back to `str(value)`. A bulk `refresh` has
no row identification, so clients should treat it as resource-wide and
requery the REST endpoint.

Delivery is best-effort after commit. A missing channel layer produces no
message, and ordinary channel-layer delivery failures are logged rather than
raised. `MemoryError` still propagates. Configuration, identification-copy,
and transaction-registration failures raised before the callback is installed
remain visible to the caller.

For the task-oriented transaction and batching instructions, see the [bulk
notification how-to](../../howto/bulk_data_change_notifications.md). The
[RemoteAPI setup how-to](../../howto/remote_manager_interface.md) covers
authentication, finite pagination, and client/server configuration. The
[Remote manager end-to-end recipe](../../examples/remote_manager_interface_end_to_end.md)
shows the server and client setup, and the [API reference](../../api/graphql.md)
records the callable contract for `emit_remote_invalidation()`.
