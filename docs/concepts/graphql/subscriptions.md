# GraphQL Subscriptions

`general_manager.api.graphql.GraphQL` adds subscription fields for every registered manager so clients can react to live changes. This page covers the basic lifecycle and the advanced dependency tracking optimisations that keep the streams efficient.

## Overview

For each manager class (e.g. `Project`), GraphQL exposes a subscription field named `on<ManagerClass>Change` (e.g. `onProjectChange`). Multi-word manager names keep their word boundaries in camelCase, such as `TaxCalculation` becoming `onTaxCalculationChange`. The resolver:

1. Validates the identification arguments (e.g. `id`, other interface inputs).
2. Instantiates the manager and emits an initial `snapshot` event.
3. Adds the subscription to one or more channel groups.
4. Listens for change notifications and forwards them as `SubscriptionEvent` payloads.

```graphql
subscription ($id: ID!) {
  onProjectChange(id: $id) {
    action
    item {
      id
      name
    }
  }
}
```

For class-wide streams, GraphQL also exposes `on<ManagerClass>ClassChange` (e.g. `onProjectClassChange` or `onTaxCalculationClassChange`). This field takes no identification arguments and emits one changed item per event:

```graphql
subscription {
  onProjectClassChange {
    action
    item {
      id
      name
    }
  }
}
```

Class-wide subscriptions do not emit an initial `snapshot`, because there is no single current item to return. They only stream future changes that happen after the subscription is active.

Each event has two fields:

- `action`: describes what triggered the update (`snapshot`, `update`, `delete`, custom signals).
- `item`: a fresh manager instance (or `null` if the underlying data can no longer be fetched).

## WebSocket protocol cookbook

When Channels is installed, GeneralManager exposes the GraphQL subscription endpoint with the `graphql-transport-ws` subprotocol. A client should offer that subprotocol during the WebSocket handshake. If the client does not offer it, the socket is still accepted without a selected subprotocol. After the socket opens, send `connection_init` before subscribing:

```json
{"type": "connection_init", "payload": {"authToken": "optional-client-value"}}
```

The server answers with exactly this acknowledgement envelope:

```json
{"type": "connection_ack"}
```

The `connection_init.payload` field is optional. When it is an object, it becomes `GraphQLSubscriptionContext.connection_params`. When it is absent, `null`, or not an object, `connection_params` is cleared to an empty mapping.

After the acknowledgement, start a stream with a `subscribe` message. The `id`
must be a string; empty strings are accepted, while missing, `null`, and
non-string ids close the socket with code `4403`. The `payload` must be an
object containing a string `query`, optional object `variables`, and optional
string `operationName`; `variables: null` and `operationName: null` are treated
as omitted, and extra message or payload fields are ignored. Empty `query`
strings count as provided strings and are passed to GraphQL parsing rather than
being treated as missing:

```json
{
  "type": "subscribe",
  "id": "project-1",
  "payload": {
    "query": "subscription ($id: ID!) { onProjectChange(id: $id) { action item { id name } } }",
    "variables": {"id": "123"}
  }
}
```

Each execution result is sent as a `next` envelope:

```json
{
  "type": "next",
  "id": "project-1",
  "payload": {
    "data": {
      "onProjectChange": {
        "action": "snapshot",
        "item": {"id": "123", "name": "Roadmap"}
      }
    }
  }
}
```

The `payload.data` key is omitted when GraphQL returns no data. The `payload.errors` key is included when GraphQL returns errors and is always a list of GraphQL formatted error dictionaries. When neither data nor errors are present, `payload` is an empty object. GeneralManager does not currently forward `ExecutionResult.extensions`.

If GraphQL returns a single non-streaming `ExecutionResult` for a subscription request, the server sends one `next` envelope and then `complete` for that operation id. A finite async subscription iterator sends `complete` after the iterator ends.

Stop a stream by sending:

```json
{"type": "complete", "id": "project-1"}
```

If `complete.id` is missing, not a string, or does not match an active subscription, the message is ignored. Sending a second `subscribe` message with an existing `id` first creates the replacement iterator, then cancels and awaits the previous task before the replacement stream is registered. Cancellation errors from the previous task are suppressed, but other exceptions raised by the previous task propagate and prevent the replacement from being registered. Disconnecting the WebSocket cancels and awaits every active subscription task; only cancellation errors are suppressed during disconnect.

`ping` messages receive `pong`; when the `ping` payload is not `null`, it is echoed. Missing or `null` ping payloads produce `{"type": "pong"}` with no `payload` key:

```json
{"type": "ping", "payload": {"sentAt": 123}}
{"type": "pong", "payload": {"sentAt": 123}}
```

The GraphQL context passed to resolvers is a `GraphQLSubscriptionContext` with `user`, decoded `headers`, the original Channels `scope`, and `connection_params` from `connection_init`. Headers are decoded with Latin-1; if duplicate header names are present, the last value wins. The connection parameters mapping is kept as the consumer state rather than copied. Treat it as read-only in resolvers; mutating the concrete dictionary is not part of the supported contract.

Protocol-level validation uses WebSocket close codes without custom close reason text. These close-only validation failures do not send `error` or `complete` envelopes first:

- `4400`: incoming JSON is not an object, the message has no recognized `type`, or the `type` is missing.
- `4401`: `subscribe` was sent before `connection_init`. This acknowledgement check runs before validating `id` or `payload`, so a pre-acknowledgement `subscribe` with malformed fields still closes with `4401`.
- `4403`: the subscription `id` is not a string or the subscription `payload` is not an object.
- `4429`: `connection_init` was sent more than once.

Repeated `connection_init` is idempotent only after the first `connection_init`
has set `connection_acknowledged`. A second `connection_init` closes with `4429`
and sends no additional acknowledgement. The first acknowledgement sets that
flag before sending `connection_ack`; if sending the acknowledgement raises an
unsuppressed exception, normal exception propagation applies and the consumer
does not have a separate retry protocol.

JSON parse failures are handled by the underlying Channels JSON consumer before
GeneralManager's `receive_json()` dispatch runs. When sending protocol messages,
GeneralManager silently discards messages if Channels raises `RuntimeError`
because the connection is already closed; other `send_json()` exceptions
propagate through the active handler or stream task.

Subscription validation errors that have a valid operation id are returned as an `error` envelope followed by `complete` for the same `id`. The `error.payload` value is always a list of error dictionaries. Missing subscription support returns `[{"message": "GraphQL subscriptions are not configured."}]`, missing or non-string `query` returns `[{"message": "A GraphQL query string is required."}]`, non-object `variables` returns `[{"message": "Variables must be provided as an object."}]`, and an invalid `operationName` returns `[{"message": "The operation name must be a string when provided."}]`.

GraphQL parse errors and `GraphQLError` exceptions raised while creating the subscription are returned the same way: `error` with a list containing each `GraphQLError.formatted` dictionary, followed by `complete`. GraphQL error `extensions` stay inside those formatted dictionaries when GraphQL provides them. Other recoverable subscription setup or iteration exceptions (`RuntimeError`, `ValueError`, `TypeError`, `LookupError`, `ConnectionError`, `KeyError`, and `asyncio.TimeoutError`) are returned as `[{"message": "<exception text>"}]`; during iteration the server then attempts iterator cleanup and sends `complete`.

If GraphQL subscription setup unexpectedly returns a value that is neither an
`ExecutionResult` nor an async iterator, the server sends
`[{"message": "GraphQL subscription did not return an async iterator."}]`,
then `complete`, and does not register a subscription task.

When a stream finishes, the server calls `aclose()` on the async iterator when a callable `aclose` attribute exists. The consumer does not verify that `aclose()` returns an awaitable before awaiting it; a non-awaitable return raises the normal Python `TypeError`. Finite streams, recoverable iteration errors, cancellation, non-recoverable iteration errors, and close failures all use the same cleanup order: attempt `aclose()`, attempt `complete`, then remove the operation id from the active subscription registry. Exceptions from `aclose()` are not suppressed: the server still attempts to send `complete`, removes the operation from the active subscription registry, and then propagates the close exception. If a finite stream's `aclose()` fails, `complete` is still attempted and the close exception propagates unless the `complete` send raises a higher-precedence error. If a non-recoverable iteration error and an `aclose()` error both occur, the `aclose()` error is the one that propagates. If sending `complete` raises anything other than the internally suppressed closed-connection `RuntimeError`, that send error supersedes the active iteration or close error.

Cancellation uses the same cleanup path: the server attempts `aclose()`, then
attempts `complete`, then removes the operation id from the active subscription
registry. `asyncio.CancelledError` propagates after cleanup unless an `aclose()`
or unsuppressed `complete` send error replaces it. During disconnect, the
consumer suppresses cancellation errors while awaiting cancelled tasks; other
task errors still propagate from the await.

### Signals and channels

- Subscriptions require Django Channels. If `get_channel_layer()` returns `None`, the resolver raises a descriptive GraphQL error explaining that `CHANNEL_LAYERS` must be configured.
- Managers are automatically decorated with `@data_change` and emit `pre_data_change` and `post_data_change` signals. GraphQL listens to `post_data_change` and forwards the event to the relevant instance channel group (`gm_subscriptions.<Manager>.<digest>`) and class channel group (`gm_subscriptions.<Manager>.__class__`).

### Identification helpers

The subscription arguments mirror the interface inputs. For nested managers, the schema accepts IDs (e.g. `employeeId`) so the server can reconstruct the full identification dictionary. This results in subscriptions that are consistent with query and mutation signatures.

## Dependency tracking

When a client subscribes to `on<Manager>Change`, the resolver primes only the GraphQL properties that appear in the `item { … }` selection. The priming step records two sets of dependencies:

- Inputs defined on the interface (standard behaviour).
- Additional managers accessed inside the requested GraphQL properties.

The subscription then joins the channel groups for all collected dependencies. Any dependent manager that emits `post_data_change` triggers a new event for the subscriber. The optimisation keeps subscriptions responsive without executing unrelated properties for every client.
Before every yielded subscription event, GeneralManager clears the per-operation
GraphQL permission-capability context so capability fields are evaluated against
the freshly hydrated payload.

Dependency tracker records only contribute channel groups when they are
`"identification"` dependencies, their manager name is registered, and the
serialized identifier parses into an identification dictionary. Malformed
identifier JSON and serialized JSON `null` are skipped. Interface input
dependencies are detected from `input_fields` whose type is a `GeneralManager`
subclass. Values may be manager instances, identification dictionaries, or lists
containing either form; `None` is ignored. Returned identification dictionaries
are copied before subscription wiring stores them. Tracker-derived dependencies
are emitted first in accepted tracker-record order; input-field dependencies are
then appended in `input_fields` iteration order, preserving the order of
list-valued input fields. Dependencies are deduplicated by manager type name plus
serialized identification, so different manager classes with the same class name
and identical serialized identification collide. The changed instance itself is
not subscribed as its own dependency. Malformed interface metadata or
non-serializable identification values are not converted into custom
subscription errors; the underlying exception propagates during subscription
setup. Dependency identifier serialization coerces mapping keys to strings; when
two keys collide after coercion, the last item after sorting by `str(key)` is the
one retained, with equal sort keys preserving the mapping's iteration order.

### Query permutations

The field selection inspection understands:

- Inline fragments on the subscription payload.
- Named fragments reused across subscriptions.
- Aliases applied to GraphQL properties.
- Subscriptions that omit the `item` field entirely (only the `action` is streamed).

Aliases do not replace the underlying field name for dependency selection; for
example, `aliasValue: propB` records `prop_b`, not `alias_value`. No additional
configuration is necessary. Continue to annotate computed fields with
`@graph_ql_property`; the dependency tracker automatically inspects what each
subscriber actually needs.

## Error handling

- Missing channel layer configuration produces a GraphQL error instructing the operator to configure `CHANNEL_LAYERS`.
- If instantiating the manager or a dependency fails during an update, the subscription sends an event with `item = null` and the incoming `action`. Clients can use this to show a placeholder while retrying the fetch.
- Class-wide subscriptions check object-level read permission before yielding each event. If the requesting user cannot read the changed object, the event is suppressed entirely so the stream does not reveal hidden object IDs or change timing.
- If a class-wide event cannot be rehydrated, such as a hard-deleted object that no longer exists, the event is suppressed rather than sent with `item = null`.
- Class-wide subscriptions suppress these malformed queued messages before
  hydration: messages for another manager name, missing or non-string `action`
  values, and missing or non-object `identification` payloads. The lower-level
  channel listener only requires `type: "gm.subscription.event"` and a present
  `action` before queueing a message, so other malformed payloads reach the
  class-wide stream and are handled there or by the normal hydration error path.
- Data-change dispatch publishes to both the instance group and the class-wide
  group only for registered managers and configured channel layers. A provided
  signal identification mapping is used when available; otherwise the changed
  instance identification is used. The identification mapping is deep-copied
  before dispatch. Dispatch failures are logged per target group and do not
  prevent attempts to publish to the remaining group.

## Testing tips

1. Extend `GeneralManagerTransactionTestCase` to register test managers and clean up dynamic models.
2. Build the schema with `GraphQL._subscription_class` and call `graphene.Schema.subscribe`.
3. Trigger changes in `asyncio.to_thread` to avoid mixing sync/async database operations.
4. Attach a small log list to the manager to assert which GraphQL properties were evaluated during the subscription lifecycle.
