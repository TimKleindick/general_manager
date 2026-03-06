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

Important async contract:
- in `WORKFLOW_ASYNC=True`, workflow handlers must be importable top-level callables.
- nested/local handlers are marked failed with an explicit error instead of executing inline.

Execution state contract:
- `resume(...)` is only valid for executions in `waiting`.
- `cancel(...)` is only valid for active executions in `pending`, `running`, or `waiting`.
- completed, failed, and cancelled executions are treated as terminal and are not overwritten by later resume/cancel requests.

For local zero-setup mode, use:

```python
GENERAL_MANAGER = {
    "WORKFLOW_MODE": "local",
    "WORKFLOW_SIGNAL_BRIDGE": True,
}
```

## 2. Register a workflow trigger with a readable event name

You can register against:
- canonical type (for example `general_manager.manager.updated`), or
- human-readable event name (for example `manager_updated`).

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
from general_manager.workflow.events import manager_updated_event

event = manager_updated_event(
    manager="Project",
    identification={"id": 42},
    changes={"status": "active"},
    old_values={"status": "draft"},
    event_name="project_status_changed",
)
```

The resulting payload stores per-field diffs:

```python
{
    "changes": {
        "status": {"old": "draft", "new": "active"}
    }
}
```

Available helpers:
- `manager_created_event(...)`
- `manager_updated_event(...)`
- `manager_deleted_event(...)`

## 4. Start a workflow and call an action

Inside your handler, start a workflow and execute an action:

```python
from general_manager.workflow.backend_registry import get_workflow_engine
from general_manager.workflow.engine import WorkflowDefinition

def start_project_status_workflow(event):
    def workflow_handler(input_data):
        # Replace with your action registry call, e.g. send_email
        return {"sent": True, "to": "ops@example.test"}

    workflow = WorkflowDefinition(
        workflow_id="project_status_email",
        handler=workflow_handler,
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
- `correlation_id` is a durable deduplication key per `workflow_id`.
- starting the same workflow with the same `correlation_id` reuses the existing execution record instead of creating a second one while the original execution is still active or already completed.
- after a failed execution, the same `correlation_id` can start a fresh execution again; failed attempts are not treated as the durable winning record.

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
- after final failure, the dead-letter hook receives `(event, exception)`.

Registration behavior:
- identical registrations for the same event are deduplicated at registration time.
- "identical" means the event key and all routing-relevant options resolve to the same registration identity.
- different `when`, `validator`, retry, or dead-letter settings still create separate registrations.
