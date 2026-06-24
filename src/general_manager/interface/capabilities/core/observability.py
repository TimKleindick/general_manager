"""Logging-based observability capability."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Protocol

from general_manager.logging import get_logger

from ..builtin import BaseCapability
from ..base import CapabilityName

_MISSING = object()


class SupportsObservabilityTarget(Protocol):
    """Structural target name hook used by logging observability."""

    @property
    def __name__(self) -> str:  # pragma: no cover - protocol definition
        """
        Public name of the target used for observability and logging.

        ``LoggingObservabilityCapability`` uses this string as the ``target``
        context value. Objects without a string ``__name__`` fall back to their
        class name. An ``AttributeError`` raised while reading ``__name__`` is
        treated as a missing name; other lookup errors propagate.

        Returns:
            The target name.
        """
        ...


class LoggingObservabilityCapability(BaseCapability):
    """
    Record interface operation lifecycle events as the ``observability`` capability.

    The capability has no constructor options. It writes structured log calls to
    ``get_logger("interface.observability")`` and lets logger, payload, target,
    and metadata access errors propagate unchanged.
    """

    name: ClassVar[CapabilityName] = "observability"

    def __init__(self) -> None:
        """
        Initialize the observability capability and configure its logger.

        Sets ``self._logger`` to ``get_logger("interface.observability")``.

        Raises:
            Exception: Logger construction errors propagate unchanged.
        """
        self._logger = get_logger("interface.observability")

    def before_operation(
        self,
        *,
        operation: str,
        target: SupportsObservabilityTarget | object,
        payload: dict[str, object],
    ) -> None:
        """
        Log the start of an interface operation with contextual metadata.

        Calls ``self._logger.debug("interface operation start", context=...)``.
        The context contains ``operation``, a string ``target`` name,
        ``payload_keys`` sorted from the payload keys, and selected payload
        metadata values: ``service``, ``method``, ``path``, ``status_code``,
        ``retry_count``, and ``request_id``. Selected metadata keys are included
        whenever they are present in the payload, including when their value is
        ``None``.

        Parameters:
            operation: Operation name recorded unchanged.
            target: Operation target. A string ``target.__name__`` is used when
                present; otherwise ``target.__class__.__name__`` is used.
            payload: Operation payload. Values are copied only for the selected
                metadata keys listed above.

        Raises:
            Exception: Target-name lookup, payload-key sorting, payload metadata
                lookup, and logger errors propagate unchanged.
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
        payload: dict[str, object],
        result: object,
    ) -> None:
        """
        Record the end of an interface operation.

        Calls ``self._logger.debug("interface operation end", context=...)``.
        The context starts with the same fields as ``before_operation()`` and
        adds ``result_type`` as ``type(result).__name__``. If
        ``result.metadata`` is a mapping, ``status_code``, ``retry_count``, and
        ``request_id`` from that ``collections.abc.Mapping`` replace same-named
        payload metadata values in the end-event context. Result metadata keys
        are included whenever they are present in the mapping, including when
        their value is ``None``. A missing ``metadata`` attribute, an
        ``AttributeError`` raised while reading ``metadata``, or a non-mapping
        metadata value is ignored.

        Parameters:
            operation: Operation name recorded unchanged.
            target: Operation target. A string ``target.__name__`` is used when
                present; otherwise ``target.__class__.__name__`` is used.
            payload: Operation payload used to build the base context.
            result: Operation result whose type name and optional mapping
                metadata are recorded.

        Raises:
            Exception: Target-name lookup, payload-key sorting, payload metadata
                lookup, non-``AttributeError`` result metadata lookup failures,
                and logger errors propagate unchanged.
        """
        context = self._context(operation, target, payload)
        result_metadata = getattr(result, "metadata", None)
        if isinstance(result_metadata, Mapping):
            if "status_code" in result_metadata:
                context["status_code"] = result_metadata["status_code"]
            if "retry_count" in result_metadata:
                context["retry_count"] = result_metadata["retry_count"]
            if "request_id" in result_metadata:
                context["request_id"] = result_metadata["request_id"]
        context["result_type"] = type(result).__name__
        self._logger.debug("interface operation end", context=context)

    def on_error(
        self,
        *,
        operation: str,
        target: SupportsObservabilityTarget | object,
        payload: dict[str, object],
        error: Exception,
    ) -> None:
        """
        Record an operation error event to the observability logger.

        Calls ``self._logger.error("interface operation error", context=...)``.
        The context starts with the same fields as ``before_operation()`` and
        adds ``error`` as ``repr(error)``, ``error_class`` as
        ``type(error).__name__``, and ``status_code`` from ``error.status_code``
        when that attribute exists, including when the value is ``None``. A
        missing ``status_code`` attribute is ignored.

        Parameters:
            operation: Operation name recorded unchanged.
            target: Operation target. A string ``target.__name__`` is used when
                present; otherwise ``target.__class__.__name__`` is used.
                ``AttributeError`` from ``__name__`` lookup is treated as
                missing.
            payload: Operation payload used to build the base context.
            error: Exception whose representation, class name, and optional
                status code are recorded.

        Raises:
            Exception: Target-name lookup, payload-key sorting, payload metadata
                lookup, non-``AttributeError`` error status lookup failures, and
                logger errors propagate unchanged.
        """
        context = self._context(operation, target, payload)
        context["error"] = repr(error)
        context["error_class"] = type(error).__name__
        status_code = getattr(error, "status_code", _MISSING)
        if status_code is not _MISSING:
            context["status_code"] = status_code
        self._logger.error("interface operation error", context=context)

    def _context(
        self,
        operation: str,
        target: SupportsObservabilityTarget | object,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """
        Create the structured log context shared by all observability events.

        Parameters:
            operation: Operation name recorded unchanged.
            target: Operation target. A string ``target.__name__`` is used when
                present; otherwise ``target.__class__.__name__`` is used.
            payload: Operation payload from which keys and selected metadata are
                extracted.

        Returns:
            A new dictionary containing ``operation``, ``target``,
            ``payload_keys``, and selected payload metadata keys whenever those
            keys are present in the payload, including when their values are
            ``None``.

        Raises:
            Exception: Non-``AttributeError`` target-name lookup failures and
                payload key/value access errors propagate unchanged.
        """
        target_name = getattr(target, "__name__", _MISSING)
        if not isinstance(target_name, str):
            target_name = target.__class__.__name__
        return {
            "operation": operation,
            "target": target_name,
            "payload_keys": sorted(payload.keys()),
        } | {
            key: payload[key]
            for key in (
                "service",
                "method",
                "path",
                "status_code",
                "retry_count",
                "request_id",
            )
            if key in payload
        }


__all__ = ["LoggingObservabilityCapability", "SupportsObservabilityTarget"]
