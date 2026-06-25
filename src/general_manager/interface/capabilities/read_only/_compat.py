"""Compatibility helper for read-only capability observability patches."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar, cast, overload

from ._types import (
    ReadOnlyEnsureSchemaOperation,
    ReadOnlyObservabilityOperation,
    ReadOnlyObservabilityPayload,
    ReadOnlySchemaObservabilityPayload,
    ReadOnlySyncDataOperation,
    ReadOnlySyncObservabilityPayload,
)

ResultT = TypeVar("ResultT")


@overload
def call_with_observability(
    target: type[object],
    *,
    operation: ReadOnlyEnsureSchemaOperation,
    payload: ReadOnlySchemaObservabilityPayload,
    func: Callable[[], ResultT],
) -> ResultT: ...


@overload
def call_with_observability(
    target: type[object],
    *,
    operation: ReadOnlySyncDataOperation,
    payload: ReadOnlySyncObservabilityPayload,
    func: Callable[[], ResultT],
) -> ResultT: ...


def call_with_observability(
    target: type[object],
    *,
    operation: ReadOnlyObservabilityOperation,
    payload: ReadOnlyObservabilityPayload,
    func: Callable[[], ResultT],
) -> ResultT:
    """
    Run `func` through the package-level observability wrapper.

    This private compatibility shim imports the wrapper from
    `general_manager.interface.capabilities.read_only` at call time so tests and
    advanced instrumentation can patch `read_only.with_observability` before
    invoking read-only schema checks or data sync.

    Parameters:
        target: Interface class passed to the observability wrapper.
        operation: Stable operation name, such as `read_only.sync_data`.
        payload: Metadata payload forwarded to the package-level wrapper. A
            patched wrapper receives this dictionary directly; the default
            wrapper copies it before invoking observability callbacks.
        func: Synchronous zero-argument callable that performs the wrapped
            operation. The default wrapper calls it exactly once.

    Returns:
        The value returned by `func` through the package-level wrapper.

    Raises:
        Exception: Re-raises any exception raised by `func`, by default
            observability callbacks, or by a patched observability wrapper.
    """
    from general_manager.interface.capabilities import read_only as read_only_package

    if operation == "read_only.ensure_schema":
        return read_only_package.with_observability(
            target,
            operation=operation,
            payload=cast(ReadOnlySchemaObservabilityPayload, payload),
            func=func,
        )
    return read_only_package.with_observability(
        target,
        operation=operation,
        payload=cast(ReadOnlySyncObservabilityPayload, payload),
        func=func,
    )
