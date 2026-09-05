"""HTTP and SSE transport views for chat."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings as django_settings
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from django.utils import timezone

from general_manager.chat.errors import public_chat_error
from general_manager.chat.context import (
    prepare_conversation_messages,
    summarize_messages_with_provider,
)
from general_manager.chat.models import (
    ChatConversation,
    ChatPendingConfirmation,
    append_chat_message,
    create_pending_confirmation,
)
from general_manager.chat.planned.catalog import load_manager_catalog
from general_manager.chat.planned.config import get_planned_chat_settings
from general_manager.chat.planned.scheduler import (
    SchedulerCallbacks,
    iter_planned_read_events,
    prepare_planned_turn,
)
from general_manager.chat.schema_index import build_schema_index
from general_manager.chat.consumer import (
    _has_tool_after_last_user,
    _iter_provider_events,
    _last_user_text,
)
from general_manager.chat.grounding import (
    build_empty_response_recovery_message,
    build_missing_tool_recovery_message,
    build_query_required_recovery_message,
    should_recover_answer_without_query,
    should_recover_missing_tool_call,
)
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
from general_manager.chat.tools import (
    ScopeChatContext,
    execute_confirmed_chat_mutation,
    execute_chat_tool,
    get_tool_definitions,
)


@dataclass(frozen=True)
class _PreparedMessageRequest:
    conversation: ChatConversation | None
    scope: dict[str, Any]
    provider: Any | None
    messages: list[Message] | None
    early_events: list[dict[str, Any]] | None
    user_text: str | None = None
    planned_settings: Any | None = None


def _ensure_session_key(request: HttpRequest) -> str | None:
    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", None)
    if session is not None and not session_key:
        # Signed-cookie sessions need a value to survive response processing.
        # This inert marker also lets SessionMiddleware emit the cookie before an
        # SSE generator gets its first opportunity to run.
        try:
            session["_gm_chat_session"] = True
        except TypeError:
            pass
        session.save()
        session.modified = True
        session_key = getattr(session, "session_key", None)
    return session_key


def _conversation_for_request(request: HttpRequest) -> ChatConversation:
    return ChatConversation.for_actor(
        user=getattr(request, "user", None),
        session_key=_ensure_session_key(request),
    )


def _attach_session_cookie(
    response: StreamingHttpResponse, request: HttpRequest
) -> None:
    """Attach the already-created session before streaming commits headers."""
    session = getattr(request, "session", None)
    if session is None:
        return
    if not session.session_key:
        return
    response.set_cookie(
        django_settings.SESSION_COOKIE_NAME,
        session.session_key,
        max_age=None
        if session.get_expire_at_browser_close()
        else session.get_expiry_age(),
        domain=django_settings.SESSION_COOKIE_DOMAIN,
        path=django_settings.SESSION_COOKIE_PATH,
        secure=django_settings.SESSION_COOKIE_SECURE,
        httponly=django_settings.SESSION_COOKIE_HTTPONLY,
        samesite=django_settings.SESSION_COOKIE_SAMESITE,
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
    *,
    scope: dict[str, Any] | None = None,
) -> str:
    return await summarize_messages_with_provider(provider, messages, scope=scope)


async def _build_messages(
    conversation: ChatConversation,
    provider: Any,
    *,
    allow_summarization: bool = True,
    scope: dict[str, Any] | None = None,
) -> list[Message]:
    async def summarize(provider_instance: Any, history: list[Any]) -> str:
        return await _summarize_messages_with_provider(
            provider_instance, history, scope=scope
        )

    return await prepare_conversation_messages(
        conversation,
        provider,
        allow_summarization=allow_summarization,
        scope=scope,
        summarize=summarize,
    )


def _planned_catalog_summary(settings: Any) -> dict[str, Any]:
    """Build the planner's inert catalog/schema reference data for one turn."""
    schema_index = build_schema_index()
    catalog = load_manager_catalog(
        getattr(settings, "catalog_source", None), schema_index
    )
    return {
        "catalog": {
            name: {
                "domain": entry.domain,
                "aliases": list(entry.aliases),
                "use_when": entry.use_when,
                "distinguish_from": list(entry.distinguish_from),
            }
            for name, entry in catalog.entries.items()
        },
        "schema": schema_index,
    }


def _answer_from_events(events: list[dict[str, Any]]) -> str:
    return "".join(
        event["content"] for event in events if event.get("type") == "text_chunk"
    )


def _chat_error_events(
    exc: Exception,
    request: HttpRequest,
    *,
    transport: str,
    extra_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    context: dict[str, Any] = {"transport": transport, "path": request.path}
    if extra_context:
        context.update(extra_context)
    emit_chat_error(
        user=getattr(request, "user", None),
        error=exc,
        context=context,
    )
    return [public_chat_error(exc).as_event()]


async def _iter_provider_turn_events(
    *,
    scope: dict[str, Any],
    conversation: ChatConversation,
    provider: Any,
    messages: list[Message],
    transport: str,
    tool_retries: int = 0,
    tool_calls: list[dict[str, Any]] | None = None,
    recovered_missing_tools: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    tool_calls = list(tool_calls or [])
    assistant_chunks: list[str] = []
    settings = get_chat_settings()
    max_retries = int(settings.get("max_retries_per_message", 8))
    recover_missing_tools = bool(settings.get("recover_missing_tool_calls", False))
    provider_tool_events: list[ToolCallEvent] = []

    async def continue_after_tool_events() -> AsyncIterator[dict[str, Any]]:
        """Execute one completed provider tool batch before the next round."""
        if len(provider_tool_events) > 1 and any(
            event.name == "mutate" for event in provider_tool_events
        ):
            yield {
                "type": "error",
                "message": "Request mutations one at a time.",
                "code": "mutation_batch_unsupported",
            }
            return

        non_mutation_count = sum(
            event.name != "mutate" for event in provider_tool_events
        )
        if tool_retries + non_mutation_count > max_retries:
            yield {
                "type": "error",
                "message": "Chat tool retry limit exceeded.",
                "code": "tool_retry_limit",
            }
            return

        results: list[tuple[ToolCallEvent, Any]] = []
        for tool_event in provider_tool_events:
            yield {
                "type": "tool_call",
                "id": tool_event.id,
                "name": tool_event.name,
                "args": tool_event.args,
            }
            result = await sync_to_async(execute_chat_tool)(
                tool_event.name,
                tool_event.args,
                ScopeChatContext.from_scope(scope),
            )
            tool_calls.append(
                {
                    "name": tool_event.name,
                    "args": dict(tool_event.args),
                    "result": result,
                }
            )
            emit_chat_tool_called(
                user=scope.get("user"),
                tool_name=tool_event.name,
                args=tool_event.args,
                result=result,
            )
            if (
                isinstance(result, dict)
                and result.get("status") == "confirmation_required"
                and tool_event.name == "mutate"
            ):
                if transport == "http":
                    yield {
                        "type": "error",
                        "message": "Confirmed mutations require WebSocket or SSE transport.",
                        "code": "confirmation_required_transport",
                    }
                    return
                await sync_to_async(append_chat_message)(
                    conversation,
                    role="assistant",
                    tool_calls=[
                        {
                            "id": tool_event.id,
                            "name": tool_event.name,
                            "args": tool_event.args,
                        }
                    ],
                )
                await sync_to_async(create_pending_confirmation)(
                    conversation,
                    confirmation_id=tool_event.id,
                    mutation_name=str(result["mutation"]),
                    payload={"input": result["input"]},
                    timeout_seconds=int(
                        get_chat_settings().get("confirm_timeout_seconds", 30)
                    ),
                )
                yield {
                    "type": "confirm_mutation",
                    "id": tool_event.id,
                    "mutation": result["mutation"],
                    "input": result["input"],
                }
                return
            results.append((tool_event, result))

        messages.append(
            Message(
                role="assistant",
                content="",
                tool_calls=tuple(event for event, _ in results),
            )
        )
        await sync_to_async(append_chat_message)(
            conversation,
            role="assistant",
            tool_calls=[
                {"id": event.id, "name": event.name, "args": event.args}
                for event, _ in results
            ],
        )
        for tool_event, result in results:
            yield {
                "type": "tool_result",
                "id": tool_event.id,
                "name": tool_event.name,
                "result": result,
            }
            if tool_event.name == "mutate":
                emit_chat_mutation_executed(
                    user=scope.get("user"),
                    mutation=tool_event.args.get("mutation"),
                    input=tool_event.args.get("input"),
                    result=result,
                )
            tool_content = json.dumps(result, sort_keys=True)
            await sync_to_async(append_chat_message)(
                conversation,
                role="tool",
                content=tool_content,
                tool_name=tool_event.name,
                tool_args=dict(tool_event.args),
                tool_result=result,
                tool_call_id=tool_event.id,
            )
            messages.append(
                Message(
                    role="tool",
                    content=tool_content,
                    tool_call_id=tool_event.id,
                    tool_name=tool_event.name,
                    tool_result=result,
                )
            )

        next_retries = tool_retries + non_mutation_count
        if results and next_retries >= max_retries:
            yield {
                "type": "error",
                "message": "Chat tool retry limit exceeded.",
                "code": "tool_retry_limit",
            }
            return
        async for next_event in _iter_provider_turn_events(
            scope=scope,
            conversation=conversation,
            provider=provider,
            messages=messages,
            transport=transport,
            tool_retries=next_retries,
            tool_calls=tool_calls,
            recovered_missing_tools=recovered_missing_tools,
        ):
            yield next_event

    async for event in _iter_provider_events(
        provider, messages, _build_tool_definitions()
    ):
        if isinstance(event, TextChunkEvent):
            assistant_chunks.append(event.content)
            if not recover_missing_tools:
                yield {"type": "text_chunk", "content": event.content}
            continue
        if isinstance(event, ToolCallEvent):
            provider_tool_events.append(event)
            continue
        if isinstance(event, DoneEvent):
            if provider_tool_events:
                await sync_to_async(enforce_chat_rate_limit)(
                    scope,
                    input_tokens=event.usage.input_tokens,
                    output_tokens=event.usage.output_tokens,
                    count_request=False,
                )
                async for next_event in continue_after_tool_events():
                    yield next_event
                return
            if assistant_chunks:
                assistant_message = "".join(assistant_chunks)
                if (
                    recover_missing_tools
                    and not recovered_missing_tools
                    and not _has_tool_after_last_user(messages)
                    and should_recover_missing_tool_call(
                        user_text=_last_user_text(messages),
                        assistant_text=assistant_message,
                        tool_calls=[],
                    )
                ):
                    messages.append(
                        Message(
                            role="system",
                            content=build_missing_tool_recovery_message(
                                _last_user_text(messages)
                            ),
                        )
                    )
                    async for recovery_event in _iter_provider_turn_events(
                        scope=scope,
                        conversation=conversation,
                        provider=provider,
                        messages=messages,
                        transport=transport,
                        tool_retries=tool_retries,
                        tool_calls=tool_calls,
                        recovered_missing_tools=True,
                    ):
                        yield recovery_event
                    return
                if (
                    recover_missing_tools
                    and not recovered_missing_tools
                    and should_recover_answer_without_query(
                        user_text=_last_user_text(messages),
                        assistant_text=assistant_message,
                        tool_calls=tool_calls,
                    )
                ):
                    messages.append(
                        Message(
                            role="system",
                            content=build_query_required_recovery_message(
                                _last_user_text(messages)
                            ),
                        )
                    )
                    async for recovery_event in _iter_provider_turn_events(
                        scope=scope,
                        conversation=conversation,
                        provider=provider,
                        messages=messages,
                        transport=transport,
                        tool_retries=tool_retries,
                        tool_calls=tool_calls,
                        recovered_missing_tools=True,
                    ):
                        yield recovery_event
                    return
                if recover_missing_tools:
                    for chunk in assistant_chunks:
                        yield {"type": "text_chunk", "content": chunk}
                await sync_to_async(append_chat_message)(
                    conversation,
                    role="assistant",
                    content=assistant_message,
                )
            elif (
                recover_missing_tools
                and not recovered_missing_tools
                and _has_tool_after_last_user(messages)
            ):
                messages.append(
                    Message(
                        role="system",
                        content=build_empty_response_recovery_message(
                            _last_user_text(messages)
                        ),
                    )
                )
                async for recovery_event in _iter_provider_turn_events(
                    scope=scope,
                    conversation=conversation,
                    provider=provider,
                    messages=messages,
                    transport=transport,
                    tool_retries=tool_retries,
                    tool_calls=tool_calls,
                    recovered_missing_tools=True,
                ):
                    yield recovery_event
                return
            await sync_to_async(enforce_chat_rate_limit)(
                scope,
                input_tokens=event.usage.input_tokens,
                output_tokens=event.usage.output_tokens,
                count_request=False,
            )
            yield {
                "type": "done",
                "usage": {
                    "input_tokens": event.usage.input_tokens,
                    "output_tokens": event.usage.output_tokens,
                },
            }
            return

    if provider_tool_events:
        async for next_event in continue_after_tool_events():
            yield next_event


async def _run_provider_turn(
    *,
    scope: dict[str, Any],
    conversation: ChatConversation,
    provider: Any,
    messages: list[Message],
    transport: str,
    tool_retries: int = 0,
    tool_calls: list[dict[str, Any]] | None = None,
    recovered_missing_tools: bool = False,
) -> list[dict[str, Any]]:
    return [
        event
        async for event in _iter_provider_turn_events(
            scope=scope,
            conversation=conversation,
            provider=provider,
            messages=messages,
            transport=transport,
            tool_retries=tool_retries,
            tool_calls=tool_calls,
            recovered_missing_tools=recovered_missing_tools,
        )
    ]


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


async def _prepare_message_request(
    request: HttpRequest,
    *,
    provider_importer: Callable[[], type[Any]] | None = None,
) -> _PreparedMessageRequest:
    scope: dict[str, Any] = {}
    payload = _parse_json_body(request)
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return _PreparedMessageRequest(
            conversation=None,
            scope=scope,
            provider=None,
            messages=None,
            early_events=[
                {
                    "type": "error",
                    "message": "Message text is required.",
                    "code": "bad_message",
                }
            ],
        )
    await sync_to_async(_ensure_session_key)(request)
    scope = await sync_to_async(_request_scope)(request)
    rate_limited = await sync_to_async(enforce_chat_rate_limit)(scope)
    if rate_limited is not None:
        return _PreparedMessageRequest(
            conversation=None,
            scope=scope,
            provider=None,
            messages=None,
            early_events=[
                {
                    "type": "error",
                    "message": "Chat rate limit exceeded. Try again later.",
                    "code": "rate_limited",
                    "retry_after_seconds": rate_limited["retry_after_seconds"],
                }
            ],
        )
    conversation = await sync_to_async(_conversation_for_request)(request)
    await sync_to_async(append_chat_message)(conversation, role="user", content=text)
    provider_cls = (
        provider_importer() if provider_importer is not None else import_provider()
    )
    provider = provider_cls()
    planned_settings = get_planned_chat_settings()
    messages = await _build_messages(
        conversation,
        provider,
        allow_summarization=not planned_settings.enabled,
        scope=scope,
    )
    emit_chat_message_received(
        user=getattr(request, "user", None),
        message=text,
        conversation_id=getattr(conversation, "pk", None),
    )
    return _PreparedMessageRequest(
        conversation=conversation,
        scope=scope,
        provider=provider,
        messages=messages,
        early_events=None,
        user_text=text,
        planned_settings=planned_settings,
    )


async def _iter_prepared_message_events(
    prepared: _PreparedMessageRequest,
    *,
    transport: str,
) -> AsyncIterator[dict[str, Any]]:
    """Select one post-admission transport strategy without changing legacy loops."""
    if (
        prepared.provider is None
        or prepared.messages is None
        or prepared.conversation is None
    ):
        return
    planned_settings = prepared.planned_settings or get_planned_chat_settings()
    if not planned_settings.enabled:
        async for event in _iter_provider_turn_events(
            scope=prepared.scope,
            conversation=prepared.conversation,
            provider=prepared.provider,
            messages=prepared.messages,
            transport=transport,
        ):
            yield event
        return

    callbacks = SchedulerCallbacks(enforce_rate_limit=enforce_chat_rate_limit)
    planned_turn = await prepare_planned_turn(
        prepared.user_text
        or next(
            (
                message.content
                for message in reversed(prepared.messages)
                if message.role == "user"
            ),
            "",
        ),
        prepared.messages,
        planned_settings,
        _planned_catalog_summary(planned_settings),
        callbacks=callbacks,
        scope=prepared.scope,
    )
    if planned_turn.mutation_plan is not None:
        async for event in _iter_provider_turn_events(
            scope=prepared.scope,
            conversation=prepared.conversation,
            provider=prepared.provider,
            messages=prepared.messages,
            transport=transport,
        ):
            yield event
        return
    async for event in iter_planned_read_events(
        planned_turn,
        scope=prepared.scope,
        conversation=prepared.conversation,
        messages=prepared.messages,
        callbacks=callbacks,
    ):
        yield event


async def _execute_message_request(
    request: HttpRequest,
    *,
    transport: str,
) -> tuple[ChatConversation | None, list[dict[str, Any]]]:
    try:
        prepared = await _prepare_message_request(request)
        if prepared.early_events is not None:
            return prepared.conversation, prepared.early_events
        events = [
            event
            async for event in _iter_prepared_message_events(
                prepared, transport=transport
            )
        ]
    except Exception as exc:  # noqa: BLE001
        events = _chat_error_events(exc, request, transport=transport)
        return None, events
    return prepared.conversation, events


async def _stream_message_events(
    request: HttpRequest,
    *,
    transport: str,
    provider_importer: Callable[[], type[Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    try:
        prepared = await _prepare_message_request(
            request,
            provider_importer=provider_importer,
        )
        if prepared.early_events is not None:
            for event in prepared.early_events:
                yield event
            return
        async for event in _iter_prepared_message_events(prepared, transport=transport):
            yield event
    except Exception as exc:  # noqa: BLE001
        for event in _chat_error_events(exc, request, transport=transport):
            yield event


def _encode_sse_event(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode()


async def _async_sse_stream(
    request: HttpRequest,
    *,
    provider_importer: Callable[[], type[Any]] | None = None,
) -> AsyncIterator[bytes]:
    async for event in _stream_message_events(
        request,
        transport="sse",
        provider_importer=provider_importer,
    ):
        yield _encode_sse_event(event)


async def _execute_confirmation_request(
    request: HttpRequest,
) -> tuple[ChatConversation, list[dict[str, Any]]]:
    conversation = await sync_to_async(_conversation_for_request)(request)
    confirmation_id: str | None = None
    try:
        payload = _parse_json_body(request)
        requested_confirmation_id = payload.get("confirmation_id")
        confirmed = payload.get("confirmed")
        if not isinstance(requested_confirmation_id, str) or not isinstance(
            confirmed, bool
        ):
            return conversation, [
                {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
            ]
        confirmation_id = requested_confirmation_id
        pending = await sync_to_async(ChatPendingConfirmation.claim_for_conversation)(
            conversation=conversation,
            confirmation_id=confirmation_id,
            now=timezone.now(),
        )
        if pending is None:
            return conversation, [
                {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
            ]

        scope = _request_scope(request)
        if confirmed:
            result = await sync_to_async(execute_confirmed_chat_mutation)(
                mutation=pending.mutation_name,
                input=pending.payload.get("input", {}),
                context=ScopeChatContext.from_scope(scope),
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
            tool_call_id=pending.confirmation_id,
        )
        provider = import_provider()()
        messages = await _build_messages(conversation, provider, scope=scope)
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
                scope=scope,
                conversation=conversation,
                provider=provider,
                messages=messages,
                transport="sse",
            )
        )
    except Exception as exc:  # noqa: BLE001
        context: dict[str, Any] = {}
        if confirmation_id is not None:
            context["confirmation_id"] = confirmation_id
        events = _chat_error_events(
            exc,
            request,
            transport="http_confirm",
            extra_context=context,
        )
    return conversation, events


@csrf_protect
@require_POST
def chat_http_view(request: HttpRequest) -> JsonResponse:
    """Handle one non-streaming chat request over HTTP."""
    try:
        denial = _check_permission(request)
        if denial is not None:
            return denial
        _conversation, events = async_to_sync(_execute_message_request)(
            request, transport="http"
        )
    except Exception as exc:  # noqa: BLE001
        events = _chat_error_events(exc, request, transport="http")
    return JsonResponse({"events": events, "answer": _answer_from_events(events)})


@csrf_protect
@require_POST
def chat_sse_view(request: HttpRequest) -> StreamingHttpResponse:
    """Stream one chat request over server-sent events."""
    try:
        denial = _check_permission(request)
        if denial is not None:
            return StreamingHttpResponse(
                iter([_encode_sse_event({"detail": "Forbidden"})]),
                status=403,
                content_type="text/event-stream",
            )
        provider_importer = import_provider
        # Streaming headers are committed before the generator runs. Establish the
        # anonymous session here so SessionMiddleware can attach its cookie.
        _ensure_session_key(request)
    except Exception as exc:  # noqa: BLE001
        events = _chat_error_events(exc, request, transport="sse")
        return StreamingHttpResponse(
            (_encode_sse_event(event) for event in events),
            content_type="text/event-stream",
        )

    response = StreamingHttpResponse(
        _async_sse_stream(request, provider_importer=provider_importer),
        content_type="text/event-stream",
    )
    _attach_session_cookie(response, request)
    return response


@csrf_protect
@require_POST
def chat_confirm_view(request: HttpRequest) -> JsonResponse:
    """Resolve a pending mutation confirmation over HTTP."""
    try:
        denial = _check_permission(request)
        if denial is not None:
            return denial
        _conversation, events = async_to_sync(_execute_confirmation_request)(request)
    except Exception as exc:  # noqa: BLE001
        events = _chat_error_events(exc, request, transport="http_confirm")
    return JsonResponse({"events": events, "answer": _answer_from_events(events)})
