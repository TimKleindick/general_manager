"""Create durable search reconciliation state.

Django applies this historical migration with dependency
`("general_manager", "0003_workflow_execution_correlation_constraint")` and
performs six schema operations: create `SearchIndexState`, add
`general_manager_search_state_manager_index_uniq`, and add four operational
indexes for dirty-row selection, claim lookup, claim expiration, and last
reconciliation time.

`SearchIndexState` stores one row per `(manager_path, index_name)`: `id` is a
`BigAutoField`; `manager_path`, `index_name`, and `schema_fingerprint` are
required `CharField`s with max lengths 512, 255, and 64; `initialized_at`,
`last_reconciled_at`, `dirty_since`, `claimed_at`, and `claim_expires_at` are
nullable/blank `DateTimeField`s; `dirty_reason` is a blank `CharField` with
default `""`, max length 32, and the choices `("initialization",
"Initialization")`, `("schema_changed", "Schema changed")`, `("data_changed",
"Data changed")`, and `("forced", "Forced")`; `claim_token` is a blank
`CharField` with default `""` and max length 64; `last_error` is a blank
`TextField` with default `""`; and `created_at`/`updated_at` are automatic
timestamps.

The migration exposes no public callables, accepts no application inputs,
returns no application output, and leaves Django/database migration errors
unwrapped.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Django migration container for the search reconciliation state table."""

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
                (
                    "claim_token",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("claim_expires_at", models.DateTimeField(blank=True, null=True)),
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
                name="general_man_dirty_s_71fc00_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="searchindexstate",
            index=models.Index(
                fields=["claim_token"],
                name="general_man_claim_t_3aaacc_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="searchindexstate",
            index=models.Index(
                fields=["claim_expires_at"],
                name="general_man_claim_e_1fa228_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="searchindexstate",
            index=models.Index(
                fields=["last_reconciled_at"],
                name="general_man_last_re_81038c_idx",
            ),
        ),
    ]
