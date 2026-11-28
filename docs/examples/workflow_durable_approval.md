# Versioned workflow approval

This recipe releases a gated step and passes its decision to a second step. It
requires a Django project with GeneralManager migrations applied, Celery and a
broker configured, a worker consuming `workflow.events` and
`workflow.executions`, and Celery Beat running. Use a database with row locking
for concurrent production workers. Configure the engine before starting:

```python
# settings.py (merge into your existing GENERAL_MANAGER settings)
GENERAL_MANAGER = {
    "WORKFLOW_MODE": "production",
    "WORKFLOW_ASYNC": True,
    "WORKFLOW_BEAT_ENABLED": True,
    "WORKFLOW_DURABLE_STEPS_ENABLED": True,
}
```

Put these importable handlers in `my_app/workflows.py`, replacing `my_app` in
both paths below with your installed application package:

```python
from general_manager.workflow import WorkflowStepContext


def record_review(context: WorkflowStepContext) -> dict[str, object]:
    return {"drawing_id": context.input_data["drawing_id"],
            "approved": (context.signal or {}).get("approved") is True}


def decide_release(context: WorkflowStepContext) -> dict[str, object]:
    # Real external effects must use context.idempotency_key to deduplicate.
    return {"drawing_id": context.input_data["drawing_id"],
            "release_allowed": context.input_data["approved"] is True}
```

From application code with Django initialized, start and resume the run:

```python
from general_manager.workflow import WorkflowDefinition, WorkflowStep, get_workflow_engine

workflow = WorkflowDefinition(
    "drawing-release", version="1", steps=(
        WorkflowStep("review", "my_app.workflows.record_review", wait_for_signal=True),
        WorkflowStep("release", "my_app.workflows.decide_release"),
    ),
)
engine = get_workflow_engine()
run = engine.start(workflow, {"drawing_id": 42}, correlation_id="drawing-42-r7")
# Authenticate and authorize the reviewer before accepting the decision.
engine.resume(run.execution_id, {
    "step": "review", "id": "decision-42-r7", "data": {"approved": True},
})
# Later, after the workers finish:
run = engine.status(run.execution_id)
```

`status()` returns a snapshot; poll according to your application's needs.
Retry the same decision using the same ID and data. This example computes a
decision rather than performing a release. For real side effects, use a
provider's idempotency contract or an atomic durable record and reconcile
uncertain outcomes. See [operations](../howto/workflow_ops.md#run-versioned-approval-workflows)
and the [API reference](../api/workflow.md#versioned-durable-execution).
