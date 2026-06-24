# Workflow Operations

This guide covers production operations for the workflow outbox and dead-letter flows.

## Drain outbox

Run:

```bash
python manage.py workflow_drain_outbox
```

This claims and routes pending outbox records.

The command accepts no custom options, calls `publish_outbox_batch()`, returns
`None`, and prints a styled success line:
`Dispatched <count> outbox record(s) for routing.` The singular label
`outbox record` is used only when the claimed count is exactly `1`; every other
count uses `outbox records`. Exceptions from claiming, telemetry, inline
routing, or Celery dispatch are not wrapped by the command.

Operator notes:
- claiming an outbox row does not consume its retry budget by itself.
- retry counters advance only when routing or handler execution actually fails.
- stale claims can be reclaimed after the configured claim TTL without dead-lettering the event just because a worker lease expired.
- `publish_outbox_batch()` returns the number of rows claimed for routing, not the number of handlers that completed.
- when Celery is unavailable, claimed rows are routed inline; otherwise one batch task is queued.
- `route_outbox_claims_batch()` logs per-row exceptions and continues routing the rest of the batch.
- `WORKFLOW_OUTBOX_PROCESS_CHUNK_SIZE` controls how many rows one drain call claims.
- empty claim batches still update outbox snapshot telemetry and do not enqueue a route task.
- if Celery `.delay(...)`, claiming, telemetry, or inline routing fails, the task exception propagates to the caller or Celery worker.
- `route_outbox_event()` returns `False` for missing/processed rows, stale or missing claim ownership, rows with no applicable handlers, duplicate in-progress delivery attempts, and route failures; unexpected registry exceptions propagate.
- malformed claim entries in `route_outbox_claims_batch()` are logged and skipped; duplicate claim entries are attempted in list order and rely on registry ownership checks.

In production, run a dedicated Celery Beat process to trigger periodic drain:

```bash
celery -A <your_project> beat -l info
```

The workflow library registers a Beat schedule for `publish_outbox_batch` when:
- `WORKFLOW_BEAT_ENABLED=True`
- Celery is installed and configured in the host app

The Beat helper returns `False` when workflow beat is disabled or Celery is not
installed, or Celery imported but `current_app` is `None`. When it returns
`True`, it updates the Celery app's `beat_schedule` entry named
`general_manager.workflow.publish_outbox_batch`.
`WORKFLOW_BEAT_OUTBOX_INTERVAL_SECONDS` defaults to `5`;
`WORKFLOW_BEAT_MAX_JITTER_SECONDS` defaults to `2` and samples one
fractional-second jitter value when the schedule is configured, not on each Beat
tick. The generated entry has task name
`general_manager.workflow.tasks.publish_outbox_batch`, numeric `schedule`
seconds, no args/kwargs, and options `{"queue": "workflow.events"}`. Returning
`False` does not remove an existing Beat entry. The helper does not configure
Celery task retries; Celery app access or assignment errors propagate.

## Execution tasks

`execute_workflow_handler(execution_id, handler_path, input_data)` imports a
synchronous handler, passes `dict(input_data or {})`, and stores `dict(result or
{})` as output on success. Non-pending executions are left unchanged. Missing
execution ids raise `WorkflowExecutionNotFoundError`; handler import/call/output
conversion failures are captured as failed execution records.

`resume_execution_task(execution_id, signal)` completes only waiting executions
and stores `signal` in metadata under `resume_signal`. It does not change output
data. `cancel_execution_task(execution_id, reason)` cancels active executions:
`pending`, `running`, or `waiting`. Both tasks return `False` for existing
records in the wrong state and raise `WorkflowExecutionNotFoundError` for missing
records.

These task functions do not configure Celery retries. Unexpected persistence
errors propagate to the caller or Celery worker.

Backlog semantics:
- retry-ready rows move back to `failed` with a future `available_at` timestamp and are reclaimed when that backoff window expires.
- dead-lettering happens after repeated real processing failures, not after repeated claim churn.

## Replay dead letters

Run:

```bash
python manage.py workflow_replay_dead_letters --limit 500
```

This moves dead-letter outbox rows back to `pending`.

Use replay when the underlying handler or dependency issue is fixed; replay does
not bypass normal retry and claim rules. The command accepts `--limit`, defaults
to `1000`, and selects the oldest dead-letter outbox rows by `created_at`.
Positive limits cap the selected rows; `0` or a negative limit requeues no rows.
Command-line values are parsed as integers by Django's argument parser, and
programmatic `call_command(...)` use must pass a non-boolean integer. Invalid
programmatic limits raise `CommandError` with the message
`Workflow replay limit must be an integer.` before any database query runs.

The command selects `WorkflowOutbox` ids whose status is
`WorkflowOutbox.STATUS_DEAD_LETTER`, then updates only those selected ids. Each
replayed row is moved to `WorkflowOutbox.STATUS_PENDING`, clears `last_error`,
`claim_token`, and `claimed_at`, and resets `attempts` to `0`. A non-positive
limit prints the styled success line `Requeued 0 dead-letter rows.` and returns
before any database query. If a positive limit finds no dead-letter rows, the
command prints `No dead-letter outbox rows found.`. Otherwise it prints a styled
success line, `Requeued <count> dead-letter row(s).`, using `dead-letter row`
only when the updated count is exactly `1`. Database query and update errors are
not wrapped by the command.

## Recommended metrics

- pending outbox count
- oldest pending outbox age
- failed/dead-letter outbox count
- workflow execution state totals
- outbox claim batch size
- outbox process duration
- delivery attempt status totals
- duplicate suppression totals

Workflow telemetry helpers live in `general_manager.workflow.telemetry` and are
called by the production registry and Celery task helpers. They register
Prometheus collectors when `prometheus-client` is importable; otherwise every
recording helper is a no-op. Collectors are registered when the module is
imported. Normal Python import caching avoids duplicate registration during
repeated Django app initialization in one process; explicit module reloads follow
the Prometheus client's duplicate-registration behavior. The helpers do not add
their own locking and rely on the metrics backend for thread/process semantics.

Use `set_outbox_snapshot(pending_count=..., oldest_pending_age_seconds=...)`
when an operations command or scheduler has a fresh outbox backlog snapshot.
The helper overwrites gauges and clamps negative values to zero before recording.
Use `observe_outbox_claim_batch(size)` for claimed batch sizes and
`observe_outbox_process_duration(status=..., duration_seconds=...)` for
per-row processing latency; both clamp negative numeric observations to zero.

Status/state counter helpers accept the status strings produced by the workflow
models and tasks. The helpers do not validate a fixed vocabulary. Spaces and
colons are normalized to `_` before the value is sent to Prometheus, so avoid
passing request ids, user ids, or other high-cardinality data as labels. If a
caller bypasses the type hints and passes a non-string label while metrics are
enabled, the helper raises `TypeError`; when metrics are unavailable, the helper
returns before validating labels.

`extract_outbox_snapshot_payload(snapshot)` is a small parser for serialized
snapshot mappings. It reads `pending_count` and
`oldest_pending_age_seconds`, treats missing or any Python-falsey value as
absent, and returns `(int, float)`. That means `None`, `0`, `0.0`, `False`, empty
strings/bytes, and empty containers all default to `(0, 0.0)`; custom
truth-value exceptions propagate. Truthy `pending_count` values are coerced with
`int(...)`, so floats truncate, `True` becomes `1`, `"1.2"` raises `ValueError`,
and non-finite floats raise `OverflowError`. Truthy
`oldest_pending_age_seconds` values are coerced with `float(...)`, so Python's
normal `nan`/`inf` handling applies. Non-coercible objects raise `TypeError`.
Parsed negative values are returned unchanged so callers can decide whether to
preserve or clamp them before recording.

The telemetry helpers do not catch exceptions raised by the installed metrics
backend. If a custom Prometheus registry or collector fails, that exception
propagates to the workflow operation that attempted to record the metric.
