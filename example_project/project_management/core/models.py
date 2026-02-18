from django.db import models
from django.conf import settings


class UnansweredAIRequest(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="unanswered_ai_requests",
    )
    tenant = models.CharField(max_length=128, blank=True, default="")
    request_id = models.CharField(max_length=64, blank=True, default="")
    question = models.TextField()
    reason_code = models.CharField(max_length=64)
    reason_message = models.TextField(blank=True, default="")
    query_request = models.JSONField(default=dict, blank=True)
    gateway_response = models.JSONField(default=dict, blank=True)
    answer = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["-created_at"], name="core_unanswered_created_idx"),
            models.Index(fields=["reason_code"], name="core_unanswered_reason_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.reason_code}: {self.question[:80]}"
