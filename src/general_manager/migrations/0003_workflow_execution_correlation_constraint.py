"""Add the workflow execution correlation uniqueness constraint.

Django applies this historical migration after
`0002_workflow_outbox_scaling_indexes` and performs exactly one schema
operation: add `general_manager_workflow_exec_active_corr_uniq` to
`WorkflowExecutionRecord`. The constraint is unique on
`(workflow_id, correlation_id)` and applies only when `correlation_id` is not
NULL, `correlation_id` is not an empty string, and `state` is one of
`("pending", "running", "waiting", "completed")`.

The local `ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES` tuple freezes the state values
that existed when the migration was created. Keep those values local to this
historical migration so future runtime workflow-state changes do not rewrite
old schema semantics. The migration exposes no public callables, accepts no
application inputs, returns no application output, and leaves Django/database
migration errors unwrapped.
"""

from django.db import migrations, models

ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES: tuple[str, str, str, str] = (
    "pending",
    "running",
    "waiting",
    "completed",
)


class Migration(migrations.Migration):
    """Django migration container for the single correlation unique constraint."""

    dependencies = [("general_manager", "0002_workflow_outbox_scaling_indexes")]

    operations = [
        migrations.AddConstraint(
            model_name="workflowexecutionrecord",
            constraint=models.UniqueConstraint(
                fields=("workflow_id", "correlation_id"),
                condition=models.Q(correlation_id__isnull=False)
                & ~models.Q(correlation_id="")
                & models.Q(state__in=ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES),
                name="general_manager_workflow_exec_active_corr_uniq",
            ),
        ),
    ]
