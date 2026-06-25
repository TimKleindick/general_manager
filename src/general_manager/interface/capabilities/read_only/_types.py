"""Shared read-only observability type definitions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol, TypedDict

type ReadOnlyEnsureSchemaOperation = Literal["read_only.ensure_schema"]
type ReadOnlySyncDataOperation = Literal["read_only.sync_data"]
type ReadOnlyObservabilityOperation = (
    ReadOnlyEnsureSchemaOperation | ReadOnlySyncDataOperation
)
ReadOnlyObservabilityOperationValues = (
    "read_only.ensure_schema",
    "read_only.sync_data",
)
ReadOnlyObservabilityTarget = type[object]
"""Read-only interface class passed to observability hooks.

The alias stays broad so dynamic interface classes and test doubles can be used
without importing ORM base classes into the package surface.
"""


class ReadOnlySchemaObservabilityPayload(TypedDict):
    """Payload emitted for `read_only.ensure_schema` observability events."""

    manager: str
    model: str


class ReadOnlySyncObservabilityPayload(TypedDict):
    """Payload emitted for `read_only.sync_data` observability events."""

    manager: str | None
    model: str | None
    schema_validated: bool


type ReadOnlyObservabilityPayload = (
    ReadOnlySchemaObservabilityPayload | ReadOnlySyncObservabilityPayload
)
type ReadOnlyEnsureSchemaObservabilityEvent = tuple[
    ReadOnlyEnsureSchemaOperation,
    ReadOnlySchemaObservabilityPayload,
]
type ReadOnlySyncObservabilityEvent = tuple[
    ReadOnlySyncDataOperation,
    ReadOnlySyncObservabilityPayload,
]


class ReadOnlyLogger(Protocol):
    """Minimal logger shape consumed by read-only management patches."""

    def debug(
        self,
        msg: object,
        *args: object,
        context: Mapping[str, object] | None = None,
        **kwargs: object,
    ) -> None:
        """Log diagnostic details with optional structured context."""

    def info(
        self,
        msg: object,
        *args: object,
        context: Mapping[str, object] | None = None,
        **kwargs: object,
    ) -> None:
        """Log sync summaries with optional structured context."""

    def warning(
        self,
        msg: object,
        *args: object,
        context: Mapping[str, object] | None = None,
        **kwargs: object,
    ) -> None:
        """Log schema or sync warnings with optional structured context."""


__all__ = [
    "ReadOnlyEnsureSchemaObservabilityEvent",
    "ReadOnlyEnsureSchemaOperation",
    "ReadOnlyLogger",
    "ReadOnlyObservabilityOperation",
    "ReadOnlyObservabilityOperationValues",
    "ReadOnlyObservabilityPayload",
    "ReadOnlyObservabilityTarget",
    "ReadOnlySchemaObservabilityPayload",
    "ReadOnlySyncDataOperation",
    "ReadOnlySyncObservabilityEvent",
    "ReadOnlySyncObservabilityPayload",
]
