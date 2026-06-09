# Workflow

GeneralManager workflows connect manager-level events to durable automation. The workflow subsystem is intentionally separate from interface CRUD code: interfaces emit domain changes, event registries route those changes, and workflow engines execute or delegate orchestration.

## Mental model

A workflow has four layers:

1. **Events** describe what happened. Helpers such as `manager_created_event`, `manager_updated_event`, and `manager_deleted_event` produce stable `WorkflowEvent` payloads.
2. **Registries** route events to handlers. `InMemoryEventRegistry` is useful for local development and tests; `DatabaseEventRegistry` stores durable route state for production-style delivery.
3. **Engines** execute workflow definitions. `LocalWorkflowEngine` runs in process, while `CeleryWorkflowEngine` persists executions and delegates async work through Celery tasks.
4. **Actions** centralize reusable side effects behind `ActionRegistry`, so handlers call named operations instead of scattering integration logic.

## Event routing

Handlers can register against canonical event types such as `general_manager.manager.updated` or readable event names such as `manager_updated`. A registration can include a `when` predicate, a validator, retry settings, and a dead-letter callback. The registry isolates handler failures so one failed route does not stop other matching handlers.

## Signal bridge

When `GENERAL_MANAGER["WORKFLOW_SIGNAL_BRIDGE"] = True`, manager create, update, and delete signals are converted into workflow events. This is the automatic path for CRUD-driven automation. Service layers can also publish events explicitly through `get_event_registry().publish(event)` when signal coupling is not the right fit.

## Execution state

Workflow executions move through a bounded state model:

- `pending`, `running`, and `waiting` are active states.
- `completed`, `failed`, and `cancelled` are terminal states.
- `resume(...)` is valid only for waiting executions.
- `cancel(...)` is valid only for active executions.

Use `correlation_id` when the same event should not start duplicate active or completed executions for the same workflow definition.

## Outbox and delivery

Production mode uses the workflow outbox to claim, route, retry, and dead-letter event delivery. The management commands `workflow_drain_outbox` and `workflow_replay_dead_letters` are operational tools for draining pending rows and replaying dead-lettered rows. Celery Beat can drain the outbox periodically when workflow beat settings are enabled.

## Backends

- `LocalWorkflowEngine` is the zero-setup development backend.
- `CeleryWorkflowEngine` is the durable production-oriented backend.
- `N8nWorkflowEngine` is an adapter stub for future remote orchestration support.

Choose the backend in `GENERAL_MANAGER["WORKFLOW_ENGINE"]`, or rely on `WORKFLOW_MODE` defaults when they fit the environment.

## Related guides

- [Build workflow event triggers](../howto/workflow_events.md)
- [Operate workflow outbox/dead letters](../howto/workflow_ops.md)
- [Workflow API reference](../api/workflow.md)
