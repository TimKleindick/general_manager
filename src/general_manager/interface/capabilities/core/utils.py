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
    """
    Invoke the provided callable while emitting observability hooks from the target's observability capability when available.

    If the target exposes a `get_capability_handler("observability")`, this function will call the capability's optional hooks in this order: `before_operation` (before invoking `func`), `on_error` (if `func` raises), and `after_operation` (after successful completion). The supplied `payload` is shallow-copied before being passed to hooks. If no observability capability is present, `func` is executed directly.

    Parameters:
        target: Object that may provide a `get_capability_handler` method to obtain an observability capability.
        operation (str): Logical name of the operation for observability hooks.
        payload (dict[str, Any]): Data passed to observability hooks; a shallow copy is made to avoid mutation of the original.
        func (Callable[[], ResultT]): Callable to execute for the operation.

    Returns:
        ResultT: The value returned by `func`.

    Raises:
        Exception: Re-raises any exception raised by `func` after invoking `on_error` if that hook is present.
    """
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
