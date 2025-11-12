"""Logging-based observability capability."""

from __future__ import annotations

from typing import Any, Protocol, ClassVar

from general_manager.logging import get_logger

from .builtin import BaseCapability
from .base import CapabilityName


class SupportsObservabilityTarget(Protocol):
    """Protocol for objects passed into the observability capability."""

    @property
    def __name__(self) -> str:  # pragma: no cover - protocol definition
        ...


class LoggingObservabilityCapability(BaseCapability):
    """Record lifecycle events for interface operations using the shared logger."""

    name: ClassVar[CapabilityName] = "observability"

    def __init__(self) -> None:
        self._logger = get_logger("interface.observability")

    def before_operation(
        self,
        *,
        operation: str,
        target: SupportsObservabilityTarget | object,
        payload: dict[str, Any],
    ) -> None:
        self._logger.debug(
            "interface operation start",
            context=self._context(operation, target, payload),
        )

    def after_operation(
        self,
        *,
        operation: str,
        target: SupportsObservabilityTarget | object,
        payload: dict[str, Any],
        result: Any,
    ) -> None:
        context = self._context(operation, target, payload)
        context["result_type"] = type(result).__name__
        self._logger.debug("interface operation end", context=context)

    def on_error(
        self,
        *,
        operation: str,
        target: SupportsObservabilityTarget | object,
        payload: dict[str, Any],
        error: Exception,
    ) -> None:
        context = self._context(operation, target, payload)
        context["error"] = repr(error)
        self._logger.error("interface operation error", context=context)

    def _context(
        self,
        operation: str,
        target: SupportsObservabilityTarget | object,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if hasattr(target, "__name__"):
            target_name = getattr(target, "__name__", None)
        else:
            target_name = target.__class__.__name__
        return {
            "operation": operation,
            "target": target_name,
            "payload_keys": sorted(payload.keys()),
        }
