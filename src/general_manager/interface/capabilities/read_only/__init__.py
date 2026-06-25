"""Public read-only capability exports.

The package-level surface is intentionally limited to capability classes and
patchable observability hooks used by application setup and tests. Database
transaction helpers stay private to the management implementation.

Exports:
    ReadOnlyLifecycleCapability: Lifecycle capability that configures
        read-only interfaces during manager class creation.
    ReadOnlyManagementCapability: Management capability that validates schema
        state and synchronizes bound read-only data.
    ReadOnlyLogger: Minimal logger protocol used by read-only management.
    ReadOnlyObservabilityHook: Callable protocol for replacing the package-level
        observability wrapper.
    ReadOnlyEnsureSchemaOperation: Literal operation for schema checks.
    ReadOnlySyncDataOperation: Literal operation for data sync.
    ReadOnlyObservabilityOperation: Stable read-only observability operation
        names.
    ReadOnlyEnsureSchemaObservabilityEvent: Paired schema operation and payload.
    ReadOnlySyncObservabilityEvent: Paired sync operation and payload.
    ReadOnlyObservabilityPayload: Union of read-only observability payload
        schemas.
    ReadOnlySchemaObservabilityPayload: Payload schema emitted by schema checks.
    ReadOnlySyncObservabilityPayload: Payload schema emitted by data sync.
    logger: Package-level `ReadOnlyLogger` patch point. Management resolves this
        name at log time, so replacing `read_only.logger` affects subsequent
        schema and synchronization logs.
    with_observability: Observability wrapper patch point used by read-only
        management. Management resolves this callable at runtime through the
        package, so replacing `read_only.with_observability` affects subsequent
        schema-check and sync calls.
"""

from collections.abc import Callable
from typing import Protocol, TypeVar, cast, overload

from general_manager.interface.capabilities.core.utils import (
    with_observability as _with_observability,
)
from . import management as _management
from ._types import (
    ReadOnlyEnsureSchemaObservabilityEvent as ReadOnlyEnsureSchemaObservabilityEvent,
    ReadOnlyEnsureSchemaOperation as ReadOnlyEnsureSchemaOperation,
    ReadOnlyLogger as ReadOnlyLogger,
    ReadOnlyObservabilityOperation as ReadOnlyObservabilityOperation,
    ReadOnlyObservabilityOperationValues as ReadOnlyObservabilityOperationValues,
    ReadOnlyObservabilityPayload as ReadOnlyObservabilityPayload,
    ReadOnlyObservabilityTarget as ReadOnlyObservabilityTarget,
    ReadOnlySchemaObservabilityPayload as ReadOnlySchemaObservabilityPayload,
    ReadOnlySyncDataOperation as ReadOnlySyncDataOperation,
    ReadOnlySyncObservabilityEvent as ReadOnlySyncObservabilityEvent,
    ReadOnlySyncObservabilityPayload as ReadOnlySyncObservabilityPayload,
)
from .lifecycle import ReadOnlyLifecycleCapability as ReadOnlyLifecycleCapability

ResultT = TypeVar("ResultT")


class ReadOnlyObservabilityHook(Protocol):
    """
    Callable shape expected for `read_only.with_observability` patches.

    The default hook creates one shallow copy of `payload` for the event and
    passes that same copy to every callback. It calls `before_operation` before
    `func`; if that callback raises, `func` is not called. It calls `on_error`
    only when `func` raises; if `on_error` raises, that exception replaces the
    original `func` exception. It calls `after_operation` only after `func`
    succeeds; if `after_operation` raises, that exception replaces the
    successful return. Otherwise it returns `func`'s result. Production
    replacement hooks should call `func` exactly once and return its result.
    Test hooks may intentionally skip, repeat, or fail the operation to simulate
    edge cases. Mutating a patched hook's received payload does not affect
    read-only management after the hook call because the payload is used only
    for that observability event. Async functions are not awaited; an awaitable
    returned by `func` is returned as-is.

    Raises:
        Exception: Re-raises exceptions from `func`, observability callbacks, or
            the hook itself; the default hook does not suppress callback
            failures.
    """

    @overload
    def __call__(
        self,
        target: ReadOnlyObservabilityTarget,
        *,
        operation: ReadOnlyEnsureSchemaOperation,
        payload: ReadOnlySchemaObservabilityPayload,
        func: Callable[[], ResultT],
    ) -> ResultT: ...

    @overload
    def __call__(
        self,
        target: ReadOnlyObservabilityTarget,
        *,
        operation: ReadOnlySyncDataOperation,
        payload: ReadOnlySyncObservabilityPayload,
        func: Callable[[], ResultT],
    ) -> ResultT: ...


ReadOnlyManagementCapability = _management.ReadOnlyManagementCapability
logger: ReadOnlyLogger = cast(ReadOnlyLogger, _management.logger)
with_observability = cast(ReadOnlyObservabilityHook, _with_observability)

__all__ = [
    "ReadOnlyEnsureSchemaObservabilityEvent",
    "ReadOnlyEnsureSchemaOperation",
    "ReadOnlyLifecycleCapability",
    "ReadOnlyLogger",
    "ReadOnlyManagementCapability",
    "ReadOnlyObservabilityHook",
    "ReadOnlyObservabilityOperation",
    "ReadOnlyObservabilityOperationValues",
    "ReadOnlyObservabilityPayload",
    "ReadOnlyObservabilityTarget",
    "ReadOnlySchemaObservabilityPayload",
    "ReadOnlySyncDataOperation",
    "ReadOnlySyncObservabilityEvent",
    "ReadOnlySyncObservabilityPayload",
    "logger",
    "with_observability",
]
