"""Persistent chat models and helpers."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar, cast

from django.conf import settings
from django.db import models
from django.utils import timezone

from general_manager.chat.settings import get_chat_settings


class AnonymousChatSessionRequiredError(ValueError):
    """Raised when anonymous chat access has no session identity."""

    def __init__(self) -> None:
        super().__init__("Anonymous chat conversations require a session key.")


class ChatConversation(models.Model):
    """Conversation identity for an authenticated user or anonymous session."""

    user: Any = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="chat_conversations",
    )
    session_key: Any = models.CharField(max_length=64, null=True, blank=True)
    summary_text: Any = models.TextField(blank=True, default="")
    summary_updated_at: Any = models.DateTimeField(null=True, blank=True)
    created_at: Any = models.DateTimeField(auto_now_add=True)
    updated_at: Any = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["created_at"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "updated_at"]),
            models.Index(fields=["session_key", "updated_at"]),
            models.Index(fields=["updated_at"]),
        ]

    @classmethod
    def for_actor(cls, *, user: Any, session_key: str | None) -> ChatConversation:
        """Return the active conversation for the actor, creating one if needed."""
        is_authenticated = bool(getattr(user, "is_authenticated", False))
        if is_authenticated and getattr(user, "pk", None) is not None:
            conversation = (
                cls.objects.filter(user=user).order_by("-updated_at", "-id").first()
            )
            if conversation is not None:
                return conversation
            return cls.objects.create(user=user)

        if not session_key:
            raise AnonymousChatSessionRequiredError
        conversation = (
            cls.objects.filter(user__isnull=True, session_key=session_key)
            .order_by("-updated_at", "-id")
            .first()
        )
        if conversation is not None:
            return conversation
        return cls.objects.create(session_key=session_key)


class ChatMessage(models.Model):
    """One persisted chat message or tool exchange item."""

    conversation: Any = models.ForeignKey(
        ChatConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role: Any = models.CharField(max_length=16)
    content: Any = models.TextField(blank=True, default="")
    tool_name: Any = models.CharField(max_length=128, null=True, blank=True)
    tool_args: Any = models.JSONField(null=True, blank=True)
    tool_result: Any = models.JSONField(null=True, blank=True)
    created_at: Any = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["created_at", "id"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["created_at"]),
        ]


class ChatPendingConfirmation(models.Model):
    """Durable mutation confirmation state for cross-request transports."""

    conversation: Any = models.ForeignKey(
        ChatConversation,
        on_delete=models.CASCADE,
        related_name="pending_confirmations",
    )
    confirmation_id: Any = models.CharField(max_length=128, unique=True)
    mutation_name: Any = models.CharField(max_length=255)
    payload: Any = models.JSONField(default=dict)
    expires_at: Any = models.DateTimeField()
    resolved_at: Any = models.DateTimeField(null=True, blank=True)
    created_at: Any = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["created_at"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["expires_at"]),
            models.Index(fields=["resolved_at"]),
        ]

    @classmethod
    def active_for_conversation(
        cls,
        *,
        conversation: ChatConversation,
        confirmation_id: str,
        now: Any | None = None,
    ) -> ChatPendingConfirmation | None:
        current_time = now or timezone.now()
        return (
            cls.objects.filter(
                conversation=conversation,
                confirmation_id=confirmation_id,
                resolved_at__isnull=True,
                expires_at__gt=current_time,
            )
            .order_by("-created_at", "-id")
            .first()
        )


def append_chat_message(
    conversation: ChatConversation,
    *,
    role: str,
    content: str = "",
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
    tool_result: Any = None,
) -> ChatMessage:
    """Persist one chat message and refresh the conversation timestamp."""
    message = ChatMessage.objects.create(
        conversation=conversation,
        role=role,
        content=content,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_result=tool_result,
    )
    ChatConversation.objects.filter(pk=conversation.pk).update(
        updated_at=timezone.now()
    )
    conversation.updated_at = timezone.now()
    return message


def get_conversation_messages(
    conversation: ChatConversation,
    *,
    max_recent_messages: int | None = None,
) -> list[ChatMessage]:
    """Return ordered messages for a conversation, optionally capped."""
    queryset = cast(Any, conversation).messages.order_by("-created_at", "-id")
    if isinstance(max_recent_messages, int) and max_recent_messages > 0:
        queryset = queryset[:max_recent_messages]
    return list(reversed(list(queryset)))


def build_conversation_context(
    conversation: ChatConversation,
    *,
    summarizer: Any | None = None,
) -> list[ChatMessage]:
    """Build the provider context window with cached summarization for old turns."""
    settings = get_chat_settings()
    summarize_after = int(settings.get("summarize_after", 20))
    max_recent_messages = int(settings.get("max_recent_messages", 12))
    messages = get_conversation_messages(conversation)
    if len(messages) <= summarize_after:
        return messages

    recent_messages = messages[-max_recent_messages:]
    older_messages = messages[:-max_recent_messages]

    # Preserve the most recent tool result in full even if it falls outside the window.
    latest_tool = next(
        (message for message in reversed(messages) if message.role == "tool"), None
    )
    if latest_tool is not None and all(
        message.pk != latest_tool.pk for message in recent_messages
    ):
        recent_messages = [latest_tool, *recent_messages]

    summary_text = conversation.summary_text.strip()
    if not summary_text and callable(summarizer):
        summary_text = str(summarizer(older_messages)).strip()
        if summary_text:
            ChatConversation.objects.filter(pk=conversation.pk).update(
                summary_text=summary_text,
                summary_updated_at=timezone.now(),
            )
            conversation.summary_text = summary_text
            conversation.summary_updated_at = timezone.now()

    if not summary_text:
        return recent_messages

    return [
        ChatMessage(
            conversation=conversation,
            role="system",
            content=summary_text,
        ),
        *recent_messages,
    ]


def update_conversation_summary(
    conversation: ChatConversation,
    *,
    summary_text: str,
) -> None:
    """Persist a generated summary for later context-window reuse."""
    timestamp = timezone.now()
    ChatConversation.objects.filter(pk=conversation.pk).update(
        summary_text=summary_text,
        summary_updated_at=timestamp,
    )
    conversation.summary_text = summary_text
    conversation.summary_updated_at = timestamp


def create_pending_confirmation(
    conversation: ChatConversation,
    *,
    confirmation_id: str,
    mutation_name: str,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> ChatPendingConfirmation:
    """Persist a new pending confirmation for the conversation."""
    return ChatPendingConfirmation.objects.create(
        conversation=conversation,
        confirmation_id=confirmation_id,
        mutation_name=mutation_name,
        payload=payload,
        expires_at=timezone.now() + timedelta(seconds=timeout_seconds),
    )


def cleanup_expired_chat_records(*, ttl_hours: int) -> dict[str, int]:
    """Delete chat records older than the configured retention TTL."""
    cutoff = timezone.now() - timedelta(hours=ttl_hours)
    deleted_confirmations = ChatPendingConfirmation.objects.filter(
        models.Q(expires_at__lt=cutoff) | models.Q(resolved_at__lt=cutoff)
    ).delete()[0]
    deleted_conversations = ChatConversation.objects.filter(
        updated_at__lt=cutoff
    ).delete()[0]
    return {
        "conversations": int(deleted_conversations),
        "pending_confirmations": int(deleted_confirmations),
    }
