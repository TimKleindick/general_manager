# RemoteAPI Websocket Invalidation

RemoteAPI websocket invalidation is a commit-safe hint that a REST resource
changed. A manager opts in with `RemoteAPI.enabled = True` and
`RemoteAPI.websocket_invalidation = True`. Clients use the websocket event to
decide when to refetch the resource over REST; the event is not a second data
transport and does not contain a complete resource snapshot.

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
[Remote manager end-to-end recipe](../../examples/remote_manager_interface_end_to_end.md)
shows the server and client setup, and the [API reference](../../api/graphql.md)
records the callable contract for `emit_remote_invalidation()`.
