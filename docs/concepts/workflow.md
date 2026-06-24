# Workflow

GeneralManager workflows connect manager-level events to durable automation. The workflow subsystem is intentionally separate from interface CRUD code: interfaces emit domain changes, event registries route those changes, and workflow engines execute or delegate orchestration.

## Mental model

A workflow has four layers:

1. **Events** describe what happened. Helpers such as `manager_created_event`, `manager_updated_event`, and `manager_deleted_event` produce stable `WorkflowEvent` payloads for manager CRUD changes.
2. **Registries** route events to handlers. `InMemoryEventRegistry` is useful for local development and tests; `DatabaseEventRegistry` stores durable route state for production-style delivery.
3. **Engines** execute workflow definitions. `LocalWorkflowEngine` runs in process, while `CeleryWorkflowEngine` persists executions and delegates async work through Celery tasks.
4. **Actions** centralize reusable side effects behind `ActionRegistry`, so handlers call named operations instead of scattering integration logic.

## Event routing

Handlers can register against canonical event types such as
`general_manager.manager.updated` or readable event names such as
`manager_updated`. Route keys are exact strings: keys containing `.` match
`WorkflowEvent.event_type`, while keys without `.` match `WorkflowEvent.event_name`.
They are not stripped, normalized, wildcarded, or prefix-matched. Empty strings
are valid event-name keys, but readable event names should stay dot-free.

A registration can include a `when` predicate, a validator, retry settings, and
a dead-letter callback. Validators run before predicates. Validator failures go
directly to dead-letter handling and are not retried. Handler failures are
retried according to `retries` and `retry_on`; with `retry_on=None`, every
handler exception is retried until the attempt budget is exhausted. Synchronous
registries isolate handler failures so one failed route does not stop later
matching routes. Predicate exceptions and dead-letter handler exceptions are not
wrapped by the in-memory registry; database outbox processing records them as
outbox failures.

Identical registrations are ignored rather than appended. Registration identity
includes the event key, handler, validator, predicate, retry count, retry
predicate, and dead-letter handler. Changing any of those routing options creates
a separate registration and may invoke the same handler more than once. Matching
handlers run in registration order within each route bucket, and type-route
registrations run before name-route registrations when an event matches both.
Registrations skipped by `when=False` are not considered applicable for
`publish()` return values. Callable identity uses
`__module__ + "." + __qualname__` when available and `repr(...)` otherwise. The
retry count in the identity is the clamped retry count.

If `retry_on` raises while evaluating a handler failure, that exception
propagates in the in-memory registry and is recorded as an outbox failure during
database outbox processing. Registration-level dead-letter handlers take
precedence over the in-memory registry's `dead_letter_handler`; database
registries only support registration-level custom dead-letter handlers.
The registry trusts the annotated types: invalid handlers, predicates,
validators, retry predicates, or dead-letter handlers fail when they are called,
while non-string event keys or non-integer retry values may raise normal Python
errors during registration.

`InMemoryEventRegistry` deduplicates published `event_id` values in a bounded
process-local cache and routes handlers inline. `DatabaseEventRegistry` persists
events and outbox rows. With async delivery enabled, `publish()` stores the event
and schedules outbox processing after commit, so it returns `False` because no
handler has completed in that call; use `publish_sync()` when a service path must
route handlers inline against the configured registry. `DatabaseEventRegistry`
`publish_sync()` commits event persistence before inline routing. Duplicate
`event_id` persistence is ignored and routing still runs against the provided
event object; non-duplicate persistence errors propagate and prevent routing.
Duplicate async `publish()` calls return `False` without creating or routing a
new outbox row.

For synchronous registries, `publish()` returns `True` when at least one
applicable handler completes, even if another matching handler fails. Duplicate
`event_id` publishes return `False`. The in-memory registry marks an id as seen
before routing, so handler failure does not make that id publishable again; when
the bounded seen-id cache is full, the oldest id is evicted. `WorkflowEvent` is a
frozen dataclass, but `payload` and `metadata` may still reference mutable
mappings supplied by the caller.

Manager event helpers fill in consistent metadata around those events. If
`event_id` is omitted, they generate a UUID4 string. If `occurred_at` is omitted,
they use the current UTC time. Create events use
`general_manager.manager.created` and include `manager`, `values`, and optional
`identification`. Update events use `general_manager.manager.updated` and include
`manager`, `changes`, and optional `identification`; every changed field is
stored as `{"old": old_values.get(field), "new": value}`, so missing old values
become `None`. Delete events use `general_manager.manager.deleted` and include
`manager` plus optional `identification`. The helpers shallow-copy top-level
payload and metadata mappings but do not deep-copy nested objects.

In-memory registry locking protects registration mutation, handler snapshot
creation, and seen-id cache updates. Handlers run outside the lock. Concurrent
register calls can affect later publishes, but not a handler snapshot already
being routed.

Choose the registry in `GENERAL_MANAGER["WORKFLOW_EVENT_REGISTRY"]`, or rely on
`WORKFLOW_MODE` defaults. `GENERAL_MANAGER["WORKFLOW_EVENT_REGISTRY"]` takes
precedence over top-level `WORKFLOW_EVENT_REGISTRY`, including explicit `None`
to use the mode default. Accepted values are:

- an `EventRegistry` instance
- a dotted import path to an event registry instance, class, or zero-argument factory
- an event registry class or zero-argument factory callable
- a mapping with `class` and optional `options`, where options are passed as keyword arguments
- `None`, or a missing setting, to use the mode default

The mode default is `InMemoryEventRegistry` unless `WORKFLOW_MODE` is
`production`, in which case it is `DatabaseEventRegistry`. Import errors,
factory errors, and constructor errors propagate. Mapping `options` must be a
mapping, and non-`None` settings must resolve to an `EventRegistry`. Mapping
keys other than `class` and `options` are ignored; registry options are not
merged with any other setting. `InMemoryEventRegistry` accepts
`dead_letter_handler` and `max_seen_event_ids` constructor options;
`DatabaseEventRegistry` accepts no constructor options.

`WORKFLOW_MODE` is stripped and lowercased. `"production"` selects
`DatabaseEventRegistry`; `"local"` and any unrecognized value select
`InMemoryEventRegistry`.

Workflow config helpers always read nested `GENERAL_MANAGER` values before
top-level Django settings. If `GENERAL_MANAGER` is not a mapping, nested values
are ignored. Boolean settings such as `WORKFLOW_ASYNC`,
`WORKFLOW_BEAT_ENABLED`, and `WORKFLOW_DEAD_LETTER_ENABLED` use Python
`bool(...)` coercion when explicitly set to a non-`None` value; for example,
non-empty strings are truthy. `WORKFLOW_ASYNC` and `WORKFLOW_BEAT_ENABLED` treat
`None` as omitted and fall back to the mode defaults.
`WORKFLOW_DEAD_LETTER_ENABLED` always uses `bool(...)`, so an explicit `None`
disables dead letters. Unexpected errors from settings attribute access are not
wrapped. Integer settings parse with
`int(...)`, clamp to their minimums, and use their documented defaults when
parsing fails:

- `WORKFLOW_BEAT_OUTBOX_INTERVAL_SECONDS`: default `5`, minimum `1`
- `WORKFLOW_BEAT_MAX_JITTER_SECONDS`: default `2`, minimum `0`
- `WORKFLOW_OUTBOX_BATCH_SIZE`: default `100`, minimum `1`
- `WORKFLOW_OUTBOX_PROCESS_CHUNK_SIZE`: default `50`, minimum `1`
- `WORKFLOW_OUTBOX_CLAIM_TTL_SECONDS`: default `300`, minimum `1`
- `WORKFLOW_MAX_RETRIES`: default `3`, minimum `0`
- `WORKFLOW_RETRY_BACKOFF_SECONDS`: default `5`, minimum `1`
- `WORKFLOW_DELIVERY_RUNNING_TIMEOUT_SECONDS`: default `300`, minimum `1`

`get_event_registry()` returns an import-time `InMemoryEventRegistry` until you
call `configure_event_registry()` or `configure_event_registry_from_settings()`.
`configure_event_registry()` trusts the static `EventRegistry` type hint and does
not validate the object at runtime.

Database registries hand `payload` and `metadata` to Django `JSONField` and
`occurred_at` to `DateTimeField`. JSON serializability, timezone handling, and
database precision follow the project's Django settings and database backend;
this layer does not normalize them.

When database outbox processing fails after an applicable handler was attempted,
the outbox attempt count increments. While attempts are below
`WORKFLOW_MAX_RETRIES`, the row remains failed and becomes due again at
`now + WORKFLOW_RETRY_BACKOFF_SECONDS * attempts`. When attempts reach
`WORKFLOW_MAX_RETRIES` and dead letters are enabled, the row moves to
`dead_letter`. Handler-level `register(..., retries=...)` controls attempts
inside one outbox processing call; outbox attempts control later processing
calls.

Dead letters are controlled by `WORKFLOW_DEAD_LETTER_ENABLED`, which defaults to
`True` and is interpreted with `bool(...)`. When disabled, rows that reach
`WORKFLOW_MAX_RETRIES` stay failed and are scheduled again with backoff instead
of moving to `dead_letter`. `WORKFLOW_MAX_RETRIES` is parsed with `int(...)`,
clamped to at least `0`, defaults to `3` on missing or invalid values, and counts
total failed outbox processing calls before dead-letter transition.
`WORKFLOW_RETRY_BACKOFF_SECONDS` is parsed with `int(...)`, clamped to at least
`1`, and defaults to `5` on missing or invalid values.

When one applicable database handler succeeds and another fails during the same
outbox processing call, the failed handler still receives dead-letter handling,
but the outbox row is marked processed because the event had a successful route.

Dotted registry import strings are resolved before classification. Imported
classes are instantiated, imported registry instances are reused, and imported
callables that are not already `EventRegistry` instances are called as factories.

## Actions

Use `ActionRegistry` when workflow handlers need named side effects such as
email, audit logging, or external API calls. The registry is process-local and
matches exact action name strings without normalization. An action implements
`execute(context, params)` and returns a result mapping or `None`. Context and
params are `Mapping[str, object]` values so handlers can pass plain dictionaries
or read-only mapping views.

Action names are unique in one registry. Registering the same name twice raises
`ActionAlreadyRegisteredError` unless `replace=True` is passed. Registration
trusts the type hints and does not validate the action object at runtime; invalid
objects fail when executed. `get(name)` returns the registered action or raises
`ActionNotFoundError`. `execute(name, context=..., params=...)` looks up the
action, defaults missing context and params to fresh empty dictionaries,
preserves supplied mapping objects including falsey mappings, and wraps
exceptions raised by the action in `ActionExecutionError`. Action error messages
include the relevant action name. `names()` returns the registered names sorted
alphabetically.

## Signal bridge

When `GENERAL_MANAGER["WORKFLOW_SIGNAL_BRIDGE"] = True`, manager create,
update, and delete signals are converted into workflow events. Nested
`GENERAL_MANAGER["WORKFLOW_SIGNAL_BRIDGE"]` takes precedence over a top-level
`WORKFLOW_SIGNAL_BRIDGE` setting, non-mapping `GENERAL_MANAGER` values are
ignored, and values are interpreted with `bool(...)`. The bridge connects to
`post_data_change` with a stable dispatch uid and `weak=False`; disconnecting
removes that receiver by dispatch uid.

The receiver ignores the Django signal sender and uses `instance` when present,
falling back to `previous_instance` from signal kwargs. Only `GeneralManager`
instances are converted, and unknown action values are ignored. Create and
update signals require at least one changed field after reserved signal keys and
private keys are removed. Delete signals can publish with only identification.
Events use the manager class name, identification from the signal payload or
current manager identification, source
`general_manager.cache.signals.post_data_change`, metadata `{"action": action}`,
and the manager event helper defaults for event ids and timestamps. Update
events receive `old_relevant_values`; when an old value is missing or `None`, the
bridge uses a non-`None` value from the previous simple-history row when
available and silently leaves the old value as `None` otherwise. Exceptions from
event registry publishing propagate to the signal caller. Service layers can
also publish events explicitly through `get_event_registry().publish(event)` when
signal coupling is not the right fit.

## Execution state

Workflow executions move through a bounded state model:

- `pending`, `running`, and `waiting` are active states.
- `completed`, `failed`, and `cancelled` are terminal states.
- `resume(...)` is valid only for waiting executions.
- `cancel(...)` is valid only for active executions.

`WorkflowDefinition` and `WorkflowExecution` are frozen dataclasses used by all
workflow backends. Their payload fields are `Mapping[str, object]` snapshots:
the dataclass attributes cannot be reassigned, but mappings are stored exactly
as provided and nested contents are not copied, deep-frozen, or JSON-normalized.
The dataclasses do not perform custom runtime validation; `WorkflowState` is the
static public state vocabulary. `WorkflowDefinition.workflow_id` is the backend
workflow identity, `version` defaults to `"1"`, and `handler` may return a
mapping or `None`. `WorkflowExecution` carries the state, input/output payloads,
correlation id, timestamps, error text, and metadata. Shared engine errors
include the relevant execution id, and invalid-state errors include the
attempted operation, current state, and comma-joined expected states.

Use `correlation_id` when the same event should not start duplicate active or completed executions for the same workflow definition.
For `CeleryWorkflowEngine`, `WorkflowDefinition.workflow_id` is the durable
workflow identity used with `correlation_id`. Reused executions ignore new input
and metadata. Returned `WorkflowExecution` objects are snapshots, not live ORM
objects; async workers may update the database immediately after `start()`
returns.

## Outbox and delivery

Production mode uses the workflow outbox to claim, route, retry, and dead-letter event delivery. The management commands `workflow_drain_outbox` and `workflow_replay_dead_letters` are operational tools for draining pending rows and replaying dead-lettered rows. Celery Beat can drain the outbox periodically when workflow beat settings are enabled.

The durable database rows behind production routing are explicit:
`WorkflowEventRecord` stores immutable event identity, type/name/source,
occurred-at time, payload, metadata, and creation time. `WorkflowOutbox` stores
one routable event row with pending/claimed/processed/failed/dead-letter status,
claim lease metadata, retry attempts, and the last error. `WorkflowDeliveryAttempt`
stores one per-handler idempotency/audit row for an event. `WorkflowExecutionRecord`
stores workflow execution state, JSON-like input/output payloads, correlation id,
timestamps, error text, and metadata. Treat these as operational records; prefer
registry and engine APIs for normal publishing and execution. The execution row
stores state as a raw database string; engines narrow it to the public
`WorkflowState` vocabulary when returning `WorkflowExecution` snapshots or
raising state-transition errors.
The workflow models keep explicit index names so Django's migration autodetector
does not generate spurious rename migrations for indexes that already exist in
the checked-in migration history.

Workflow outbox processing also emits optional telemetry through
`general_manager.workflow.telemetry`. When `prometheus-client` is installed,
helpers update counters, gauges, and histograms for backlog snapshots, claim
batches, processing duration, status transitions, delivery-attempt outcomes,
execution states, and duplicate suppression. When `prometheus-client` is not
installed, the helpers are no-ops.

## Backends

- For zero-setup development, use `LocalWorkflowEngine`.
- Durable production-oriented workflows use `CeleryWorkflowEngine`.
- Future remote orchestration support is represented by the `N8nWorkflowEngine`
  adapter stub.

`LocalWorkflowEngine` stores executions in process memory and is intended for
development and tests. It deep-copies input data, metadata, handler input, and
resume signals so returned execution snapshots are isolated from later caller
mutation. Workflows without handlers complete immediately with empty output.
Handler exceptions are captured as failed executions instead of propagating. A
non-empty `correlation_id` reuses the existing local execution for the same
workflow id, including failed executions; empty or missing correlation ids create
independent executions. Concurrent local starts with the same correlation key
wait for the in-flight start and return its completed or failed snapshot.
`resume(...)` is valid only for waiting executions and records a provided signal
under `metadata["resume_signal"]`. `cancel(...)` is valid only for active
executions and stores its reason in `error`. `status(...)`, `resume(...)`, and
`cancel(...)` raise `WorkflowExecutionNotFoundError` for unknown ids.

`N8nWorkflowEngine` stores `base_url` and optional `api_key` so settings can
describe a future n8n integration, but it does not perform remote orchestration
yet. `start(...)`, `resume(...)`, `cancel(...)`, and `status(...)` all raise
`N8nOperationNotImplementedError`, and the adapter makes no network requests.

Choose the backend in `GENERAL_MANAGER["WORKFLOW_ENGINE"]`, or rely on `WORKFLOW_MODE` defaults when they fit the environment. `configure_workflow_engine()` sets or clears the process-local active engine. `get_workflow_engine()` reuses that active engine when present; otherwise it reads Django settings and installs one process-local default engine for later calls.

`GENERAL_MANAGER["WORKFLOW_ENGINE"]` takes precedence over top-level `WORKFLOW_ENGINE`, including explicit `None` to clear the configured engine and use `WORKFLOW_MODE` defaults. Accepted values are:

- a `WorkflowEngine` instance
- a dotted import path to an engine instance, class, or zero-argument factory
- a workflow engine class or zero-argument factory callable
- a mapping with `class` and optional `options`, where options are passed as keyword arguments
- `None`, or a missing setting, to use the mode default

The mode default is `LocalWorkflowEngine` unless `WORKFLOW_MODE` is `production`, in which case it is `CeleryWorkflowEngine`. Import errors, factory errors, and constructor errors propagate. Mapping `options` must be a mapping, and non-`None` settings must resolve to a `WorkflowEngine`.

## Related guides

- [Build workflow event triggers](../howto/workflow_events.md)
- [Operate workflow outbox/dead letters](../howto/workflow_ops.md)
- [Workflow API reference](../api/workflow.md)
