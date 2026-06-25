"""Compatibility helper for calculation capability observability patches."""

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
    Delegate invocation to the package-level `with_observability` function.

    This resolves the helper through the package on each call (tests patch
    `general_manager.interface.capabilities.calculation.with_observability` directly).

    Parameters:
        target: Interface class or instance passed to the observability hook.
        operation: Stable operation label for hook consumers.
        payload: Metadata snapshot describing the operation. It is copied into
            a plain dictionary before delegation.
        func: Zero-argument callback that performs the operation.

    Returns:
        The value returned by ``func`` through the package-level hook.

    Raises:
        Exception: Re-raises any exception raised by ``func`` or by the
            package-level observability hook.
    """
    from general_manager.interface.capabilities import (
        calculation as calculation_package,
    )

    return calculation_package.with_observability(
        target,
        operation=operation,
        payload=dict(payload),
        func=func,
    )
