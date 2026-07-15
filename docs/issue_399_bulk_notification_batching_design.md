# Bulk Data-Change Notification Batching Design

## Context

GeneralManager emits `post_data_change` after each successful mutation. The
GraphQL subscription receiver synchronously bridges into the channel layer once
for the instance group and once for the class-wide group. RemoteAPI websocket
invalidation uses the same bridge once for opt-in managers.

When a synchronous import creates thousands of managers row by row, asgiref
creates a single-worker executor and an event loop for each bridge call. A
14,265-row import can therefore create roughly 28,530 GraphQL bridge lifecycles,
or roughly 42,795 when RemoteAPI invalidation is also enabled. This overhead is
incurred even when no subscribers are connected and can exhaust memory under a
debugger or other memory pressure.

## Goals

- Provide an explicit, nestable context for batching data-change notifications.
- Emit one GraphQL refresh per affected manager class at outermost context exit.
- Emit one RemoteAPI refresh per affected websocket resource at outermost exit.
- Use one synchronous-to-async bridge for the complete batch flush.
- Flush accumulated refreshes after both successful and exceptional exits.
- Preserve immediate cache invalidation and all unrelated signal receivers.
- Preserve existing row-level notification behavior outside the context.
- Propagate resource exhaustion rather than reporting a failed dispatch as
  successful.

## Non-goals

- Automatically detect mutation loops.
- Delay or coalesce dependency-cache invalidation.
- Preserve individual row actions or identifications inside an explicit batch.
- Coordinate a batch collector across unrelated threads or processes.
- Introduce a persistent background event-loop thread.

## Public API

Expose a synchronous context manager from `general_manager.api`:

```python
from general_manager.api import bulk_data_change_notifications

with bulk_data_change_notifications():
    for row in rows:
        ExampleManager.create(**row)
```

The context is re-entrant. Only the outermost exit flushes notifications. An
empty context performs no channel-layer lookup and creates no async bridge.

The collector is scoped with `ContextVar`. Mutations executed in unrelated
worker threads are outside the batch unless those threads explicitly enter
their own context.

## Architecture

Add a small notification-batching module under `general_manager.api`. It owns:

- the public context manager;
- the active batch state;
- target registration helpers for GraphQL managers and RemoteAPI resources;
- the single asynchronous flush coroutine; and
- the synchronous bridge used at outermost exit.

The active state stores a nesting depth and deduplicated notification targets.
Targets contain only stable routing information needed at flush time. The state
is detached from the active context before dispatch begins so notification
callbacks cannot accidentally append to the batch being flushed.

GraphQL and RemoteAPI signal receivers keep their existing eligibility checks.
When no batch is active, they dispatch immediately. When a batch is active,
they register a target and return without calling `async_to_sync`.

At outermost exit, the context snapshots the targets and invokes one
`async_to_sync` wrapper around the flush coroutine. The coroutine dispatches
targets sequentially to bound concurrency and reports failures with target
context. Deterministic target ordering makes behavior and tests reproducible.

## GraphQL Refresh Routing

Each registered GraphQL manager receives a dedicated refresh group distinct
from its existing instance and class-wide change groups. The group uses the
same stable naming constraints as existing subscription groups.

Detail subscriptions join:

- their existing instance group;
- existing instance groups for dependency managers; and
- the refresh group for their own manager class and every dependency manager
  class represented by those groups.

On a refresh message, a detail subscription rehydrates its own identified item
and emits:

```text
action = "refresh"
item = <fresh item or null>
```

Class-wide subscriptions join their existing class group and the manager refresh
group. A refresh message cannot identify one authorized item, so they emit:

```text
action = "refresh"
item = null
```

This intentionally relaxes the existing class-wide timing guarantee: a client
with an active class subscription can observe that some item in the class
changed during an explicitly batched operation, but receives no item identity.
Row-level events outside the batch retain the existing per-object permission
checks.

The GraphQL refresh channel message contains `type`, `action="refresh"`, and the
manager name. It does not contain row identification.

## RemoteAPI Refresh Routing

RemoteAPI batching deduplicates by websocket invalidation resource, including
the configuration fields that determine the group and protocol payload. The
flush emits the normal invalidation envelope with:

```text
action = "refresh"
identification = null
event_id = <new UUID>
```

One message is sent per affected resource, regardless of how many rows changed.
Normal non-bulk RemoteAPI events retain their existing action and identification.

## Failure Semantics

The context flushes accumulated targets in `finally`, including when the batch
body raises. This prevents stale subscribers when some rows committed before a
later row failed. A refresh after a fully rolled-back transaction is redundant
but safe.

Ordinary channel-layer dispatch failures are logged with their target and do
not stop remaining refreshes. Successful database mutations are not reported as
failed solely because a best-effort notification could not be delivered.

`MemoryError` is never treated as an ordinary delivery failure. It propagates
immediately. If the batch body and the flush both fail, both exceptions are
preserved in an `ExceptionGroup` so neither failure is hidden.

Immediate GraphQL dispatch outside a batch should wrap both group sends in one
async helper, reducing the normal path from two bridge lifecycles to one while
preserving independent failure logging for each group. It must not log a
successful dispatch when every target failed.

## Transactions

The notification context must wrap an explicit database transaction so the
transaction commits before refresh dispatch:

```python
with bulk_data_change_notifications():
    with transaction.atomic():
        for row in rows:
            ExampleManager.create(**row)
```

Reversing the contexts can flush before the outer transaction commits, allowing
subscribers to rehydrate pre-commit state. The initial implementation documents
and tests the required ordering instead of attempting implicit multi-database
`on_commit` coordination.

## Cache Behavior

Dependency-cache invalidation remains immediate. The existing data-change
barrier prevents unsafe publication during a mutation but does not bypass cache
reads for the duration of an outer batch. Delaying deletion without a broader
cache-consistency redesign would therefore expose stale values.

Run-scoped cache clearing, dependency index maintenance, GraphQL cache rewarm,
and unrelated `post_data_change` receivers retain their current behavior.

## Verification

Test-driven implementation will add coverage for:

- empty, nested, successful, and exceptional batch contexts;
- target deduplication across many mutations;
- one bridge invocation per outermost non-empty flush;
- deterministic sequential dispatch;
- one GraphQL refresh per affected manager class;
- detail subscription refresh and rehydration;
- dependency-class refresh triggering detail subscriptions;
- class-wide `refresh` events with `item=null`;
- one RemoteAPI refresh per affected resource;
- normal row-level behavior outside a batch;
- ordinary channel-layer failures continuing across targets;
- `MemoryError` propagation and dual-failure `ExceptionGroup` behavior;
- transaction ordering; and
- immediate cache invalidation remaining active inside a notification batch.

Focused tests run first, followed by Ruff, mypy, and the full pytest suite when
the focused checks pass.

## Documentation

Update the GraphQL subscription guide and RemoteAPI websocket documentation to
describe:

- the public batching context;
- `refresh` payload semantics;
- detail and class subscription behavior;
- the explicit class-level timing disclosure;
- transaction context ordering; and
- the fact that cache invalidation is not batched.
