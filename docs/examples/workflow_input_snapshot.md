# Snapshot async workflow input before commit

Use a durable workflow engine inside a Django transaction when the handler must
see the values that started the execution, even if the caller reuses its input
mapping before commit.

Define an importable top-level handler in the application:

```python
# myproject/workflows.py
def handle_project_status(input_data):
    return {"project_id": input_data["project_id"], "status": input_data["status"]}
```

Start the workflow with the initial payload:

```python
from django.db import transaction

from general_manager.workflow.backend_registry import get_workflow_engine
from general_manager.workflow.engine import WorkflowDefinition

workflow = WorkflowDefinition(
    workflow_id="project_status",
    metadata={"handler_path": "myproject.workflows.handle_project_status"},
)
payload = {"project_id": 42, "status": "ready"}

with transaction.atomic():
    execution = get_workflow_engine().start(workflow, input_data=payload)
    payload["status"] = "archived"
    assert execution.input_data == {"project_id": 42, "status": "ready"}
```

In async mode, the handler is queued only after the outer transaction commits,
and it receives the captured top-level payload with `status="ready"`. A
savepoint or outer rollback discards the corresponding execution and task.
The copy is shallow: nested mappings and sequences are not copied or normalized,
so copy nested values when their contents may change before dispatch.
