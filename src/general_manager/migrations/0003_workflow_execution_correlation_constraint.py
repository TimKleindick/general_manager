from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = (("general_manager", "0002_workflow_outbox_scaling_indexes"),)

    operations = (
        migrations.AddConstraint(
            model_name="workflowexecutionrecord",
            constraint=models.UniqueConstraint(
                fields=("workflow_id", "correlation_id"),
                condition=models.Q(correlation_id__isnull=False)
                & ~models.Q(correlation_id="")
                & models.Q(state__in=("pending", "running", "waiting", "completed")),
                name="general_manager_workflow_exec_active_corr_uniq",
            ),
        ),
    )
