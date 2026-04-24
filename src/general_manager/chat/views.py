"""HTTP and SSE transport views for chat."""

from __future__ import annotations

import json
from typing import Any

from asgiref.sync import async_to_sync, sync_to_async
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from django.utils import timezone

from general_manager.chat.models import (
    ChatConversation,
    ChatPendingConfirmation,
    append_chat_message,
    build_conversation_context,
    create_pending_confirmation,
    get_conversation_messages,
    update_conversation_summary,
)
from general_manager.chat.consumer import _iter_provider_events
from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
)
from general_manager.chat.rate_limits import enforce_chat_rate_limit
from general_manager.chat.signals import (
    emit_chat_error,
    emit_chat_message_received,
    emit_chat_mutation_executed,
    emit_chat_tool_called,
)
from general_manager.chat.settings import (
    get_chat_permission,
    get_chat_settings,
    import_provider,
)
from general_manager.chat.system_prompt import build_system_prompt
from general_manager.chat.tools import (
    ScopeChatContext,
    execute_chat_tool,
    get_tool_definitions,
)


def _ensure_session_key(request: HttpRequest) -> str | None:
    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", None)
    if session is not None and not session_key:
        session.save()
        session_key = getattr(session, "session_key", None)
    return session_key


def _conversation_for_request(request: HttpRequest) -> ChatConversation:
    return ChatConversation.for_actor(
        user=getattr(request, "user", None),
        session_key=_ensure_session_key(request),
    )


def _render_summary_source(messages: list[Any]) -> str:
    lines: list[str] = []
    for item in messages:
        prefix = item.role
        if item.tool_name:
            prefix = f"{prefix}:{item.tool_name}"
        lines.append(f"{prefix}: {item.content}")
    return "\n".join(lines)


async def _summarize_messages_with_provider(
    provider: Any,
    messages: list[Any],
) -> str:
    prompt_messages = [
        Message(
            role="system",
            content=(
                "Summarize the prior conversation briefly for future context. "
                "Keep facts, user intent, tool outcomes, and unresolved tasks."
            ),
        ),
        Message(role="user", content=_render_summary_source(messages)),
    ]
    chunks: list[str] = []
    async for event in provider.complete(prompt_messages, []):
        if isinstance(event, TextChunkEvent):
            chunks.append(event.content)
    return "".join(chunks).strip()


async def _build_messages(
    conversation: ChatConversation, provider: Any
) -> list[Message]:
    settings = get_chat_settings()
    summarize_after = int(settings.get("summarize_after", 20))
    max_recent_messages = int(settings.get("max_recent_messages", 12))
    conversation_messages = await sync_to_async(get_conversation_messages)(conversation)
    if (
        len(conversation_messages) > summarize_after
        and not conversation.summary_text.strip()
    ):
        older_messages = conversation_messages[:-max_recent_messages]
        summary_text = await _summarize_messages_with_provider(provider, older_messages)
        if summary_text:
            await sync_to_async(update_conversation_summary)(
                conversation,
                summary_text=summary_text,
            )

    messages = [Message(role="system", content=build_system_prompt())]
    for item in await sync_to_async(build_conversation_context)(conversation):
        messages.append(Message(role=item.role, content=item.content))
    return messages


def _answer_from_events(events: list[dict[str, Any]]) -> str:
    return "".join(
        event["content"] for event in events if event.get("type") == "text_chunk"
    )


async def _run_provider_turn(
    *,
    scope: dict[str, Any],
    conversation: ChatConversation,
    provider: Any,
    messages: list[Message],
    transport: str,
    tool_retries: int = 0,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    assistant_chunks: list[str] = []
    max_retries = int(get_chat_settings().get("max_retries_per_message", 8))

    async for event in _iter_provider_events(
        provider, messages, _build_tool_definitions()
    ):
        if isinstance(event, TextChunkEvent):
            assistant_chunks.append(event.content)
            events.append({"type": "text_chunk", "content": event.content})
            continue
        if isinstance(event, ToolCallEvent):
            events.append(
                {
                    "type": "tool_call",
                    "id": event.id,
                    "name": event.name,
                    "args": event.args,
                }
            )
            result = execute_chat_tool(
                event.name, event.args, ScopeChatContext.from_scope(scope)
            )
            emit_chat_tool_called(
                user=scope.get("user"),
                tool_name=event.name,
                args=event.args,
                result=result,
            )
            if (
                isinstance(result, dict)
                and result.get("status") == "confirmation_required"
                and event.name == "mutate"
            ):
                if transport == "http":
                    events.append(
                        {
                            "type": "error",
                            "message": "Confirmed mutations require WebSocket or SSE transport.",
                            "code": "confirmation_required_transport",
                        }
                    )
                    return events
                await sync_to_async(create_pending_confirmation)(
                    conversation,
                    confirmation_id=event.id,
                    mutation_name=str(result["mutation"]),
                    payload={"input": result["input"]},
                    timeout_seconds=int(
                        get_chat_settings().get("confirm_timeout_seconds", 30)
                    ),
                )
                events.append(
                    {
                        "type": "confirm_mutation",
                        "id": event.id,
                        "mutation": result["mutation"],
                        "input": result["input"],
                    }
                )
                return events
            events.append(
                {
                    "type": "tool_result",
                    "id": event.id,
                    "name": event.name,
                    "result": result,
                }
            )
            if event.name == "mutate":
                emit_chat_mutation_executed(
                    user=scope.get("user"),
                    mutation=event.args.get("mutation"),
                    input=event.args.get("input"),
                    result=result,
                )
            tool_content = json.dumps(result, sort_keys=True)
            await sync_to_async(append_chat_message)(
                conversation,
                role="tool",
                content=tool_content,
                tool_name=event.name,
                tool_args=dict(event.args),
                tool_result=result,
            )
            messages.append(
                Message(
                    role="assistant",
                    content=(
                        f"Called tool {event.name}. The next message is the tool "
                        "result; answer from it exactly."
                    ),
                )
            )
            messages.append(Message(role="tool", content=tool_content))
            next_retries = tool_retries + (0 if event.name == "mutate" else 1)
            if event.name != "mutate" and next_retries >= max_retries:
                events.append(
                    {
                        "type": "error",
                        "message": "Chat tool retry limit exceeded.",
                        "code": "tool_retry_limit",
                    }
                )
                return events
            events.extend(
                await _run_provider_turn(
                    scope=scope,
                    conversation=conversation,
                    provider=provider,
                    messages=messages,
                    transport=transport,
                    tool_retries=next_retries,
                )
            )
            return events
        if isinstance(event, DoneEvent):
            if assistant_chunks:
                await sync_to_async(append_chat_message)(
                    conversation,
                    role="assistant",
                    content="".join(assistant_chunks),
                )
            enforce_chat_rate_limit(
                scope,
                input_tokens=event.usage.input_tokens,
                output_tokens=event.usage.output_tokens,
            )
            events.append(
                {
                    "type": "done",
                    "usage": {
                        "input_tokens": event.usage.input_tokens,
                        "output_tokens": event.usage.output_tokens,
                    },
                }
            )
            return events
    return events


def _build_tool_definitions() -> list[Any]:
    from general_manager.chat.providers.base import ToolDefinition

    return [
        ToolDefinition(
            name=tool["name"],
            description=str(tool["description"]),
            input_schema=dict(tool["input_schema"]),
        )
        for tool in get_tool_definitions()
    ]


def _request_scope(request: HttpRequest) -> dict[str, Any]:
    return {
        "user": getattr(request, "user", None),
        "session": getattr(request, "session", None),
        "client": (
            request.META.get("REMOTE_ADDR", ""),
            int(request.META.get("REMOTE_PORT", 0) or 0),
        ),
    }


def _parse_json_body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    payload = json.loads(request.body.decode())
    return payload if isinstance(payload, dict) else {}


def _check_permission(request: HttpRequest) -> JsonResponse | None:
    permission = get_chat_permission()
    if (
        callable(permission)
        and permission(getattr(request, "user", None), _request_scope(request)) is False
    ):
        return JsonResponse({"detail": "Forbidden"}, status=403)
    return None


async def _execute_message_request(
    request: HttpRequest,
    *,
    transport: str,
) -> tuple[ChatConversation, list[dict[str, Any]]]:
    conversation = await sync_to_async(_conversation_for_request)(request)
    payload = _parse_json_body(request)
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return conversation, [
            {
                "type": "error",
                "message": "Message text is required.",
                "code": "bad_message",
            }
        ]
    scope = _request_scope(request)
    rate_limited = enforce_chat_rate_limit(scope)
    if rate_limited is not None:
        return conversation, [
            {
                "type": "error",
                "message": "Chat rate limit exceeded. Try again later.",
                "code": "rate_limited",
                "retry_after_seconds": rate_limited["retry_after_seconds"],
            }
        ]
    await sync_to_async(append_chat_message)(conversation, role="user", content=text)
    provider = import_provider()()
    messages = await _build_messages(conversation, provider)
    emit_chat_message_received(
        user=getattr(request, "user", None),
        message=text,
        conversation_id=getattr(conversation, "pk", None),
    )
    try:
        events = await _run_provider_turn(
            scope=scope,
            conversation=conversation,
            provider=provider,
            messages=messages,
            transport=transport,
        )
    except Exception as exc:  # noqa: BLE001
        emit_chat_error(
            user=getattr(request, "user", None),
            error=exc,
            context={"transport": transport, "path": request.path},
        )
        events = [{"type": "error", "message": str(exc), "code": "chat_error"}]
    return conversation, events


async def _execute_confirmation_request(
    request: HttpRequest,
) -> tuple[ChatConversation, list[dict[str, Any]]]:
    conversation = await sync_to_async(_conversation_for_request)(request)
    payload = _parse_json_body(request)
    confirmation_id = payload.get("confirmation_id")
    confirmed = payload.get("confirmed")
    if not isinstance(confirmation_id, str) or not isinstance(confirmed, bool):
        return conversation, [
            {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
        ]
    pending = await sync_to_async(ChatPendingConfirmation.active_for_conversation)(
        conversation=conversation,
        confirmation_id=confirmation_id,
        now=timezone.now(),
    )
    if pending is None:
        return conversation, [
            {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
        ]

    if confirmed:
        result = execute_chat_tool(
            "mutate",
            {
                "mutation": pending.mutation_name,
                "input": pending.payload.get("input", {}),
                "confirmed": True,
            },
            ScopeChatContext.from_scope(_request_scope(request)),
        )
    else:
        result = {"status": "cancelled", "reason": "user_rejected"}
    emit_chat_tool_called(
        user=getattr(request, "user", None),
        tool_name="mutate",
        args={
            "mutation": pending.mutation_name,
            "input": pending.payload.get("input", {}),
        },
        result=result,
    )
    emit_chat_mutation_executed(
        user=getattr(request, "user", None),
        mutation=pending.mutation_name,
        input=pending.payload.get("input", {}),
        result=result,
    )

    pending.resolved_at = timezone.now()
    await sync_to_async(pending.save)(update_fields=["resolved_at"])
    tool_content = json.dumps(result, sort_keys=True)
    await sync_to_async(append_chat_message)(
        conversation,
        role="tool",
        content=tool_content,
        tool_name="mutate",
        tool_args={
            "mutation": pending.mutation_name,
            "input": pending.payload.get("input", {}),
        },
        tool_result=result,
    )
    provider = import_provider()()
    messages = await _build_messages(conversation, provider)
    events = [
        {
            "type": "tool_result",
            "id": pending.confirmation_id,
            "name": "mutate",
            "result": result,
        }
    ]
    events.extend(
        await _run_provider_turn(
            scope=_request_scope(request),
            conversation=conversation,
            provider=provider,
            messages=messages,
            transport="sse",
        )
    )
    return conversation, events


@csrf_protect
@require_POST
def chat_http_view(request: HttpRequest) -> JsonResponse:
    denial = _check_permission(request)
    if denial is not None:
        return denial
    _conversation, events = async_to_sync(_execute_message_request)(
        request, transport="http"
    )
    return JsonResponse({"events": events, "answer": _answer_from_events(events)})


@csrf_protect
@require_POST
def chat_sse_view(request: HttpRequest) -> StreamingHttpResponse:
    denial = _check_permission(request)
    if denial is not None:
        return StreamingHttpResponse(
            iter([f"data: {json.dumps({'detail': 'Forbidden'})}\n\n"]),
            status=403,
            content_type="text/event-stream",
        )
    _conversation, events = async_to_sync(_execute_message_request)(
        request, transport="sse"
    )

    def _stream():
        for event in events:
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingHttpResponse(_stream(), content_type="text/event-stream")


@csrf_protect
@require_POST
def chat_confirm_view(request: HttpRequest) -> JsonResponse:
    denial = _check_permission(request)
    if denial is not None:
        return denial
    _conversation, events = async_to_sync(_execute_confirmation_request)(request)
    return JsonResponse({"events": events, "answer": _answer_from_events(events)})
