"""Shared helpers for capability implementations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Callable, Protocol, TypeVar, cast

ResultT = TypeVar("ResultT")


class ObservabilityCapability(Protocol):
    """
    Structural hook surface consumed internally by :func:`with_observability`.

    The protocol is not exported from this module. Hook methods receive the same
    shallow-copied payload dictionary for one wrapper invocation and return
    ``None``. Concrete hook exceptions are not normalized by the wrapper.
    """

    def before_operation(
        self,
        *,
        operation: str,
        target: object,
        payload: dict[str, object],
    ) -> None: ...

    def after_operation(
        self,
        *,
        operation: str,
        target: object,
        payload: dict[str, object],
        result: object,
    ) -> None: ...

    def on_error(
        self,
        *,
        operation: str,
        target: object,
        payload: dict[str, object],
        error: Exception,
    ) -> None: ...


def with_observability(
    target: object,
    *,
    operation: str,
    payload: Mapping[str, object],
    func: Callable[[], ResultT],
) -> ResultT:
    """
    Execute a zero-argument callable with optional observability hooks.

    If ``target`` exposes ``get_capability_handler("observability")`` and that
    lookup returns a capability object, this function reads
    ``before_operation``, ``after_operation``, and ``on_error`` attributes from
    that capability using ``getattr(..., None)``. Absent hook attributes and hook
    attributes set to ``None`` are ignored; non-``None`` values are called as hook
    methods and non-callable values fail when called. The supplied ``payload`` is
    shallow-copied exactly once after capability lookup and hook-attribute lookup,
    before ``before_operation``. The same copy is passed to every hook for that
    invocation. If the target has no handler lookup method, or the lookup returns
    ``None``, ``func`` is executed directly and no payload copy is made.

    Hook order is ``before_operation`` before ``func``, ``on_error`` only if
    ``func`` raises, and ``after_operation`` only after ``func`` succeeds. If
    ``before_operation`` raises, ``func`` is not called. Hook exceptions are not
    wrapped: a failing ``on_error`` replaces the original operation exception,
    and a failing ``after_operation`` replaces the successful operation result.

    Parameters:
        target: Object that may provide a ``get_capability_handler`` method to
            obtain an observability capability.
        operation: Logical operation name passed unchanged to hooks.
        payload: Mapping copied into a plain ``dict`` for hook payloads when a
            capability is present.
        func: Zero-argument callable to execute for the operation.

    Returns:
        The value returned by ``func``.

    Raises:
        Exception: Exceptions from ``get_capability_handler``, hook-attribute
            lookup, ``dict(payload)``, ``func``, and hook calls propagate
            directly. When ``func`` raises and ``on_error`` is present, the
            original exception is re-raised unless ``on_error`` raises a
            replacement exception.
    """
    get_handler = getattr(target, "get_capability_handler", None)
    if get_handler is None:
        return func()
    capability = get_handler("observability")
    if capability is None:
        return func()
    observed = cast(ObservabilityCapability, capability)
    before = getattr(observed, "before_operation", None)
    after = getattr(observed, "after_operation", None)
    on_error = getattr(observed, "on_error", None)
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
