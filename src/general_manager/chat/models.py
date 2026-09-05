"""Persistent chat models and helpers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from hashlib import sha256
from typing import Any, ClassVar, cast

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from general_manager.chat.settings import get_chat_settings


class AnonymousChatSessionRequiredError(ValueError):
    """Raised when anonymous chat access has no session identity."""

    def __init__(self) -> None:
        super().__init__("Anonymous chat conversations require a session key.")


class InvalidSummaryWatermarkError(ValueError):
    """Raised when a summary coverage marker crosses conversations."""

    def __init__(self) -> None:
        super().__init__("Summary watermark must belong to its conversation.")


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
    summarized_through: Any = models.ForeignKey(
        "ChatMessage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
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
        normalized_session_key = (
            session_key
            if len(session_key) <= 64
            else sha256(session_key.encode()).hexdigest()
        )
        if normalized_session_key != session_key:
            legacy_conversation = (
                cls.objects.filter(user__isnull=True, session_key=session_key)
                .order_by("-updated_at", "-id")
                .first()
            )
            if legacy_conversation is not None:
                legacy_conversation.session_key = normalized_session_key
                legacy_conversation.save(update_fields=["session_key"])
                return legacy_conversation
        conversation = (
            cls.objects.filter(user__isnull=True, session_key=normalized_session_key)
            .order_by("-updated_at", "-id")
            .first()
        )
        if conversation is not None:
            return conversation
        return cls.objects.create(session_key=normalized_session_key)


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
    tool_call_id: Any = models.CharField(max_length=128, null=True, blank=True)
    tool_calls: Any = models.JSONField(null=True, blank=True)
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
    confirmation_id: Any = models.CharField(max_length=128)
    mutation_name: Any = models.CharField(max_length=255)
    payload: Any = models.JSONField(default=dict)
    expires_at: Any = models.DateTimeField()
    resolved_at: Any = models.DateTimeField(null=True, blank=True)
    unresolved_marker: Any = models.BooleanField(
        null=True,
        default=True,
        editable=False,
    )
    created_at: Any = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["created_at"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["expires_at"]),
            models.Index(fields=["resolved_at"]),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["conversation", "confirmation_id", "unresolved_marker"],
                name="gm_chat_pending_conv_conf_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        resolved_at__isnull=True,
                        unresolved_marker=True,
                        unresolved_marker__isnull=False,
                    )
                    | models.Q(
                        resolved_at__isnull=False,
                        unresolved_marker__isnull=True,
                    )
                ),
                name="gm_chat_pending_resolution_state",
            ),
        ]

    def save(
        self,
        *args: Any,
        force_insert: bool = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        """Keep the internal uniqueness marker aligned with resolution state."""
        if args:
            parse_save_params = getattr(
                cast(Any, self),
                "_parse_save_params",
                None,
            )
            if parse_save_params is None:
                cast(Any, super().save)(
                    *args,
                    force_insert=force_insert,
                    force_update=force_update,
                    using=using,
                    update_fields=update_fields,
                )
                return
            force_insert, force_update, using, update_fields = parse_save_params(
                *args,
                method_name="save",
                force_insert=force_insert,
                force_update=force_update,
                using=using,
                update_fields=update_fields,
            )
        if update_fields is not None:
            update_fields = set(update_fields)
        if update_fields is None or "resolved_at" in update_fields:
            self.unresolved_marker = True if self.resolved_at is None else None
            if update_fields is not None:
                update_fields.add("unresolved_marker")
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

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

    @classmethod
    def claim_for_conversation(
        cls,
        *,
        conversation: ChatConversation,
        confirmation_id: str,
        now: Any | None = None,
        allow_expired: bool = False,
    ) -> ChatPendingConfirmation | None:
        current_time = now if now is not None else timezone.now()
        with transaction.atomic():
            queryset = cls.objects.select_for_update().filter(
                conversation=conversation,
                confirmation_id=confirmation_id,
                resolved_at__isnull=True,
            )
            if not allow_expired:
                queryset = queryset.filter(expires_at__gt=current_time)
            pending = queryset.order_by("-created_at", "-id").first()
            if pending is None:
                return None
            pending.resolved_at = current_time
            pending.unresolved_marker = None
            pending.save(update_fields=["resolved_at", "unresolved_marker"])
            return pending


def append_chat_message(
    conversation: ChatConversation,
    *,
    role: str,
    content: str = "",
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
    tool_result: Any = None,
    tool_call_id: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> ChatMessage:
    """Persist one chat message and refresh the conversation timestamp."""
    message = ChatMessage.objects.create(
        conversation=conversation,
        role=role,
        content=content,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_result=tool_result,
        tool_call_id=tool_call_id,
        tool_calls=tool_calls,
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


def _tool_exchange_context(
    messages: list[ChatMessage], selected_messages: list[ChatMessage]
) -> list[ChatMessage]:
    """Keep complete persisted tool exchanges together inside a context window."""
    selected_ids = {message.pk for message in selected_messages}
    result_groups = _tool_result_groups(messages)
    # Include the declaring assistant row for selected results and every result for a
    # selected assistant call group. Repeat because adding a group can add more IDs.
    changed = True
    while changed:
        changed = False
        selected = [message for message in messages if message.pk in selected_ids]
        for message in selected:
            group = result_groups.get(message.pk)
            if group is not None and group.pk not in selected_ids:
                selected_ids.add(group.pk)
                changed = True
        selected_group_ids = {message.pk for message in selected}
        for result_id, group in result_groups.items():
            if group.pk in selected_group_ids and result_id not in selected_ids:
                selected_ids.add(result_id)
                changed = True
    return [message for message in messages if message.pk in selected_ids]


def _complete_tool_exchange_groups(
    messages: list[ChatMessage],
) -> tuple[dict[Any, ChatMessage], set[Any]]:
    """Return result links and declarations for complete native tool exchanges."""
    declarations: dict[str, ChatMessage] = {}
    declaration_call_ids: dict[Any, list[str] | None] = {}
    declaration_messages: dict[Any, ChatMessage] = {}
    result_groups: dict[Any, ChatMessage] = {}
    result_declarations: dict[Any, ChatMessage] = {}
    results_by_declaration: dict[Any, list[ChatMessage]] = {}
    for message in messages:
        if message.role == "assistant":
            calls = getattr(message, "tool_calls", None) or []
            if not calls:
                continue
            call_ids: list[str] = []
            for call in calls:
                if not isinstance(call, dict):
                    call_ids = []
                    break
                call_id, name, args = call.get("id"), call.get("name"), call.get("args")
                if (
                    not isinstance(call_id, str)
                    or not isinstance(name, str)
                    or not isinstance(args, dict)
                ):
                    call_ids = []
                    break
                call_ids.append(call_id)
            declaration_call_ids[message.pk] = (
                call_ids if call_ids and len(call_ids) == len(set(call_ids)) else None
            )
            declaration_messages[message.pk] = message
            for call_id in call_ids:
                declarations[call_id] = message
        elif message.role == "tool" and message.tool_call_id:
            declaration = declarations.get(message.tool_call_id)
            if declaration is not None:
                results_by_declaration.setdefault(declaration.pk, []).append(message)
                result_declarations[message.pk] = declaration

    if not declarations:
        return result_groups, set()

    complete_declarations: set[Any] = set()
    message_positions = {
        message.pk: position for position, message in enumerate(messages)
    }
    for declaration_id, declared_call_ids in declaration_call_ids.items():
        if declared_call_ids is None:
            continue
        contiguous_results: list[ChatMessage] = []
        for candidate in messages[message_positions[declaration_id] + 1 :]:
            if candidate.role != "tool":
                break
            if (
                result_declarations.get(candidate.pk)
                is not declaration_messages[declaration_id]
            ):
                break
            contiguous_results.append(candidate)
        result_ids = [result.tool_call_id for result in contiguous_results]
        if len(contiguous_results) == len(declared_call_ids) and set(result_ids) == set(
            declared_call_ids
        ):
            complete_declarations.add(declaration_id)
            for result in contiguous_results:
                result_groups[result.pk] = result_declarations[result.pk]
    return result_groups, complete_declarations


def _tool_result_groups(messages: list[ChatMessage]) -> dict[Any, ChatMessage]:
    """Associate results only when their nearest declaration is complete."""
    result_groups, _complete_declarations = _complete_tool_exchange_groups(messages)
    return result_groups


def provider_messages_from_context(messages: list[ChatMessage]) -> list[Any]:
    """Serialize persisted rows without creating orphan provider tool messages."""
    from general_manager.chat.providers.base import Message, ToolCallEvent

    result_groups, complete_declarations = _complete_tool_exchange_groups(messages)
    provider_messages: list[Message] = []
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None)
        if message.role == "assistant" and tool_calls:
            calls: list[ToolCallEvent] = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                call_id, name, args = call.get("id"), call.get("name"), call.get("args")
                if (
                    isinstance(call_id, str)
                    and isinstance(name, str)
                    and isinstance(args, dict)
                ):
                    calls.append(ToolCallEvent(id=call_id, name=name, args=args))
            if calls and message.pk in complete_declarations:
                provider_messages.append(
                    Message(
                        role="assistant",
                        content=message.content,
                        tool_calls=tuple(calls),
                    )
                )
                continue
            call_names = ", ".join(
                str(call.get("name", "unknown"))
                for call in tool_calls
                if isinstance(call, dict)
            )
            provider_messages.append(
                Message(
                    role="assistant",
                    content=(
                        "Historical incomplete tool exchange"
                        f" ({call_names or 'unknown'}): {message.content}"
                    ),
                )
            )
            continue
        if message.role == "tool":
            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id and message.pk in result_groups:
                provider_messages.append(
                    Message(
                        role="tool",
                        content=message.content,
                        tool_call_id=tool_call_id,
                        tool_name=getattr(message, "tool_name", None),
                        tool_result=getattr(message, "tool_result", None),
                    )
                )
            else:
                provider_messages.append(
                    Message(
                        role="assistant",
                        content=(
                            "Historical tool data"
                            f" ({getattr(message, 'tool_name', None) or 'unknown'}): "
                            f"{message.content}"
                        ),
                    )
                )
            continue
        provider_messages.append(Message(role=message.role, content=message.content))
    return provider_messages


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
    recent_messages = _tool_exchange_context(messages, recent_messages)

    watermark = conversation.summarized_through
    summary_text = conversation.summary_text.strip()
    summary_is_current = (
        bool(summary_text)
        and watermark is not None
        and older_messages
        and watermark.pk == older_messages[-1].pk
    )
    if not summary_is_current and callable(summarizer):
        summary_text = str(summarizer(older_messages)).strip()
        if summary_text:
            update_conversation_summary(
                conversation,
                summary_text=summary_text,
                summarized_through=older_messages[-1],
            )

    if not summary_is_current and not callable(summarizer):
        summary_text = ""
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
    summarized_through: ChatMessage | None = None,
) -> None:
    """Persist a generated summary for later context-window reuse."""
    if (
        summarized_through is not None
        and summarized_through.conversation.pk != conversation.pk
    ):
        raise InvalidSummaryWatermarkError
    timestamp = timezone.now()
    with transaction.atomic():
        current = ChatConversation.objects.select_for_update().get(pk=conversation.pk)
        current_watermark_id = getattr(current, "summarized_through_id", None)
        current_watermark = (
            ChatMessage.objects.get(pk=current_watermark_id)
            if current_watermark_id is not None
            else None
        )
        if (
            summarized_through is not None
            and current_watermark is not None
            and (summarized_through.created_at, summarized_through.pk)
            <= (current_watermark.created_at, current_watermark.pk)
        ):
            conversation.summary_text = current.summary_text
            conversation.summary_updated_at = current.summary_updated_at
            conversation.summarized_through = current_watermark
            return
        current.summary_text = summary_text
        current.summary_updated_at = timestamp
        current.summarized_through = summarized_through
        current.save(
            update_fields=["summary_text", "summary_updated_at", "summarized_through"]
        )
    conversation.summary_text = summary_text
    conversation.summary_updated_at = timestamp
    conversation.summarized_through = summarized_through


def create_pending_confirmation(
    conversation: ChatConversation,
    *,
    confirmation_id: str,
    mutation_name: str,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> ChatPendingConfirmation:
    """Persist a new pending confirmation for the conversation."""
    current_time = timezone.now()
    with transaction.atomic():
        ChatConversation.objects.select_for_update().get(pk=conversation.pk)
        ChatPendingConfirmation.objects.filter(
            conversation=conversation,
            confirmation_id=confirmation_id,
            resolved_at__isnull=True,
            expires_at__lte=current_time,
        ).update(resolved_at=current_time, unresolved_marker=None)
        unresolved_duplicate_exists = ChatPendingConfirmation.objects.filter(
            conversation=conversation,
            confirmation_id=confirmation_id,
            resolved_at__isnull=True,
        ).exists()
        if unresolved_duplicate_exists:
            raise IntegrityError
        return ChatPendingConfirmation.objects.create(
            conversation=conversation,
            confirmation_id=confirmation_id,
            mutation_name=mutation_name,
            payload=payload,
            expires_at=current_time + timedelta(seconds=timeout_seconds),
        )


def cleanup_expired_chat_records(*, ttl_hours: int) -> dict[str, int]:
    """Delete chat records older than the configured retention TTL."""
    cutoff = timezone.now() - timedelta(hours=ttl_hours)
    confirmation_total, confirmation_counts = ChatPendingConfirmation.objects.filter(
        models.Q(expires_at__lt=cutoff) | models.Q(resolved_at__lt=cutoff)
    ).delete()
    conversation_total, conversation_counts = ChatConversation.objects.filter(
        updated_at__lt=cutoff
    ).delete()

    def deleted_count(model: type[models.Model]) -> int:
        return int(conversation_counts.get(model._meta.label, 0)) + int(
            confirmation_counts.get(model._meta.label, 0)
        )

    return {
        "conversations": deleted_count(ChatConversation),
        "messages": deleted_count(ChatMessage),
        "pending_confirmations": deleted_count(ChatPendingConfirmation),
        "total": int(conversation_total + confirmation_total),
    }
