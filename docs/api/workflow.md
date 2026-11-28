# Workflow API

Events describe changes, registries select handlers, and engines run workflow
definitions. Start with [workflow concepts](../concepts/workflow.md) or the
[event tutorial](../howto/workflow_events.md); use
[workflow operations](../howto/workflow_ops.md) for durable queues and replay.

The reference follows those layers: [events](#events-and-constructors),
[registries and settings](#registries-and-settings),
[execution](#workflow-definitions-and-execution), [actions](#actions),
and [backend configuration](#backend-configuration-and-signal-bridge).
Local backends keep process-local state; production defaults select database
routing and the Celery engine.

## Events and constructors

::: general_manager.workflow.event_registry.WorkflowEvent

::: general_manager.workflow.event_registry.WorkflowEventHandler

::: general_manager.workflow.event_registry.EventValidator

::: general_manager.workflow.event_registry.EventPredicate

::: general_manager.workflow.event_registry.DeadLetterHandler

::: general_manager.workflow.event_registry.RetryPredicate

::: general_manager.workflow.events.manager_created_event

::: general_manager.workflow.events.manager_updated_event

::: general_manager.workflow.events.manager_deleted_event

The manager event helpers are public constructors for common CRUD workflow
events. They return `WorkflowEvent` instances with canonical event types:
`general_manager.manager.created`, `general_manager.manager.updated`, and
`general_manager.manager.deleted`. If omitted, `event_id` is generated as a
UUID4 string and `occurred_at` is the current UTC time.

Payload contracts are stable:

- `manager_created_event(...)` stores `{"manager": name, "values": {...}}` and
  includes `identification` only when provided.
- `manager_updated_event(...)` stores `{"manager": name, "changes": {...}}`,
  where each changed field maps to `{"old": old_values.get(field), "new":
  value}`. Missing old values are represented as `None`.
- `manager_deleted_event(...)` stores `{"manager": name}` and includes
  `identification` only when provided.

The helpers shallow-copy top-level `values`, `changes`, `old_values`,
`identification`, and `metadata` mappings. Nested objects are not deep-copied or
JSON-normalized; database registries rely on Django field serialization.

## Registries and settings

::: general_manager.workflow.event_registry.EventRegistry

::: general_manager.workflow.event_registry.InMemoryEventRegistry

::: general_manager.workflow.event_registry.DatabaseEventRegistry

::: general_manager.workflow.event_registry.DurableHandlerRegistrationIdRequiredError

::: general_manager.workflow.event_registry.InvalidDurableHandlerRegistrationIdError

::: general_manager.workflow.event_registry.WorkflowHandlerRegistrationConflictError

::: general_manager.workflow.event_registry.InvalidWorkflowEventRegistryOptionsError

::: general_manager.workflow.event_registry.InvalidWorkflowEventRegistryError

::: general_manager.workflow.event_registry.configure_event_registry

::: general_manager.workflow.event_registry.configure_event_registry_from_settings

::: general_manager.workflow.event_registry.get_event_registry

::: general_manager.workflow.event_registry.publish_sync

::: general_manager.workflow.config.workflow_mode

::: general_manager.workflow.config.workflow_async_enabled

::: general_manager.workflow.config.workflow_durable_steps_enabled

::: general_manager.workflow.config.workflow_beat_enabled

::: general_manager.workflow.config.workflow_beat_outbox_interval_seconds

::: general_manager.workflow.config.workflow_beat_max_jitter_seconds

::: general_manager.workflow.config.workflow_outbox_batch_size

::: general_manager.workflow.config.workflow_outbox_process_chunk_size

::: general_manager.workflow.config.workflow_outbox_claim_ttl_seconds

::: general_manager.workflow.config.workflow_max_retries

::: general_manager.workflow.config.workflow_retry_backoff_seconds

::: general_manager.workflow.config.workflow_dead_letter_enabled

::: general_manager.workflow.config.workflow_delivery_running_timeout_seconds

Workflow config helpers read nested `GENERAL_MANAGER` values before top-level
Django settings. Non-mapping `GENERAL_MANAGER` values are ignored. Boolean
helpers use Python `bool(...)` coercion for explicit non-`None` values;
`WORKFLOW_ASYNC` and `WORKFLOW_BEAT_ENABLED` treat `None` as omitted and fall
back to mode defaults. `WORKFLOW_DEAD_LETTER_ENABLED` always uses `bool(...)`,
so an explicit `None` disables dead letters. Integer helpers parse with
`int(...)`, clamp to their documented minimums, and return their defaults when
parsing fails.
`workflow_mode()` normalizes with `str(...).strip().lower()` and accepts only
`local` or `production`. The helpers do not wrap unexpected errors raised while
reading settings attributes.

::: general_manager.workflow.models.WorkflowEventRecord

::: general_manager.workflow.models.WorkflowOutbox

::: general_manager.workflow.models.WorkflowExecutionRecord

::: general_manager.workflow.models.WorkflowStepRecord

::: general_manager.workflow.models.WorkflowDeliveryAttempt

The workflow persistence models are durable implementation records used by the
database event registry, outbox workers, and production workflow engines.
`WorkflowEventRecord` stores the event payload and metadata, `WorkflowOutbox`
tracks claim/retry/dead-letter routing state, `WorkflowDeliveryAttempt` records
per-handler idempotent delivery status, and `WorkflowExecutionRecord` stores
workflow execution state, input/output payloads, correlation ids, timestamps,
errors, and metadata. Application code should normally use the registry and
engine APIs instead of mutating these rows directly. Execution rows store state
as raw database strings; engines narrow those values to the public
`WorkflowState` vocabulary when returning `WorkflowExecution` snapshots or
raising state-transition errors. Workflow model `Meta.indexes` use explicit
names that match the checked-in migrations, including the initial workflow
schema migration and later scaling-index migration. Treat those names as
migration-owned metadata; renaming them creates an explicit index-rename
migration.

::: general_manager.workflow.tasks.configure_workflow_beat_schedule_from_settings

::: general_manager.workflow.tasks.publish_outbox_batch

::: general_manager.workflow.tasks.route_outbox_event

::: general_manager.workflow.tasks.route_outbox_claims_batch

::: general_manager.workflow.tasks.execute_workflow_handler

::: general_manager.workflow.tasks.resume_execution_task

::: general_manager.workflow.tasks.cancel_execution_task

::: general_manager.workflow.tasks.publish_durable_steps

::: general_manager.workflow.tasks.execute_workflow_step

::: general_manager.workflow.telemetry.set_outbox_snapshot

::: general_manager.workflow.telemetry.observe_outbox_claim_batch

::: general_manager.workflow.telemetry.observe_outbox_process_duration

::: general_manager.workflow.telemetry.increment_outbox_status

::: general_manager.workflow.telemetry.increment_delivery_attempt

::: general_manager.workflow.telemetry.increment_execution_state

::: general_manager.workflow.telemetry.increment_duplicate_suppression

::: general_manager.workflow.telemetry.extract_outbox_snapshot_payload

## Workflow definitions and execution

::: general_manager.workflow.engine.WorkflowDefinition

::: general_manager.workflow.engine.WorkflowExecution

::: general_manager.workflow.engine.WorkflowEngine

::: general_manager.workflow.engine.WorkflowState

::: general_manager.workflow.engine.WorkflowEngineError

::: general_manager.workflow.engine.WorkflowExecutionNotFoundError

::: general_manager.workflow.engine.WorkflowCancelledError

::: general_manager.workflow.engine.WorkflowInvalidStateError

`WorkflowDefinition` and `WorkflowExecution` are frozen dataclasses shared by
all workflow backends. Payload fields use `Mapping[str, object]`: the dataclass
itself is immutable, but mappings are stored exactly as provided and nested
contents are not copied, deep-frozen, or JSON-normalized. The dataclasses perform
no custom runtime validation beyond normal Python construction; `WorkflowState`
is the static public state vocabulary. `WorkflowDefinition.workflow_id` is the
backend workflow identity, and an optional handler receives input data and
returns a mapping or `None`. The shared state tuples group active,
active-or-completed, and terminal states for backend validation. Workflow engine
errors include the execution id. Not-found and cancelled errors use
`Workflow execution '<id>' was not found.` and `Workflow execution '<id>' is
cancelled.`; invalid-state errors include the attempted operation, current
state, and comma-joined expected state list.

## Actions

::: general_manager.workflow.actions.Action

::: general_manager.workflow.actions.ActionAlreadyRegisteredError

::: general_manager.workflow.actions.ActionExecutionError

::: general_manager.workflow.actions.ActionNotFoundError

::: general_manager.workflow.actions.ActionRegistry

Workflow actions are named side-effect adapters invoked from workflow handlers.
An `Action` implements `execute(context, params)` and returns a mapping result
or `None`. `ActionRegistry` is process-local and matches exact action name
strings; names are not normalized. `register(name, action)` stores the action
without runtime protocol validation and rejects duplicate names with
`ActionAlreadyRegisteredError` unless `replace=True` is passed. `get(name)`
raises `ActionNotFoundError` for missing names. `execute(name, ...)` applies
fresh empty dictionaries only when context or params are omitted, preserves
supplied mapping objects including falsey mappings, and wraps exceptions raised
by the action in `ActionExecutionError`. Action error messages include the
relevant action name. `names()` returns registered names sorted alphabetically.

## Backend configuration and signal bridge

::: general_manager.workflow.backend_registry.configure_workflow_engine

::: general_manager.workflow.backend_registry.configure_workflow_engine_from_settings

::: general_manager.workflow.backend_registry.get_workflow_engine

::: general_manager.workflow.signal_bridge.configure_workflow_signal_bridge_from_settings

::: general_manager.workflow.signal_bridge.connect_workflow_signal_bridge

::: general_manager.workflow.signal_bridge.disconnect_workflow_signal_bridge

The signal bridge connects `general_manager.cache.signals.post_data_change` to
workflow publishing. `connect_workflow_signal_bridge(registry=...)` optionally
installs the provided event registry, then connects a receiver with a stable
dispatch uid and `weak=False`; `disconnect_workflow_signal_bridge()` removes that
receiver by dispatch uid. `workflow_signal_bridge_enabled(settings)` reads
nested `GENERAL_MANAGER["WORKFLOW_SIGNAL_BRIDGE"]` before the top-level setting,
ignores non-mapping `GENERAL_MANAGER` values, uses `bool(...)` coercion, and
defaults to `False`. The receiver ignores the Django signal sender and uses
`instance` when present, otherwise `previous_instance` from signal kwargs.

The bridge publishes create, update, and delete events only for `GeneralManager`
instances; unknown actions are ignored. Create/update events require at least
one non-reserved changed field. Event payloads use the manager class name,
identification from the signal payload or current manager identification, source
`general_manager.cache.signals.post_data_change`, and metadata
`{"action": action}`. Event ids and timestamps use the manager event helper
defaults. Update events start with `old_relevant_values`; when an old value is
missing or `None`, the bridge uses a non-`None` value from the previous
simple-history row when available. Exceptions from `publish()` propagate to the
signal caller.

::: general_manager.workflow.backends.local.LocalWorkflowEngine

`LocalWorkflowEngine` is a process-local backend for development and tests.
`start(...)` deep-copies input data and metadata into the returned
`WorkflowExecution`, deep-copies handler input, completes immediately when no
handler is configured, and records handler exceptions as failed executions. A
non-empty `correlation_id` reuses an active or completed execution for the same
workflow id; failed and cancelled executions are fresh retry attempts. Concurrent
starts with the same local correlation key wait for the in-flight start and
return its snapshot. `resume(...)` only accepts waiting executions, stores a
deep copy of the supplied signal under `metadata["resume_signal"]` when a signal is provided,
and completes the execution. `cancel(...)` only accepts active executions and
stores the optional reason as `WorkflowExecution.error`. `status(...)`,
`resume(...)`, and `cancel(...)` raise `WorkflowExecutionNotFoundError` for
unknown ids; invalid states raise the documented workflow state errors. Stored
state is in memory only and is not shared across processes.

::: general_manager.workflow.backends.celery.CeleryWorkflowEngine

For legacy single-handler executions, `start(...)` captures a shallow `dict(...)`
copy of input before persistence.
Async dispatch uses that captured input after the outer transaction commits;
later top-level changes to the caller's mapping do not affect the task payload.
Rolling back the transaction or the savepoint containing `start(...)` discards
the task publication. Nested objects are not deep-copied or JSON-normalized.
For step-based executions, input and metadata are deep-copied before persistence.

Correlation reuse checks for an existing active or completed execution before
inserting. Protection against concurrent inserts also requires database support
for the conditional unique constraint. MariaDB does not enforce that constraint,
so concurrent starts can create duplicate executions for the same correlation id.

::: general_manager.workflow.backends.n8n.N8nWorkflowEngine

`N8nWorkflowEngine` is a placeholder adapter for future remote orchestration.
The constructor stores `base_url` and optional `api_key`, but `start(...)`,
`resume(...)`, `cancel(...)`, and `status(...)` are not implemented and raise
`N8nOperationNotImplementedError`. The current adapter performs no network
requests.

## Versioned durable execution

::: general_manager.workflow.engine.WorkflowStep

::: general_manager.workflow.engine.WorkflowStepContext

::: general_manager.workflow.engine.WorkflowStepConfigurationError

::: general_manager.workflow.engine.WorkflowSignalError

`WorkflowStep(key, handler_path, wait_for_signal=False, max_attempts=3,
claim_seconds=300, retry_seconds=15)` declares one ordered step. Keys must be
unique, nonblank and at most 128 characters; handler paths must be importable
callables of at most 512 characters. Attempts must be 1–100, lease seconds
1–86400 and retry seconds 0–86400. A step handler accepts one
`WorkflowStepContext` and returns a string-keyed JSON mapping or `None` (treated
as `{}`). Invalid output or handler failure consumes an attempt. Context fields
are `execution_id`, `workflow_id`, pinned `version`, `step`, stable
`idempotency_key`, `input_data`, and optional `signal` data.

`WorkflowDefinition.steps` defaults to `()`. A nonempty tuple requires Celery,
async mode, and `WORKFLOW_DURABLE_STEPS_ENABLED`; it cannot be combined with a
legacy handler or `metadata["handler_path"]`. Its version must be nonblank and
at most 128 characters. Invalid plans raise `WorkflowStepConfigurationError`
before insertion, except when a correlation ID first reuses an existing run.
`WorkflowExecution.version` is the pinned version for step-based runs and
`None` for legacy runs. First-step state is `waiting` for a signal gate and
`pending` otherwise. Step input and metadata are deep-copied at start.

`CeleryWorkflowEngine.resume(execution_id, signal)` returns an execution
snapshot. A durable signal is `{"step": key, "id": decision_id, "data": {…}}`;
`data` defaults to `{}`. The step and ID must be nonblank strings, the ID at
most 128 characters, and neither may contain U+0000. Data must be a JSON
mapping with string top-level keys, finite numbers, and no U+0000 or unpaired
surrogates in decoded strings or keys. Invalid signals raise
`WorkflowSignalError` and do not release the gate. Repeating the same ID and
canonical payload for that step returns the current snapshot; reusing the ID
with different data raises `WorkflowSignalError`. A different decision for a
step that is not waiting raises `WorkflowInvalidStateError`. Unknown runs raise
`WorkflowExecutionNotFoundError`; cancelled runs raise `WorkflowCancelledError`.

`resume_execution_task(execution_id, signal=None)` returns `True` when a waiting
run accepts a signal and `False` when it is not waiting. It raises
`WorkflowExecutionNotFoundError` for an unknown run and propagates durable
signal and state errors. Unlike the engine method, it checks the run is waiting
before applying the durable signal, so a replay after it has left waiting returns
`False`. Legacy executions complete and store a supplied signal in metadata.

`publish_durable_steps()` dispatches up to the configured outbox process chunk
size of due or expired-lease steps and returns the number successfully
published, not the number completed. `execute_workflow_step(step_id)` returns
`None`: it claims due work, runs the pinned handler, and checkpoints or retries
it. A missing, stale, or non-due claim is a no-op. `WorkflowStepRecord` is the
migration-owned persistence record for operations; application code should use
the engine rather than mutate records.

`workflow_durable_steps_enabled(django_settings=settings)` returns a boolean;
nested `GENERAL_MANAGER` settings take precedence over top-level settings, and
the default is `False`. With Beat, async mode, and durable steps enabled,
`configure_workflow_beat_schedule_from_settings()` adds
`general_manager.workflow.publish_durable_steps` on `workflow.events`, using
the outbox interval and jitter. Disabling either async or durable steps removes
that entry when the schedule is configured successfully.

[Workflow concepts](../concepts/workflow.md#versioned-durable-steps), the
[operations guide](../howto/workflow_ops.md#run-versioned-approval-workflows),
and the [approval recipe](../examples/workflow_durable_approval.md) cover setup,
recovery, and side effects.
