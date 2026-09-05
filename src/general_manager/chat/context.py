"""Shared bounded provider-context preparation for legacy chat transports."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from asgiref.sync import sync_to_async

from general_manager.chat.models import (
    ChatConversation,
    build_conversation_context,
    get_conversation_messages,
    provider_messages_from_context,
    update_conversation_summary,
)
from general_manager.chat.providers.base import DoneEvent, Message, TextChunkEvent
from general_manager.chat.rate_limits import enforce_chat_rate_limit
from general_manager.chat.settings import get_chat_settings
from general_manager.chat.system_prompt import build_system_prompt


async def summarize_messages_with_provider(
    provider: Any,
    messages: list[Any],
    *,
    scope: dict[str, Any] | None = None,
) -> str:
    """Summarize one history prefix within a whole-request provider deadline."""
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
    provider_config = getattr(provider, "provider_config", None)
    if not isinstance(provider_config, Mapping):
        provider_config = get_chat_settings().get("provider_config", {})
    timeout_seconds = float(provider_config.get("timeout_seconds", 60))
    chunks: list[str] = []

    async def consume() -> None:
        async for event in provider.complete(prompt_messages, []):
            if isinstance(event, TextChunkEvent):
                chunks.append(event.content)
            elif isinstance(event, DoneEvent) and scope is not None:
                await sync_to_async(enforce_chat_rate_limit)(
                    scope,
                    input_tokens=event.usage.input_tokens,
                    output_tokens=event.usage.output_tokens,
                    count_request=False,
                )

    await asyncio.wait_for(consume(), timeout=timeout_seconds)
    return "".join(chunks).strip()


async def prepare_conversation_messages(
    conversation: ChatConversation,
    provider: Any,
    *,
    allow_summarization: bool = True,
    scope: dict[str, Any] | None = None,
    summarize: Callable[[Any, list[Any]], Awaitable[str]] | None = None,
) -> list[Message]:
    """Return system plus bounded durable history for a legacy provider turn."""
    settings = get_chat_settings()
    summarize_after = int(settings.get("summarize_after", 20))
    max_recent_messages = int(settings.get("max_recent_messages", 12))
    conversation_messages = await sync_to_async(get_conversation_messages)(conversation)
    if allow_summarization and len(conversation_messages) > summarize_after:
        older_messages = conversation_messages[:-max_recent_messages]
        summary_is_current = (
            bool(conversation.summary_text.strip())
            and getattr(conversation, "summarized_through_id", None)
            == older_messages[-1].pk
        )
        if not summary_is_current:
            summary_text = (
                await summarize(provider, older_messages)
                if summarize is not None
                else await summarize_messages_with_provider(
                    provider, older_messages, scope=scope
                )
            )
            if summary_text:
                await sync_to_async(update_conversation_summary)(
                    conversation,
                    summary_text=summary_text,
                    summarized_through=older_messages[-1],
                )
    context = await sync_to_async(build_conversation_context)(conversation)
    return [
        Message(role="system", content=build_system_prompt()),
        *provider_messages_from_context(context),
    ]


def _render_summary_source(messages: list[Any]) -> str:
    return "\n".join(
        f"{item.role}{f':{item.tool_name}' if item.tool_name else ''}: {item.content}"
        for item in messages
    )
