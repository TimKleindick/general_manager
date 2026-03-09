from __future__ import annotations

from django.db import migrations, models

from general_manager.workflow.engine import ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES


class Migration(migrations.Migration):
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
