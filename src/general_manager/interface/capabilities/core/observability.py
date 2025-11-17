"""Logging-based observability capability."""

from __future__ import annotations

from typing import Any, Protocol, ClassVar

from general_manager.logging import get_logger

from ..builtin import BaseCapability
from ..base import CapabilityName


class SupportsObservabilityTarget(Protocol):
    """Protocol for objects passed into the observability capability."""

    @property
    def __name__(self) -> str:  # pragma: no cover - protocol definition
        """
        Public name of the target used for observability and logging.

        This read-only property supplies the identifier that LoggingObservabilityCapability records as the target name in log contexts.

        Returns:
            str: The target's name.
        """
        ...


class LoggingObservabilityCapability(BaseCapability):
    """Record lifecycle events for interface operations using the shared logger."""

    name: ClassVar[CapabilityName] = "observability"

    def __init__(self) -> None:
        """
        Initialize the observability capability and configure its logger.

        Sets the instance attribute `self._logger` to a logger named "interface.observability".
        """
        self._logger = get_logger("interface.observability")

    def before_operation(
        self,
        *,
        operation: str,
        target: SupportsObservabilityTarget | object,
        payload: dict[str, Any],
    ) -> None:
        """
        Log the start of an interface operation with contextual metadata.

        Records a debug-level log entry containing the operation name, the target identifier, and the payload's keys.

        Parameters:
            operation (str): The name of the operation being started.
            target (SupportsObservabilityTarget | object): The operation target; its `__name__` is used when available, otherwise the target's class name is used.
            payload (dict[str, Any]): The payload for the operation; only its keys are included in the log context.
        """
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
        """
        Record the end of an interface operation by logging its context including the result's type.

        Parameters:
            operation (str): Name of the operation that completed.
            target (SupportsObservabilityTarget | object): The operation target; its `__name__` is used when available, otherwise the target's class name is used.
            payload (dict[str, Any]): The operation payload; only the payload's keys are included in the logged context.
            result (Any): The operation result whose type name is recorded in the logged context.
        """
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
        """
        Record an operation error event to the observability logger.

        Logs an error-level entry "interface operation error" with a context containing:
        the operation name, the target identifier (uses target.__name__ if present, otherwise the target's class name),
        a sorted list of payload keys, and the `repr` of the provided exception.

        Parameters:
            operation (str): The name of the operation that failed.
            target (SupportsObservabilityTarget | object): The target of the operation; if it exposes `__name__` that value is used for the target identifier, otherwise the target's class name is used.
            payload (dict[str, Any]): The operation payload; only its keys are included in the logged context.
            error (Exception): The exception that occurred; its `repr` is included in the logged context.
        """
        context = self._context(operation, target, payload)
        context["error"] = repr(error)
        self._logger.error("interface operation error", context=context)

    def _context(
        self,
        operation: str,
        target: SupportsObservabilityTarget | object,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Create a structured context dictionary describing an operation invocation.

        Parameters:
            operation (str): The operation name.
            target: The target of the operation; if the object exposes a `__name__` attribute that value is used, otherwise the target's class name is used.
            payload (dict[str, Any]): The operation payload from which keys will be extracted.

        Returns:
            dict[str, Any]: A dictionary with keys:
                - "operation": the provided operation name
                - "target": resolved target name (string)
                - "payload_keys": sorted list of keys from the payload
        """
        if hasattr(target, "__name__"):
            target_name = getattr(target, "__name__", None)
        else:
            target_name = target.__class__.__name__
        return {
            "operation": operation,
            "target": target_name,
            "payload_keys": sorted(payload.keys()),
        }
