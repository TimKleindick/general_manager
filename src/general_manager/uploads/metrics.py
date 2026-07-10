"""Low-cardinality, failure-isolated observability for file uploads."""

from __future__ import annotations

import logging
import re
from threading import RLock
from typing import Protocol
from uuid import UUID


class UploadMetricsBackend(Protocol):
    """Minimal backend contract used by upload instrumentation."""

    def increment(self, metric: str, value: int, labels: dict[str, str]) -> None: ...

    def observe(self, metric: str, value: float, labels: dict[str, str]) -> None: ...


class _NoopUploadMetricsBackend:
    def increment(self, metric: str, value: int, labels: dict[str, str]) -> None:
        del metric, value, labels

    def observe(self, metric: str, value: float, labels: dict[str, str]) -> None:
        del metric, value, labels


_LOCK = RLock()
_backend: UploadMetricsBackend = _NoopUploadMetricsBackend()
_logger = logging.getLogger("general_manager.uploads")
_ADAPTER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,254}$")
_STATES = frozenset(
    {
        "pending",
        "transferring",
        "uploaded",
        "finalizing",
        "consumed",
        "superseded",
        "rejected",
        "expired",
    }
)
_TRANSPORTS = frozenset({"proxy", "direct"})
_OPERATIONS = frozenset(
    {
        "begin",
        "transfer",
        "validation",
        "finalization",
        "cleanup",
        "expired",
        "rejected",
        "consumed",
        "superseded",
    }
)
_RESULTS = frozenset({"completed", "failed", "skipped", "retried", "dry_run"})
_ERRORS = frozenset(
    {
        "authentication_error",
        "backend_unsupported",
        "binding_mismatch",
        "checksum_mismatch",
        "expired",
        "file_type",
        "finalization_failed",
        "image_invalid",
        "incomplete",
        "quota_exceeded",
        "rate_limited",
        "size_mismatch",
        "storage_changed",
        "storage_error",
        "transfer_conflict",
        "unknown",
    }
)
_BACKEND_TYPE_ERROR = "Upload metrics backends must implement increment and observe."
_ERROR_CODE_LABELS = {
    "INVALID_FILE_TYPE": "file_type",
    "INVALID_IMAGE": "image_invalid",
    "UPLOAD_AUTHENTICATION_REQUIRED": "authentication_error",
    "UNAUTHENTICATED": "authentication_error",
    "UPLOAD_BACKEND_UNSUPPORTED": "backend_unsupported",
    "UPLOAD_BINDING_MISMATCH": "binding_mismatch",
    "UPLOAD_CHECKSUM_MISMATCH": "checksum_mismatch",
    "UPLOAD_EXPIRED": "expired",
    "UPLOAD_FINALIZATION_FAILED": "finalization_failed",
    "UPLOAD_INCOMPLETE": "incomplete",
    "UPLOAD_QUOTA_EXCEEDED": "quota_exceeded",
    "UPLOAD_RATE_LIMITED": "rate_limited",
    "UPLOAD_SIZE_MISMATCH": "size_mismatch",
    "UPLOAD_STORAGE_CHANGED": "storage_changed",
    "UPLOAD_STORAGE_ERROR": "storage_error",
    "UPLOAD_TRANSFER_CONFLICT": "transfer_conflict",
}


def set_upload_metrics_backend(backend: UploadMetricsBackend) -> UploadMetricsBackend:
    """Install ``backend`` and return the prior backend for deterministic restore."""

    if not callable(getattr(backend, "increment", None)) or not callable(
        getattr(backend, "observe", None)
    ):
        raise TypeError(_BACKEND_TYPE_ERROR)
    global _backend
    with _LOCK:
        previous = _backend
        _backend = backend
    return previous


def restore_upload_metrics_backend(backend: UploadMetricsBackend) -> None:
    """Restore a backend previously returned by :func:`set_upload_metrics_backend`."""

    global _backend
    with _LOCK:
        _backend = backend


def _current_backend() -> UploadMetricsBackend:
    with _LOCK:
        return _backend


def _increment(metric: str, labels: dict[str, str]) -> None:
    try:
        _current_backend().increment(metric, 1, labels)
    except Exception:  # noqa: BLE001 - observability cannot affect behavior
        return


def _observe(metric: str, value: float, labels: dict[str, str]) -> None:
    try:
        _current_backend().observe(metric, value, labels)
    except Exception:  # noqa: BLE001 - observability cannot affect behavior
        return


def _valid_adapter(value: object) -> str | None:
    return value if isinstance(value, str) and _ADAPTER.fullmatch(value) else None


def record_upload_transition(
    intent: object, state: object, *, from_state: object | None = None
) -> None:
    """Record and safely log one durable intent state transition."""

    adapter = _valid_adapter(_safe_attribute(intent, "adapter_id"))
    if adapter is None or state not in _STATES:
        return
    _increment("upload_transition_total", {"adapter": adapter, "state": str(state)})
    context = _safe_intent_context(intent=intent, adapter=adapter)
    context.update({"event": "upload_transition", "to_state": state, "state": state})
    if from_state in _STATES:
        context["from_state"] = str(from_state)
    try:
        _logger.info("upload state transition", extra={"upload": context})
    except Exception:  # noqa: BLE001 - hostile logging handlers are isolated
        return


def record_upload_bytes(
    *,
    adapter: object,
    transport: object,
    byte_count: object,
    intent: object | None = None,
    correlation_id: object | None = None,
) -> None:
    """Observe transferred bytes as a value, never as a metric label."""

    safe_adapter = _valid_adapter(adapter)
    if (
        safe_adapter is None
        or transport not in _TRANSPORTS
        or isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or byte_count < 0
    ):
        return
    _observe(
        "upload_bytes",
        float(byte_count),
        {"adapter": safe_adapter, "transport": str(transport)},
    )
    _log_event(
        "upload transfer",
        {
            **_safe_intent_context(
                intent=intent,
                correlation_id=correlation_id,
                adapter=safe_adapter,
            ),
            "event": "upload_transfer",
            "transport": str(transport),
            "bytes": byte_count,
        },
    )


def record_upload_failure(
    *,
    adapter: object,
    operation: object,
    error: object,
    intent: object | None = None,
    correlation_id: object | None = None,
) -> None:
    """Count a sanitized upload failure from fixed-cardinality labels."""

    safe_adapter = _valid_adapter(adapter)
    if safe_adapter is None or operation not in _OPERATIONS or error not in _ERRORS:
        return
    _increment(
        "upload_failure_total",
        {"adapter": safe_adapter, "operation": str(operation), "error": str(error)},
    )
    _log_failure(
        adapter=safe_adapter,
        operation=str(operation),
        error=str(error),
        intent=intent,
        correlation_id=correlation_id,
    )


def record_upload_cleanup(
    *, operation: object, result: object, intent: object | None = None
) -> None:
    """Count one bounded cleanup result."""

    if operation not in _OPERATIONS or result not in _RESULTS:
        return
    _increment(
        "upload_cleanup_total",
        {"operation": str(operation), "result": str(result)},
    )
    if intent is not None:
        _log_operation(intent, operation=str(operation), result=str(result))


def observe_upload_duration(
    *,
    adapter: object,
    operation: object,
    result: object,
    seconds: object,
    intent: object | None = None,
    correlation_id: object | None = None,
) -> None:
    """Observe a duration with fixed labels and a non-negative value."""

    safe_adapter = _valid_adapter(adapter)
    if (
        safe_adapter is None
        or operation not in _OPERATIONS
        or result not in _RESULTS
        or isinstance(seconds, bool)
        or not isinstance(seconds, (int, float))
        or seconds < 0
    ):
        return
    _observe(
        "upload_duration_seconds",
        float(seconds),
        {
            "adapter": safe_adapter,
            "operation": str(operation),
            "result": str(result),
        },
    )
    _log_event(
        "upload duration",
        {
            **_safe_intent_context(
                intent=intent,
                correlation_id=correlation_id,
                adapter=safe_adapter,
            ),
            "event": "upload_duration",
            "operation": str(operation),
            "result": str(result),
            "duration_seconds": float(seconds),
        },
    )


def upload_error_label(code: object) -> str:
    """Map one stable public error code to a bounded metric label."""

    return (
        _ERROR_CODE_LABELS.get(code, "unknown") if isinstance(code, str) else "unknown"
    )


def _log_operation(intent: object, *, operation: str, result: str) -> None:
    adapter = _valid_adapter(_safe_attribute(intent, "adapter_id"))
    if adapter is None:
        return
    context = _safe_intent_context(intent=intent, adapter=adapter)
    context.update(
        {
            "event": "upload_operation",
            "operation": operation,
            "result": result,
        }
    )
    _log_event("upload operation", context)


def _log_failure(
    *,
    adapter: str,
    operation: str,
    error: str,
    intent: object | None,
    correlation_id: object | None,
) -> None:
    context = _safe_intent_context(
        intent=intent,
        correlation_id=correlation_id,
        adapter=adapter,
    )
    context.update(
        {
            "event": "upload_failure",
            "operation": operation,
            "error": error,
        }
    )
    try:
        _logger.warning("upload failure", extra={"upload": context})
    except Exception:  # noqa: BLE001 - logging cannot affect behavior
        return


def _safe_correlation_id(value: object) -> str | None:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        try:
            return str(UUID(value))
        except ValueError:
            return None
    return None


def _safe_attribute(value: object, attribute: str) -> object | None:
    try:
        return getattr(value, attribute, None)
    except Exception:  # noqa: BLE001 - observability must tolerate hostile objects
        return None


def _safe_intent_context(
    *,
    intent: object | None,
    adapter: object,
    correlation_id: object | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {}
    safe_adapter = _valid_adapter(adapter)
    if safe_adapter is not None:
        context["adapter"] = safe_adapter

    intent_id = (
        _safe_correlation_id(_safe_attribute(intent, "id"))
        if intent is not None
        else None
    )
    if intent_id is None:
        intent_id = _safe_correlation_id(correlation_id)
    if intent_id is not None:
        context["intent_id"] = intent_id

    if intent is None:
        return context
    for attribute, key in (("manager_name", "manager"), ("field_name", "field")):
        value = _safe_attribute(intent, attribute)
        if isinstance(value, str) and _SAFE_NAME.fullmatch(value):
            context[key] = value
    state = _safe_attribute(intent, "state")
    if state in _STATES:
        context["state"] = state
    declared_size = _safe_attribute(intent, "declared_size")
    if (
        isinstance(declared_size, int)
        and not isinstance(declared_size, bool)
        and declared_size >= 0
    ):
        context["declared_size"] = declared_size
    size = _safe_attribute(intent, "verified_size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        size = declared_size
    if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
        context["size"] = size
    return context


def _log_event(message: str, context: dict[str, object]) -> None:
    try:
        _logger.info(message, extra={"upload": context})
    except Exception:  # noqa: BLE001 - logging cannot affect behavior
        return
