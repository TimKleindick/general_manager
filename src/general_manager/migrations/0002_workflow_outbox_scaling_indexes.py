# ruff: noqa: RUF012

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("general_manager", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="workflowoutbox",
            index=models.Index(
                fields=["status", "claimed_at"],
                name="workflow_ou_status__8b7f7b_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="workflowoutbox",
            index=models.Index(
                fields=["status", "available_at", "id"],
                name="workflow_ou_status__a5f7dc_idx",
            ),
        ),
    ]
