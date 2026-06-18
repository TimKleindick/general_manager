from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("general_manager", "0003_workflow_execution_correlation_constraint")
    ]

    operations = [
        migrations.CreateModel(
            name="SearchIndexState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("manager_path", models.CharField(max_length=512)),
                ("index_name", models.CharField(max_length=255)),
                ("schema_fingerprint", models.CharField(max_length=64)),
                ("initialized_at", models.DateTimeField(blank=True, null=True)),
                ("last_reconciled_at", models.DateTimeField(blank=True, null=True)),
                ("dirty_since", models.DateTimeField(blank=True, null=True)),
                (
                    "dirty_reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("initialization", "Initialization"),
                            ("schema_changed", "Schema changed"),
                            ("data_changed", "Data changed"),
                            ("forced", "Forced"),
                        ],
                        default="",
                        max_length=32,
                    ),
                ),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddConstraint(
            model_name="searchindexstate",
            constraint=models.UniqueConstraint(
                fields=("manager_path", "index_name"),
                name="general_manager_search_state_manager_index_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="searchindexstate",
            index=models.Index(
                fields=["dirty_since", "index_name"],
                name="general_man_dirty__61cc37_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="searchindexstate",
            index=models.Index(
                fields=["manager_path", "index_name"],
                name="general_man_manager_9982f5_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="searchindexstate",
            index=models.Index(
                fields=["last_reconciled_at"],
                name="general_man_last_re_829c4b_idx",
            ),
        ),
    ]
