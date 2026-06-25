"""Compatibility helper for existing model capability observability patches."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeVar


ResultT = TypeVar("ResultT")


def call_with_observability(
    target: object,
    *,
    operation: str,
    payload: Mapping[str, object],
    func: Callable[[], ResultT],
) -> ResultT:
    """
    Delegate invocation to the package-level `with_observability`, resolving the target at call time so runtime patches are honored.

    Parameters:
        target: Interface class or model class passed to the observability hook.
        operation: Stable operation label for hook consumers.
        payload: Metadata snapshot describing the operation.
        func: Zero-argument callback that performs the operation.

    Returns:
        The value returned by `func` through the package-level hook.
    """
    from general_manager.interface.capabilities import existing_model as package

    return package.with_observability(
        target,
        operation=operation,
        payload=dict(payload),
        func=func,
    )
