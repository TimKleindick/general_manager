"""WebSocket consumer for chat."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from typing import TYPE_CHECKING, Any

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

from general_manager.chat.audit import emit_chat_audit_event
from general_manager.chat.errors import public_chat_error
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
    ToolDefinition,
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

if TYPE_CHECKING:
    from general_manager.chat.models import ChatConversation

    class _ChatConsumerBase:
        """Typed subset of the Channels websocket consumer base."""

        scope: dict[str, Any]

        @classmethod
        def as_asgi(cls, **initkwargs: Any) -> Any:
            """Build an ASGI application callable for the consumer."""
            ...

        async def accept(self, subprotocol: str | None = None) -> None:
            """Accept the websocket connection."""
            ...

        async def close(self, code: int | None = None) -> None:
            """Close the websocket connection with an optional code."""
            ...

        async def send_json(self, content: Any, close: bool = False) -> None:
            """Send a JSON-serializable websocket message."""
            ...

        async def disconnect(self, code: int) -> None:
            """Handle websocket disconnection from the base class."""
            ...

else:
    _ChatConsumerBase = AsyncJsonWebsocketConsumer


async def _iter_provider_events(
    provider: Any,
    messages: list[Message],
    tools: list[ToolDefinition],
) -> Any:
    """Yield provider events while enforcing first-chunk and stall timeouts."""
    provider_config = get_chat_settings().get("provider_config", {})
    request_timeout = float(provider_config.get("timeout_seconds", 60))
    stream_timeout = float(provider_config.get("stream_timeout_seconds", 30))
    stream = provider.complete(messages, tools).__aiter__()
    first_chunk = True
    while True:
        timeout = request_timeout if first_chunk else stream_timeout
        try:
            event = await asyncio.wait_for(stream.__anext__(), timeout=timeout)
        except StopAsyncIteration:
            return
        first_chunk = False
        yield event


def _last_user_text(messages: list[Message]) -> str:
    """Return the most recent user message content from a provider history."""
    return next(
        (message.content for message in reversed(messages) if message.role == "user"),
        "",
    )


def _has_tool_after_last_user(messages: list[Message]) -> bool:
    """Return whether a tool result exists after the most recent user message."""
    for message in reversed(messages):
        if message.role == "tool":
            return True
        if message.role == "user":
            return False
    return False


def _confirmation_unavailable_event() -> dict[str, str]:
    """Return the terminal event for a confirmation that can no longer be claimed."""
    return {
        "type": "error",
        "message": "Pending confirmation is no longer available.",
        "code": "confirmation_unavailable",
    }


class ChatConsumer(_ChatConsumerBase):
    """Minimal streaming chat consumer for Phase 1 foundation work."""

    _active_turn: asyncio.Future[None] | None = None
    _pending_confirmation: dict[str, Any] | None = None
    _confirmation_waiter: asyncio.Future[bool] | None = None
    _confirmation_timeout_task: asyncio.Task[None] | None = None
    _provider_task: asyncio.Task[Any] | None = None
    _history_cache: list[dict[str, str]] | None = None
    conversation: "ChatConversation | None" = None

    @staticmethod
    def _serialize_tool_result(result: Any) -> str:
        return json.dumps(result, sort_keys=True)

    @staticmethod
    def _build_tool_definitions() -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=tool["name"],
                description=str(tool["description"]),
                input_schema=dict(tool["input_schema"]),
            )
            for tool in get_tool_definitions()
        ]

    async def _get_persistent_conversation(
        self, *, suppress_errors: bool = True
    ) -> ChatConversation | None:
        from general_manager.chat.models import ChatConversation

        try:
            return await sync_to_async(ChatConversation.for_actor)(
                user=self.scope.get("user"),
                session_key=getattr(self, "session_key", None),
            )
        except Exception:
            if not suppress_errors:
                raise
            return None

    async def _load_history(self) -> list[dict[str, str]]:
        if self.conversation is not None:
            from general_manager.chat.models import get_conversation_messages

            try:
                messages = await sync_to_async(get_conversation_messages)(
                    self.conversation
                )
                history = [
                    {"role": item.role, "content": item.content} for item in messages
                ]
                self._history_cache = list(history)
            except Exception:  # noqa: BLE001
                return list(self._history_cache or [])
            else:
                return history
        return list(self._history_cache or [])

    async def _record_message(
        self,
        *,
        role: str,
        content: str,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        tool_result: Any = None,
    ) -> None:
        from general_manager.chat.models import append_chat_message

        if self.conversation is not None:
            try:
                await sync_to_async(append_chat_message)(
                    self.conversation,
                    role=role,
                    content=content,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result=tool_result,
                )
            except Exception:  # noqa: BLE001
                ...
            else:
                if self._history_cache is None:
                    self._history_cache = []
                self._history_cache.append({"role": role, "content": content})
                return
        if self._history_cache is None:
            self._history_cache = []
        self._history_cache.append({"role": role, "content": content})

    async def connect(self) -> None:
        """Initialize provider, permissions, and persistent chat state."""
        try:
            permission = get_chat_permission()
            if (
                callable(permission)
                and permission(self.scope.get("user"), self.scope) is False
            ):
                await self.close(code=4403)
                return
            session = self.scope.get("session")
            session_key = getattr(session, "session_key", None)
            if session is not None and not session_key:
                session.save()
                session_key = getattr(session, "session_key", None)
            self.session_key = session_key
            provider_cls = import_provider()
            self.provider = provider_cls()
            self._active_turn: asyncio.Future[None] | None = None
            self._pending_confirmation = None
            self._confirmation_waiter = None
            self._confirmation_timeout_task = None
            self._history_cache = []
            self.conversation = await self._get_persistent_conversation(
                suppress_errors=False
            )
            await self.accept()
        except Exception as exc:  # noqa: BLE001
            emit_chat_error(
                user=self.scope.get("user"),
                error=exc,
                context={"transport": "websocket", "phase": "connect"},
            )
            await self.close(code=1011)

    async def disconnect(self, code: int) -> None:
        """Cancel in-flight chat work before closing the websocket."""
        provider_task = getattr(self, "_provider_task", None)
        if provider_task is not None and not provider_task.done():
            provider_task.cancel()
            try:
                await provider_task
            except asyncio.CancelledError:
                pass
        await self._cancel_confirmation_timeout()
        await super().disconnect(code)

    async def receive_json(self, content: Any, **_kwargs: Any) -> None:
        """Route incoming websocket payloads to chat or confirmation handlers."""
        if not isinstance(content, dict):
            await self.send_json(
                {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
            )
            return
        message_type = content.get("type")
        if message_type == "confirm":
            try:
                await self._handle_confirmation_response(content)
            except Exception as exc:  # noqa: BLE001
                context: dict[str, Any] = {
                    "transport": "websocket",
                    "session_key": self.session_key,
                }
                confirmation_id = content.get("confirmation_id")
                if isinstance(confirmation_id, str):
                    context["confirmation_id"] = confirmation_id
                emit_chat_error(
                    user=self.scope.get("user"),
                    error=exc,
                    context=context,
                )
                await self.send_json(public_chat_error(exc).as_event())
            return
        if message_type != "message":
            await self.send_json(
                {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
            )
            return
        if self._pending_confirmation is not None:
            await self.send_json(
                {
                    "type": "error",
                    "message": "A mutation confirmation is still pending.",
                    "code": "confirmation_pending",
                }
            )
            return
        active_turn = getattr(self, "_active_turn", None)
        if active_turn is not None and not active_turn.done():
            await self.send_json(
                {
                    "type": "error",
                    "message": "A chat turn is already in progress.",
                    "code": "turn_in_progress",
                }
            )
            return
        text = content.get("text")
        if not isinstance(text, str) or not text.strip():
            await self.send_json(
                {
                    "type": "error",
                    "message": "Message text is required.",
                    "code": "bad_message",
                }
            )
            return
        loop = asyncio.get_running_loop()
        self._active_turn = loop.create_future()
        try:
            if self.conversation is None:
                self.conversation = await self._get_persistent_conversation()
            rate_limit_result = enforce_chat_rate_limit(self.scope)
            if rate_limit_result is not None:
                await self.send_json(
                    {
                        "type": "error",
                        "message": "Chat rate limit exceeded. Try again later.",
                        "code": "rate_limited",
                        "retry_after_seconds": rate_limit_result["retry_after_seconds"],
                    }
                )
                return
            history = await self._load_history()
            await self._record_message(role="user", content=text)
            history = await self._load_history()
            emit_chat_audit_event(
                "user_message",
                {"message": text, "session_key": self.session_key},
            )
            messages = [Message(role="system", content=build_system_prompt())]
            messages.extend(
                Message(role=item["role"], content=item["content"]) for item in history
            )
            emit_chat_message_received(
                user=self.scope.get("user"),
                message=text,
                conversation_id=getattr(self.conversation, "pk", None),
            )
            await self._stream_provider_turn(messages, history, tool_retries=0)
        except Exception as exc:  # noqa: BLE001
            emit_chat_error(
                user=self.scope.get("user"),
                error=exc,
                context={"transport": "websocket", "session_key": self.session_key},
            )
            await self.send_json(public_chat_error(exc).as_event())
        finally:
            if self._active_turn is not None and not self._active_turn.done():
                self._active_turn.set_result(None)

    async def _stream_provider_turn(
        self,
        messages: list[Message],
        history: list[dict[str, str]],
        *,
        tool_retries: int,
        tool_calls: list[dict[str, Any]] | None = None,
        recovered_missing_tools: bool = False,
    ) -> None:
        tool_calls = list(tool_calls or [])
        assistant_chunks: list[str] = []
        self._provider_task = asyncio.current_task()
        recover_missing_tools = bool(
            get_chat_settings().get("recover_missing_tool_calls", False)
        )
        try:
            async for event in _iter_provider_events(
                self.provider, messages, self._build_tool_definitions()
            ):
                if isinstance(event, TextChunkEvent):
                    assistant_chunks.append(event.content)
                    if not recover_missing_tools:
                        await self.send_json(
                            {"type": "text_chunk", "content": event.content}
                        )
                elif isinstance(event, ToolCallEvent):
                    max_retries = int(
                        get_chat_settings().get("max_retries_per_message", 8)
                    )
                    if event.name != "mutate" and tool_retries >= max_retries:
                        await self.send_json(
                            {
                                "type": "error",
                                "message": "Chat tool retry limit exceeded.",
                                "code": "tool_retry_limit",
                            }
                        )
                        return
                    should_resume = await self._handle_tool_call(
                        event,
                        messages,
                        history,
                        tool_retries=tool_retries,
                        tool_calls=tool_calls,
                        recovered_missing_tools=recovered_missing_tools,
                    )
                    if not should_resume:
                        return
                    return
                elif isinstance(event, DoneEvent):
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
                            await self._stream_provider_turn(
                                messages,
                                history,
                                tool_retries=tool_retries,
                                tool_calls=tool_calls,
                                recovered_missing_tools=True,
                            )
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
                            await self._stream_provider_turn(
                                messages,
                                history,
                                tool_retries=tool_retries,
                                tool_calls=tool_calls,
                                recovered_missing_tools=True,
                            )
                            return
                        if recover_missing_tools:
                            for chunk in assistant_chunks:
                                await self.send_json(
                                    {"type": "text_chunk", "content": chunk}
                                )
                        await self._record_message(
                            role="assistant", content=assistant_message
                        )
                        emit_chat_audit_event(
                            "assistant_message",
                            {
                                "message": assistant_message,
                                "session_key": self.session_key,
                            },
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
                        await self._stream_provider_turn(
                            messages,
                            history,
                            tool_retries=tool_retries,
                            tool_calls=tool_calls,
                            recovered_missing_tools=True,
                        )
                        return
                    enforce_chat_rate_limit(
                        self.scope,
                        input_tokens=event.usage.input_tokens,
                        output_tokens=event.usage.output_tokens,
                        count_request=False,
                    )
                    await self.send_json(
                        {
                            "type": "done",
                            "usage": {
                                "input_tokens": event.usage.input_tokens,
                                "output_tokens": event.usage.output_tokens,
                            },
                        }
                    )
        finally:
            self._provider_task = None

    async def _handle_tool_call(
        self,
        event: ToolCallEvent,
        messages: list[Message],
        history: list[dict[str, str]],
        *,
        tool_retries: int,
        tool_calls: list[dict[str, Any]] | None = None,
        recovered_missing_tools: bool = False,
    ) -> bool:
        tool_calls = list(tool_calls or [])
        emit_chat_audit_event(
            "tool_call",
            {
                "tool_name": event.name,
                "args": event.args,
                "session_key": self.session_key,
            },
        )
        await self.send_json(
            {
                "type": "tool_call",
                "id": event.id,
                "name": event.name,
                "args": event.args,
            }
        )
        result = await sync_to_async(execute_chat_tool)(
            event.name, event.args, ScopeChatContext.from_scope(self.scope)
        )
        tool_calls.append(
            {"name": event.name, "args": dict(event.args), "result": result}
        )
        emit_chat_tool_called(
            user=self.scope.get("user"),
            tool_name=event.name,
            args=event.args,
            result=result,
        )
        if (
            isinstance(result, dict)
            and result.get("status") == "confirmation_required"
            and event.name == "mutate"
        ):
            timeout_seconds = int(
                get_chat_settings().get("confirm_timeout_seconds", 30)
            )
            emit_chat_audit_event(
                "tool_result",
                {
                    "tool_name": event.name,
                    "args": event.args,
                    "result": result,
                    "session_key": self.session_key,
                },
            )
            durable = False
            if self.conversation is not None:
                from general_manager.chat.models import create_pending_confirmation

                try:
                    await sync_to_async(create_pending_confirmation)(
                        self.conversation,
                        confirmation_id=event.id,
                        mutation_name=str(result["mutation"]),
                        payload={"input": result["input"]},
                        timeout_seconds=timeout_seconds,
                    )
                except Exception as exc:  # noqa: BLE001
                    emit_chat_error(
                        user=self.scope.get("user"),
                        error=exc,
                        context={
                            "transport": "websocket",
                            "phase": "create_pending_confirmation",
                            "confirmation_id": event.id,
                            "conversation_id": getattr(self.conversation, "pk", None),
                            "session_key": self.session_key,
                        },
                    )
                    await self.send_json(public_chat_error(exc).as_event())
                    return True
                else:
                    durable = True
            await self.send_json(
                {
                    "type": "confirm_mutation",
                    "id": event.id,
                    "mutation": result["mutation"],
                    "input": result["input"],
                }
            )
            self._pending_confirmation = {
                "id": event.id,
                "mutation": result["mutation"],
                "input": result["input"],
                "messages": list(messages),
                "history": history,
                "expires_at": timezone.now() + timedelta(seconds=timeout_seconds),
                "durable": durable,
            }
            self._confirmation_waiter = asyncio.get_running_loop().create_future()
            self._confirmation_timeout_task = asyncio.create_task(
                self._await_confirmation_timeout(
                    confirmation_id=event.id,
                    timeout_seconds=timeout_seconds,
                )
            )
            return False
        emit_chat_audit_event(
            "tool_result",
            {
                "tool_name": event.name,
                "args": event.args,
                "result": result,
                "session_key": self.session_key,
            },
        )
        if event.name == "mutate":
            emit_chat_mutation_executed(
                user=self.scope.get("user"),
                mutation=event.args.get("mutation"),
                input=event.args.get("input"),
                result=result,
            )
        await self.send_json(
            {
                "type": "tool_result",
                "id": event.id,
                "name": event.name,
                "result": result,
            }
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
        tool_message = Message(role="tool", content=self._serialize_tool_result(result))
        messages.append(tool_message)
        await self._record_message(
            role="tool",
            content=tool_message.content,
            tool_name=event.name,
            tool_args=dict(event.args),
            tool_result=result,
        )
        next_tool_retries = tool_retries + (0 if event.name == "mutate" else 1)
        max_retries = int(get_chat_settings().get("max_retries_per_message", 8))
        if event.name != "mutate" and next_tool_retries >= max_retries:
            await self.send_json(
                {
                    "type": "error",
                    "message": "Chat tool retry limit exceeded.",
                    "code": "tool_retry_limit",
                }
            )
            return True
        await self._stream_provider_turn(
            messages,
            history,
            tool_retries=next_tool_retries,
            tool_calls=tool_calls,
            recovered_missing_tools=recovered_missing_tools,
        )
        return True

    async def _await_confirmation_timeout(
        self, *, confirmation_id: str, timeout_seconds: int
    ) -> None:
        waiter = self._confirmation_waiter
        if waiter is None:
            return
        try:
            await asyncio.wait_for(waiter, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            pending = self._pending_confirmation
            if pending is not None and pending.get("id") == confirmation_id:
                if bool(pending.get("durable")):
                    claimed = await self._claim_durable_pending_confirmation(
                        confirmation_id=confirmation_id,
                        allow_expired=True,
                    )
                    if not claimed:
                        await self._cancel_confirmation_timeout()
                        await self.send_json(_confirmation_unavailable_event())
                        self._pending_confirmation = None
                        return
                await self._resolve_pending_confirmation(
                    pending=pending,
                    confirmed=False,
                    cancellation_reason="confirmation_timed_out",
                )
        except asyncio.CancelledError:
            raise
        finally:
            if self._confirmation_timeout_task is asyncio.current_task():
                self._confirmation_timeout_task = None

    async def _cancel_confirmation_timeout(self) -> None:
        waiter = self._confirmation_waiter
        self._confirmation_waiter = None
        if waiter is not None and not waiter.done():
            waiter.cancel()
        task = self._confirmation_timeout_task
        self._confirmation_timeout_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _claim_durable_pending_confirmation(
        self, *, confirmation_id: str, allow_expired: bool = False
    ) -> bool:
        if self.conversation is None:
            return False
        from general_manager.chat.models import ChatPendingConfirmation

        claim_kwargs: dict[str, Any] = {
            "conversation": self.conversation,
            "confirmation_id": confirmation_id,
        }
        if allow_expired:
            claim_kwargs["allow_expired"] = True
        claimed = await sync_to_async(ChatPendingConfirmation.claim_for_conversation)(
            **claim_kwargs,
        )
        return claimed is not None

    async def _resolve_pending_confirmation(
        self,
        *,
        pending: dict[str, Any],
        confirmed: bool,
        cancellation_reason: str,
    ) -> None:
        confirmation_id = str(pending["id"])
        previous_active_turn = getattr(self, "_active_turn", None)
        followup_turn = asyncio.get_running_loop().create_future()
        self._active_turn = followup_turn
        try:
            await self._cancel_confirmation_timeout()
            self._pending_confirmation = None
            if confirmed:
                result = await sync_to_async(execute_chat_tool)(
                    "mutate",
                    {
                        "mutation": pending["mutation"],
                        "input": pending["input"],
                        "confirmed": True,
                    },
                    ScopeChatContext.from_scope(self.scope),
                )
            else:
                result = {"status": "cancelled", "reason": cancellation_reason}
            emit_chat_tool_called(
                user=self.scope.get("user"),
                tool_name="mutate",
                args={"mutation": pending["mutation"], "input": pending["input"]},
                result=result,
            )
            emit_chat_mutation_executed(
                user=self.scope.get("user"),
                mutation=pending["mutation"],
                input=pending["input"],
                result=result,
            )
            emit_chat_audit_event(
                "tool_result",
                {
                    "tool_name": "mutate",
                    "args": {
                        "mutation": pending["mutation"],
                        "input": pending["input"],
                    },
                    "result": result,
                    "session_key": self.session_key,
                },
            )
            tool_content = self._serialize_tool_result(result)
            await self.send_json(
                {
                    "type": "tool_result",
                    "id": confirmation_id,
                    "name": "mutate",
                    "result": result,
                }
            )
            messages = list(pending["messages"])
            messages.append(
                Message(
                    role="assistant",
                    content=(
                        "Called tool mutate. The next message is the tool result; "
                        "answer from it exactly."
                    ),
                )
            )
            messages.append(Message(role="tool", content=tool_content))
            await self._record_message(
                role="tool",
                content=tool_content,
                tool_name="mutate",
                tool_args={"mutation": pending["mutation"], "input": pending["input"]},
                tool_result=result,
            )
            await self._stream_provider_turn(
                messages, list(pending["history"]), tool_retries=0
            )
        finally:
            if not followup_turn.done():
                followup_turn.set_result(None)
            if self._active_turn is followup_turn:
                self._active_turn = previous_active_turn

    async def _handle_confirmation_response(self, content: dict[str, Any]) -> None:
        pending = self._pending_confirmation
        confirmation_id = content.get("confirmation_id")
        confirmed = content.get("confirmed")
        if not isinstance(confirmation_id, str) or not isinstance(confirmed, bool):
            await self.send_json(
                {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
            )
            return

        from general_manager.chat.models import ChatPendingConfirmation

        db_pending: Any | None = None
        if pending is None and self.conversation is not None:
            db_pending = await sync_to_async(
                ChatPendingConfirmation.claim_for_conversation
            )(
                conversation=self.conversation,
                confirmation_id=confirmation_id,
            )
            if db_pending is not None:
                history = await self._load_history()
                pending = {
                    "id": db_pending.confirmation_id,
                    "mutation": db_pending.mutation_name,
                    "input": db_pending.payload.get("input", {}),
                    "messages": [Message(role="system", content=build_system_prompt())]
                    + [
                        Message(role=item["role"], content=item["content"])
                        for item in history
                    ],
                    "history": history,
                    "expires_at": db_pending.expires_at,
                    "durable": False,
                }
        if pending is None or confirmation_id != pending.get("id"):
            await self.send_json(
                {"type": "error", "message": "Unknown chat event.", "code": "bad_event"}
            )
            return
        if bool(pending.get("durable")):
            claimed = await self._claim_durable_pending_confirmation(
                confirmation_id=confirmation_id
            )
            if not claimed:
                await self._cancel_confirmation_timeout()
                await self.send_json(_confirmation_unavailable_event())
                self._pending_confirmation = None
                return
        cancellation_reason = "user_rejected"
        expires_at = pending.get("expires_at")
        if isinstance(expires_at, datetime) and expires_at <= timezone.now():
            confirmed = False
            cancellation_reason = "confirmation_timed_out"
        waiter = self._confirmation_waiter
        if waiter is not None and not waiter.done():
            waiter.set_result(bool(confirmed))
        await self._resolve_pending_confirmation(
            pending=pending,
            confirmed=bool(confirmed),
            cancellation_reason=cancellation_reason,
        )
