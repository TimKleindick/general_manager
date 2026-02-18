from django.contrib import admin

from core.models import UnansweredAIRequest


@admin.register(UnansweredAIRequest)
class UnansweredAIRequestAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "reason_code",
        "user",
        "tenant",
        "request_id",
        "short_question",
    )
    list_filter = ("reason_code", "created_at")
    search_fields = ("question", "reason_message", "request_id", "tenant")
    readonly_fields = (
        "created_at",
        "user",
        "tenant",
        "request_id",
        "question",
        "reason_code",
        "reason_message",
        "query_request",
        "gateway_response",
        "answer",
    )

    def short_question(self, obj: UnansweredAIRequest) -> str:
        return (obj.question[:80] + "...") if len(obj.question) > 80 else obj.question

    short_question.short_description = "question"
