"""Cache-backed rate limiting for chat transports."""

from __future__ import annotations

import math
from typing import Any

from django.core.cache import cache

from general_manager.chat.settings import get_chat_settings


def _scope_identifier(scope: dict[str, Any]) -> str:
    user = scope.get("user")
    user_id = getattr(user, "pk", None)
    if user_id is not None:
        return f"user:{user_id}"

    session = scope.get("session")
    session_key = getattr(session, "session_key", None)
    if isinstance(session_key, str) and session_key:
        return f"session:{session_key}"

    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return f"ip:{client[0]}"
    return "anonymous"


def _counter_key(identifier: str, counter: str) -> str:
    return f"general_manager:chat_rate_limit:{identifier}:{counter}"


def _increment(identifier: str, counter: str, amount: int, window_seconds: int) -> int:
    key = _counter_key(identifier, counter)
    added = cache.add(key, amount, timeout=window_seconds)
    if added:
        return amount
    try:
        total = cache.incr(key, amount)
    except ValueError:
        cache.set(key, amount, timeout=window_seconds)
        total = amount
    return int(total)


def enforce_chat_rate_limit(
    scope: dict[str, Any],
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> dict[str, Any] | None:
    """Increment counters and return metadata when the caller exceeds a limit."""
    settings = get_chat_settings()
    rate_limit = settings["rate_limit"]
    requests = rate_limit.get("requests")
    window_seconds = int(rate_limit.get("window_seconds") or 60)
    identifier = _scope_identifier(scope)

    if isinstance(requests, int) and requests > 0:
        request_total = _increment(identifier, "requests", 1, window_seconds)
        if request_total > requests:
            return {
                "scope": identifier,
                "retry_after_seconds": window_seconds,
            }

    input_budget = rate_limit.get("input_tokens")
    if isinstance(input_budget, int) and input_budget > 0 and input_tokens > 0:
        total = _increment(identifier, "input_tokens", input_tokens, window_seconds)
        if total > input_budget:
            return {
                "scope": identifier,
                "retry_after_seconds": window_seconds,
            }

    token_budget = rate_limit.get("tokens")
    total_tokens = input_tokens + output_tokens
    if isinstance(token_budget, int) and token_budget > 0 and total_tokens > 0:
        total = _increment(identifier, "tokens", total_tokens, window_seconds)
        if total > token_budget:
            return {
                "scope": identifier,
                "retry_after_seconds": window_seconds,
            }

    output_budget = rate_limit.get("output_tokens")
    if isinstance(output_budget, int) and output_budget > 0 and output_tokens > 0:
        total = _increment(identifier, "output_tokens", output_tokens, window_seconds)
        if total > output_budget:
            return {
                "scope": identifier,
                "retry_after_seconds": window_seconds,
            }
    return None


def get_query_timeout_ms() -> int | None:
    """Return configured query timeout in milliseconds."""
    timeout_seconds = get_chat_settings().get("query_timeout_seconds")
    if timeout_seconds is None:
        return None
    return max(1, math.ceil(float(timeout_seconds) * 1000))
