# Workflow API

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

::: general_manager.workflow.event_registry.EventRegistry

::: general_manager.workflow.event_registry.InMemoryEventRegistry

::: general_manager.workflow.event_registry.DatabaseEventRegistry

::: general_manager.workflow.event_registry.InvalidWorkflowEventRegistryOptionsError

::: general_manager.workflow.event_registry.InvalidWorkflowEventRegistryError

::: general_manager.workflow.event_registry.configure_event_registry

::: general_manager.workflow.event_registry.configure_event_registry_from_settings

::: general_manager.workflow.event_registry.get_event_registry

::: general_manager.workflow.event_registry.publish_sync

::: general_manager.workflow.config.workflow_mode

::: general_manager.workflow.config.workflow_async_enabled

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

::: general_manager.workflow.telemetry.set_outbox_snapshot

::: general_manager.workflow.telemetry.observe_outbox_claim_batch

::: general_manager.workflow.telemetry.observe_outbox_process_duration

::: general_manager.workflow.telemetry.increment_outbox_status

::: general_manager.workflow.telemetry.increment_delivery_attempt

::: general_manager.workflow.telemetry.increment_execution_state

::: general_manager.workflow.telemetry.increment_duplicate_suppression

::: general_manager.workflow.telemetry.extract_outbox_snapshot_payload

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
non-empty `correlation_id` reuses the existing local execution for the same
workflow id, including failed executions. Concurrent starts with the same local
correlation key wait for the in-flight start and return its completed or failed
snapshot. `resume(...)` only accepts waiting executions, stores a deep copy of
the supplied signal under `metadata["resume_signal"]` when a signal is provided,
and completes the execution. `cancel(...)` only accepts active executions and
stores the optional reason as `WorkflowExecution.error`. `status(...)`,
`resume(...)`, and `cancel(...)` raise `WorkflowExecutionNotFoundError` for
unknown ids; invalid states raise the documented workflow state errors. Stored
state is in memory only and is not shared across processes.

::: general_manager.workflow.backends.celery.CeleryWorkflowEngine

::: general_manager.workflow.backends.n8n.N8nWorkflowEngine

`N8nWorkflowEngine` is a placeholder adapter for future remote orchestration.
The constructor stores `base_url` and optional `api_key`, but `start(...)`,
`resume(...)`, `cancel(...)`, and `status(...)` are not implemented and raise
`N8nOperationNotImplementedError`. The current adapter performs no network
requests.
