from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_project_percent_measurements"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UnansweredAIRequest",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("tenant", models.CharField(blank=True, default="", max_length=128)),
                ("request_id", models.CharField(blank=True, default="", max_length=64)),
                ("question", models.TextField()),
                ("reason_code", models.CharField(max_length=64)),
                ("reason_message", models.TextField(blank=True, default="")),
                ("query_request", models.JSONField(blank=True, default=dict)),
                ("gateway_response", models.JSONField(blank=True, default=dict)),
                ("answer", models.TextField(blank=True, default="")),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="unanswered_ai_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="unansweredairequest",
            index=models.Index(
                fields=["-created_at"], name="core_unanswered_created_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="unansweredairequest",
            index=models.Index(
                fields=["reason_code"], name="core_unanswered_reason_idx"
            ),
        ),
    ]
