"""Shared helpers for capability implementations."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

ResultT = TypeVar("ResultT")


def with_observability(
    target: Any,
    *,
    operation: str,
    payload: dict[str, Any],
    func: Callable[[], ResultT],
) -> ResultT:
    """Invoke func while emitting observability events when available."""
    get_handler = getattr(target, "get_capability_handler", None)
    if get_handler is None:
        return func()
    capability = get_handler("observability")
    if capability is None:
        return func()
    before = getattr(capability, "before_operation", None)
    after = getattr(capability, "after_operation", None)
    on_error = getattr(capability, "on_error", None)
    safe_payload = dict(payload)
    if before is not None:
        before(operation=operation, target=target, payload=safe_payload)
    try:
        result = func()
    except Exception as exc:  # pragma: no cover - propagate but log
        if on_error is not None:
            on_error(
                operation=operation,
                target=target,
                payload=safe_payload,
                error=exc,
            )
        raise
    if after is not None:
        after(
            operation=operation,
            target=target,
            payload=safe_payload,
            result=result,
        )
    return result


__all__ = ["with_observability"]
