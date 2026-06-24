# Workflow Events Tutorial

This tutorial shows how to configure the workflow engine in `settings.py`, trigger workflow events from manager updates, and route events using human-readable names and predicates.

## 1. Configure the workflow engine

Add workflow settings in `settings.py`:

```python
GENERAL_MANAGER = {
    "WORKFLOW_MODE": "production",
    "WORKFLOW_ENGINE": {
        "class": "general_manager.workflow.backends.celery.CeleryWorkflowEngine",
        "options": {},
    },
    "WORKFLOW_EVENT_REGISTRY": {
        "class": "general_manager.workflow.event_registry.DatabaseEventRegistry",
        "options": {},
    },
    "WORKFLOW_SIGNAL_BRIDGE": True,
    "WORKFLOW_ASYNC": True,
    "WORKFLOW_OUTBOX_BATCH_SIZE": 100,
    "WORKFLOW_OUTBOX_PROCESS_CHUNK_SIZE": 50,
    "WORKFLOW_BEAT_ENABLED": True,
    "WORKFLOW_BEAT_OUTBOX_INTERVAL_SECONDS": 5,
    "WORKFLOW_BEAT_MAX_JITTER_SECONDS": 2,
    "WORKFLOW_DELIVERY_RUNNING_TIMEOUT_SECONDS": 300,
    "WORKFLOW_MAX_RETRIES": 3,
    "WORKFLOW_DEAD_LETTER_ENABLED": True,
}
```

- `WORKFLOW_ENGINE` selects the orchestration backend.
- `WORKFLOW_MODE` controls defaults (`local` vs `production`).
- `WORKFLOW_EVENT_REGISTRY` selects in-memory vs durable DB routing.
- `WORKFLOW_SIGNAL_BRIDGE=True` enables automatic event creation from manager mutation signals.
- `WORKFLOW_BEAT_*` controls periodic outbox draining via Celery Beat.
- `WORKFLOW_OUTBOX_PROCESS_CHUNK_SIZE` controls per-task claimed outbox batch processing size.
- `WORKFLOW_DELIVERY_RUNNING_TIMEOUT_SECONDS` defines stale-running takeover threshold for delivery attempts.

`WORKFLOW_ENGINE` accepts an engine instance, class, factory, dotted import path, or a mapping with `class` plus `options`. The nested `GENERAL_MANAGER["WORKFLOW_ENGINE"]` value takes precedence over a top-level `WORKFLOW_ENGINE` setting, including explicit `None` to clear the configured engine and use the `WORKFLOW_MODE` default.

`WORKFLOW_EVENT_REGISTRY` accepts the same shapes for event registries: registry
instance, class, factory, dotted import path, or a mapping with `class` plus
`options`. The nested `GENERAL_MANAGER["WORKFLOW_EVENT_REGISTRY"]` value takes
precedence over a top-level `WORKFLOW_EVENT_REGISTRY` setting, including
explicit `None` to use the `WORKFLOW_MODE` default. In local mode the default is
`InMemoryEventRegistry`; in production mode it is `DatabaseEventRegistry`.
Mapping `options` must be a mapping, and non-`None` values must resolve to an
`EventRegistry`.
`WORKFLOW_MODE` is stripped and lowercased. `"production"` selects the database
registry default; `"local"` and unrecognized values select the in-memory default.
`InMemoryEventRegistry` accepts `dead_letter_handler` and `max_seen_event_ids`
options. `DatabaseEventRegistry` has no constructor options.

`WORKFLOW_SIGNAL_BRIDGE` reads nested `GENERAL_MANAGER` values before the
top-level setting and uses Python `bool(...)` coercion. Enabling it connects the
`post_data_change` receiver with a stable dispatch uid. The receiver ignores the
Django signal sender and uses `instance` when present, otherwise
`previous_instance`. The bridge publishes create/update/delete events only for
`GeneralManager` instances and ignores unknown action values. Create and update
events are skipped when the signal contains no changed fields after reserved
keys are removed. Delete events include identification when available. Event ids
and timestamps use the manager event helper defaults. Update events use
`old_relevant_values`; when an old value is missing or `None`, the bridge uses a
non-`None` value from the previous simple-history row when available and
otherwise leaves the old value as `None`. Exceptions from event publishing
propagate to the signal caller.

Workflow config helpers read nested `GENERAL_MANAGER` values before top-level
Django settings. Non-mapping `GENERAL_MANAGER` values are ignored. Explicit
non-`None` boolean settings use Python `bool(...)` coercion, so non-empty
strings are truthy. `WORKFLOW_ASYNC` and `WORKFLOW_BEAT_ENABLED` treat `None` as
omitted and fall back to the mode defaults. `WORKFLOW_DEAD_LETTER_ENABLED`
always uses `bool(...)`, so an explicit `None` disables dead letters. Unexpected
errors from settings attribute access are not wrapped. Integer settings parse
with `int(...)`, clamp to their minimums, and fall back to their defaults on
invalid values:

- `WORKFLOW_BEAT_OUTBOX_INTERVAL_SECONDS`: default `5`, minimum `1`
- `WORKFLOW_BEAT_MAX_JITTER_SECONDS`: default `2`, minimum `0`
- `WORKFLOW_OUTBOX_BATCH_SIZE`: default `100`, minimum `1`
- `WORKFLOW_OUTBOX_PROCESS_CHUNK_SIZE`: default `50`, minimum `1`
- `WORKFLOW_OUTBOX_CLAIM_TTL_SECONDS`: default `300`, minimum `1`
- `WORKFLOW_MAX_RETRIES`: default `3`, minimum `0`
- `WORKFLOW_RETRY_BACKOFF_SECONDS`: default `5`, minimum `1`
- `WORKFLOW_DELIVERY_RUNNING_TIMEOUT_SECONDS`: default `300`, minimum `1`

Important async contract:
- in `WORKFLOW_ASYNC=True`, workflow handlers must be importable top-level callables.
- nested/local handlers are marked failed with an explicit error instead of executing inline.
- `CeleryWorkflowEngine.start(...)` validates the handler path before enqueueing.
  A workflow with neither `workflow.handler` nor `metadata["handler_path"]` is
  intentionally handlerless and completes immediately with `{}` output. A
  workflow with an inline/local handler that cannot be represented as an import
  path is malformed for async mode and returns a failed execution record.
  Non-callable imports, import failures, and missing Celery support also return
  failed execution records instead of raising from `start(...)`.
- async handler import, call, and output-conversion failures inside the worker
  are captured later by `execute_workflow_handler` as failed executions.
- in `WORKFLOW_ASYNC=False`, the engine runs the handler inline and returns a
  snapshot after inline execution. Handler exceptions and invalid truthy handler
  outputs are captured as failed executions.
- in `WORKFLOW_ASYNC=True`, `start(...)` returns the persisted pre-worker
  snapshot, usually `pending`, after queueing the handler task on transaction
  commit. The worker may update the row immediately after return; call
  `status(execution_id)` when you need fresh state.

Execution state contract:
- `resume(...)` is only valid for executions in `waiting`.
- `cancel(...)` is only valid for active executions in `pending`, `running`, or `waiting`.
- completed, failed, and cancelled executions are treated as terminal and are not overwritten by later resume/cancel requests.
- `WorkflowDefinition` and `WorkflowExecution` are frozen dataclasses, but
  payload and metadata mappings are stored exactly as provided and are not
  copied, deep-frozen, or JSON-normalized by the dataclasses.
- workflow dataclasses rely on static `WorkflowState` typing and do not reject
  invalid runtime values beyond normal Python construction.
- `WorkflowDefinition.workflow_id` is the backend workflow identity, `version`
  defaults to `"1"`, and `handler` may return a mapping or `None`.
- workflow error messages include the execution id; invalid-state errors also
  include the attempted operation, current state, and expected states.
- `resume(...)` and `cancel(...)` are inline database updates on the engine.
  They do not enqueue Celery tasks. `resume(...)` changes `waiting` to
  `completed`, stores a truthy signal in `metadata["resume_signal"]`, and leaves
  output data unchanged. `cancel(...)` changes an active execution to
  `cancelled` and stores `reason` in `WorkflowExecution.error`.
- `resume(...)`, `cancel(...)`, and `status(...)` raise
  `WorkflowExecutionNotFoundError` for unknown ids. `resume(...)` raises
  `WorkflowCancelledError` for `cancelled` rows and `WorkflowInvalidStateError`
  for `pending`, `running`, `completed`, or `failed` rows. `cancel(...)` raises
  `WorkflowCancelledError` for already-`cancelled` rows and
  `WorkflowInvalidStateError` for `completed` or `failed` rows.
- the engine returns `WorkflowExecution` snapshots. They are not live ORM
  objects and may become stale after async workers update the database.

For local zero-setup mode, use:

```python
GENERAL_MANAGER = {
    "WORKFLOW_MODE": "local",
    "WORKFLOW_SIGNAL_BRIDGE": True,
}
```

Local engine behavior:
- `LocalWorkflowEngine` keeps executions in process memory. It is useful for
  development and tests, not cross-process durability.
- `start(...)` deep-copies input data and metadata into the execution snapshot
  and deep-copies the input passed to the handler.
- workflows without handlers complete immediately with `{}` output.
- handler exceptions are captured as failed executions with `error` set to the
  exception text.
- a non-empty `correlation_id` reuses an existing execution for the same
  workflow id, including failed executions; empty or omitted correlation ids
  create independent executions.
- concurrent local starts with the same correlation key wait for the in-flight
  start and return its completed or failed snapshot.
- `resume(...)` only works for waiting executions and stores a provided signal in
  `metadata["resume_signal"]`, even when the signal mapping is empty or falsey.
- `cancel(...)` only works for active executions and stores the optional reason
  in `WorkflowExecution.error`.

`N8nWorkflowEngine` is present only as a future adapter stub. It stores
`base_url` and optional `api_key`, but `start(...)`, `resume(...)`,
`cancel(...)`, and `status(...)` raise `N8nOperationNotImplementedError` and do
not call n8n yet.

## 2. Register a workflow trigger with a readable event name

You can register against:
- canonical type (for example `general_manager.manager.updated`), or
- human-readable event name (for example `manager_updated`).

Route keys are exact strings. Keys containing `.` match `event_type`; keys
without `.` match `event_name`. There is no wildcard, prefix, whitespace, or
case normalization. Keep readable event names dot-free to avoid routing them as
event types.

Use a `when` filter to route only relevant updates:

```python
from general_manager.workflow.event_registry import InMemoryEventRegistry

event_registry = InMemoryEventRegistry()
event_registry.register(
    "manager_updated",
    handler=start_project_status_workflow,
    when=lambda event: (
        event.payload.get("manager") == "Project"
        and event.payload.get("changes", {}).get("status", {}).get("new") == "active"
    ),
)
```

## 3. Use typed event constructors

GeneralManager workflow helpers provide compact, readable event creation:

```python
from general_manager.workflow.events import (
    manager_created_event,
    manager_deleted_event,
    manager_updated_event,
)

event = manager_updated_event(
    manager="Project",
    identification={"id": 42},
    changes={"status": "active"},
    old_values={"status": "draft"},
    event_name="project_status_changed",
)
```

The resulting update payload stores per-field diffs:

```python
{
    "manager": "Project",
    "identification": {"id": 42},
    "changes": {
        "status": {"old": "draft", "new": "active"}
    }
}
```

Create and delete events use the same event envelope:

```python
created = manager_created_event(
    manager="Project",
    identification={"id": 42},
    values={"status": "draft"},
)
deleted = manager_deleted_event(manager="Project", identification={"id": 42})
```

Helper behavior:
- `manager_created_event(...)` uses event type
  `general_manager.manager.created` and stores `manager`, `values`, and optional
  `identification`.
- `manager_updated_event(...)` uses event type
  `general_manager.manager.updated` and stores `manager`, per-field `changes`,
  and optional `identification`. Fields missing from `old_values` get
  `{"old": None, "new": value}`.
- `manager_deleted_event(...)` uses event type
  `general_manager.manager.deleted` and stores `manager` plus optional
  `identification`.
- all helpers default `event_id` to a UUID4 string and `occurred_at` to the
  current UTC time.
- `values`, `changes`, `old_values`, `identification`, and `metadata` are
  shallow-copied at the top level. Nested values are left as-is and must still be
  serializable when using the database registry.

## 4. Start a workflow and call an action

Inside your handler, start a workflow and execute an action:

```python
from general_manager.workflow.actions import ActionRegistry
from general_manager.workflow.backend_registry import get_workflow_engine
from general_manager.workflow.engine import WorkflowDefinition

action_registry = ActionRegistry()


class SendEmailAction:
    def execute(self, context, params):
        return {"sent": True, "to": params["to"], "project_id": context["project_id"]}


action_registry.register("send_project_email", SendEmailAction())


def project_status_workflow_handler(input_data):
    return action_registry.execute(
        "send_project_email",
        context={"project_id": input_data["project_id"]},
        params={"to": "ops@example.test"},
    )

def start_project_status_workflow(event):
    workflow = WorkflowDefinition(
        workflow_id="project_status_email",
        handler=project_status_workflow_handler,
    )
    engine = get_workflow_engine()
    engine.start(
        workflow,
        input_data={
            "event_id": event.event_id,
            "project_id": event.payload["identification"]["id"],
            "old_status": event.payload["changes"]["status"]["old"],
            "new_status": event.payload["changes"]["status"]["new"],
        },
        correlation_id=event.event_id,
    )
```

Correlation behavior:
- `correlation_id` is a durable deduplication key scoped to
  `WorkflowDefinition.workflow_id`, which is the durable workflow identity.
- starting the same workflow with the same `correlation_id` reuses the existing execution record instead of creating a second one while the original execution is still active or already completed.
- after a failed execution, the same `correlation_id` can start a fresh execution again; failed attempts are not treated as the durable winning record.
- when an existing execution is reused, new `input_data` and `metadata` are
  ignored; callers receive a snapshot of the existing row.
- concurrent starts with the same `(workflow_id, correlation_id)` rely on the
  database uniqueness constraint. If an insert races, the loser reloads the
  active/completed execution when available; otherwise the database
  `IntegrityError` propagates.

Payload and metadata behavior:
- `input_data` and `metadata` are top-level `Mapping[str, object]` values and
  are copied with `dict(...)` before persistence.
- nested values are accepted or rejected by the configured Django `JSONField`;
  database serialization errors propagate.
- when a handler path is available, `CeleryWorkflowEngine` writes
  `metadata["handler_path"]` with the import path used for dispatch, replacing a
  user-provided key of the same name.

Action registry behavior:
- a registry is process-local and matches exact action name strings; it does not
  strip, lowercase, or otherwise normalize names.
- action names are unique. Registering the same name twice raises
  `ActionAlreadyRegisteredError` unless you pass `replace=True`.
- registration trusts the type hints and does not validate the action object at
  runtime.
- `execute(name, context=..., params=...)` defaults only omitted context or
  params to fresh `{}` instances and passes supplied mappings through to the
  action, even when a custom mapping is falsey.
- missing action names raise `ActionNotFoundError`.
- exceptions raised by the action are wrapped in `ActionExecutionError` with the
  original exception available as `__cause__`.
- action error messages include the relevant action name.
- `names()` returns registered action names sorted alphabetically.

## 5. Trigger without signals (explicit publish)

If you do not want signal-based triggering, publish events directly from your service layer, mutation, or view:

```python
from general_manager.workflow.event_registry import get_event_registry
from general_manager.workflow.events import manager_updated_event

event = manager_updated_event(
    manager="Project",
    identification=project.identification,
    changes={"status": "active"},
    old_values={"status": "draft"},
    event_name="project_status_changed",
)
get_event_registry().publish(event)
```

This keeps trigger logic explicit while using the same routing and workflow engine contracts.

`DatabaseEventRegistry.publish()` returns `False` in async mode because it only
persists the event and schedules outbox work after commit. Use
`publish_sync(event)` when the current code path must persist and route handlers
inline against the configured registry. Database `publish_sync()` commits event
persistence before routing. Duplicate event ids are not inserted again, but
routing still runs against the event object you passed; other persistence errors
propagate and prevent routing.

## 6. Handle trigger failures with retries and dead-letter hooks

`InMemoryEventRegistry.register(...)` supports per-handler retries and dead-letter callbacks:

```python
dead_letters: list[tuple[str, str]] = []

event_registry.register(
    "manager_updated",
    handler=start_project_status_workflow,
    when=lambda event: event.payload.get("manager") == "Project",
    retries=2,
    dead_letter_handler=lambda event, exc: dead_letters.append(
        (event.event_id, str(exc))
    ),
)
```

Behavior:
- handler exceptions are isolated and do not crash event publishing for other handlers.
- failed handlers are retried up to `retries`.
- with `retry_on=None`, every handler exception is retryable until the attempt budget is exhausted.
- validator failures are dead-lettered without retry.
- `when` predicate exceptions and dead-letter handler exceptions are not wrapped by the in-memory registry; keep those callables small and explicit.
- if a `retry_on` predicate raises, that exception propagates in the in-memory registry and becomes an outbox failure during database outbox processing.
- registration-level dead-letter handlers take precedence over the in-memory registry-level handler.
- after final failure, the dead-letter hook receives `(event, exception)`.

Registration behavior:
- identical registrations for the same event are deduplicated at registration time.
- "identical" means the event key, handler, validator, predicate, retry count, retry predicate, and dead-letter handler resolve to the same registration identity.
- callable identity uses `__module__ + "." + __qualname__` when available and `repr(...)` otherwise; the retry count is compared after clamping negative values to zero.
- different `when`, `validator`, retry, or dead-letter settings still create separate registrations.
- handlers run in registration order inside each route bucket.
- if an event matches both type and name routes, type-route handlers run first.
- registrations skipped by `when=False` are not considered applicable for the publish return value.

Publish behavior:
- synchronous registries return `True` if at least one applicable handler completed, even when another matching handler failed.
- duplicate `event_id` publishes return `False`.
- duplicate async database publishes return `False` without creating or routing another outbox row.
- `InMemoryEventRegistry` marks an id as seen before routing; handler failure does not make the same id publishable again.
- `max_seen_event_ids` is clamped to at least `1`; the oldest id is evicted when the bounded cache is full.
- `get_event_registry()` returns an import-time in-memory registry until you configure it explicitly or from settings.

Runtime-boundary notes:
- the event registry trusts the type hints; invalid non-callable handlers or predicates fail when routed, and invalid event keys/retry counts may fail during registration.
- `WorkflowEvent` is frozen but does not deep-freeze `payload` or `metadata`.
- database registries rely on Django `JSONField` and `DateTimeField` behavior for payload serialization, timezone handling, and timestamp precision.
- in-memory handlers run outside the registry lock; concurrent registrations affect later publishes, not a handler snapshot already being routed.
- dotted registry import strings are resolved first: imported classes are instantiated, imported registry instances are reused, and imported non-registry callables are called as factories.
- database outbox failures increment the outbox attempt count; failed rows become due again after `WORKFLOW_RETRY_BACKOFF_SECONDS * attempts` until `WORKFLOW_MAX_RETRIES` moves them to `dead_letter` when dead letters are enabled.
- `WORKFLOW_DEAD_LETTER_ENABLED` defaults to `True` and is interpreted with `bool(...)`; when disabled, max-attempt rows stay failed and continue to be scheduled with backoff.
- `WORKFLOW_MAX_RETRIES` is parsed with `int(...)`, clamped to at least `0`, defaults to `3`, and counts total failed outbox processing calls before dead-letter transition.
- `WORKFLOW_RETRY_BACKOFF_SECONDS` is parsed with `int(...)`, clamped to at least `1`, and defaults to `5`.
- if one applicable database handler succeeds and another fails in the same processing call, the failed handler is dead-lettered but the outbox row is processed because at least one route completed.
