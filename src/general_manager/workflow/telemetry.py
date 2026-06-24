"""
Workflow runtime telemetry helpers with optional Prometheus support.

Collectors are created when this module is imported and `prometheus_client` is
available. Normal Python import caching prevents repeated app initialization
from registering the collectors more than once in a process; explicit module
reloads follow the Prometheus client's duplicate-registration behavior. The
helpers add no extra locking and rely on the installed metrics backend for
thread/process semantics.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, SupportsFloat, SupportsInt, cast


class _PrometheusMetric(Protocol):
    """Subset of the Prometheus client metric API used by workflow telemetry."""

    def set(self, value: float) -> None: ...

    def observe(self, value: float) -> None: ...

    def inc(self) -> None: ...

    def labels(self, *label_values: str) -> _PrometheusMetric: ...


class _MetricFactory(Protocol):
    """Callable shape shared by Counter, Gauge, and Histogram factories."""

    def __call__(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
    ) -> _PrometheusMetric: ...


try:  # pragma: no cover - optional dependency boundary
    from prometheus_client import (
        Counter as _PrometheusCounter,
        Gauge as _PrometheusGauge,
        Histogram as _PrometheusHistogram,
    )
except ImportError:  # pragma: no cover - optional dependency boundary
    _Counter = _Gauge = _Histogram = None
else:  # pragma: no cover - import shape is covered by typed tests
    _Counter = cast("_MetricFactory", _PrometheusCounter)
    _Gauge = cast("_MetricFactory", _PrometheusGauge)
    _Histogram = cast("_MetricFactory", _PrometheusHistogram)


_METRICS_AVAILABLE = (
    _Counter is not None and _Gauge is not None and _Histogram is not None
)
_PENDING_COUNT_TYPE_ERROR = "pending_count must be coercible to int"
_OLDEST_AGE_TYPE_ERROR = "oldest_pending_age_seconds must be coercible to float"
_LABEL_TYPE_ERROR = "workflow telemetry labels must be str"

_outbox_pending_count: _PrometheusMetric | None = None
_outbox_oldest_pending_age_seconds: _PrometheusMetric | None = None
_outbox_process_duration_seconds: _PrometheusMetric | None = None
_outbox_claim_batch_size: _PrometheusMetric | None = None
_outbox_status_total: _PrometheusMetric | None = None
_delivery_attempt_total: _PrometheusMetric | None = None
_execution_state_total: _PrometheusMetric | None = None
_duplicate_suppression_total: _PrometheusMetric | None = None

if _METRICS_AVAILABLE:
    assert _Counter is not None
    assert _Gauge is not None
    assert _Histogram is not None
    _outbox_pending_count = _Gauge(
        "workflow_outbox_pending_count",
        "Current pending workflow outbox rows.",
    )
    _outbox_oldest_pending_age_seconds = _Gauge(
        "workflow_outbox_oldest_pending_age_seconds",
        "Age in seconds of the oldest pending outbox row.",
    )
    _outbox_process_duration_seconds = _Histogram(
        "workflow_outbox_process_duration_seconds",
        "Outbox row processing duration.",
        ["status"],
    )
    _outbox_claim_batch_size = _Histogram(
        "workflow_outbox_claim_batch_size",
        "Number of outbox rows claimed per batch.",
    )
    _outbox_status_total = _Counter(
        "workflow_outbox_status_total",
        "Workflow outbox status transitions.",
        ["status"],
    )
    _delivery_attempt_total = _Counter(
        "workflow_delivery_attempt_total",
        "Workflow delivery attempt outcomes.",
        ["status"],
    )
    _execution_state_total = _Counter(
        "workflow_execution_state_total",
        "Workflow execution states written.",
        ["state"],
    )
    _duplicate_suppression_total = _Counter(
        "workflow_duplicate_suppression_total",
        "Duplicate workflow delivery attempts suppressed.",
    )


def _safe_label(label_value: str) -> str:
    """
    Normalize workflow status/state strings for Prometheus labels.

    Raises:
        TypeError: If `label_value` is not a string at runtime.
    """
    if not isinstance(label_value, str):
        raise TypeError(_LABEL_TYPE_ERROR)
    return label_value.replace(" ", "_").replace(":", "_")


def set_outbox_snapshot(
    *, pending_count: int, oldest_pending_age_seconds: float
) -> None:
    """
    Set the point-in-time workflow outbox backlog gauges.

    Parameters:
        pending_count: Current number of pending outbox rows.
        oldest_pending_age_seconds: Age in seconds of the oldest pending row.

    Negative values are clamped to `0`/`0.0` before recording. The helper is a
    no-op when `prometheus_client` is unavailable. It does not catch exceptions
    raised by the installed metrics backend.
    """
    if (
        not _METRICS_AVAILABLE
        or _outbox_pending_count is None
        or _outbox_oldest_pending_age_seconds is None
    ):
        return
    _outbox_pending_count.set(max(0, pending_count))
    _outbox_oldest_pending_age_seconds.set(max(0.0, oldest_pending_age_seconds))


def observe_outbox_claim_batch(size: int) -> None:
    """
    Observe the number of workflow outbox rows claimed in one batch.

    Parameters:
        size: Claimed row count. Negative values are recorded as `0`.

    The helper is a no-op when `prometheus_client` is unavailable and does not
    catch exceptions raised by the installed metrics backend.

    Raises:
        TypeError: If `status` is not a string at runtime and metrics are
            enabled.
    """
    if not _METRICS_AVAILABLE or _outbox_claim_batch_size is None:
        return
    _outbox_claim_batch_size.observe(max(0, size))


def observe_outbox_process_duration(*, status: str, duration_seconds: float) -> None:
    """
    Observe workflow outbox row processing latency for a terminal status.

    Parameters:
        status: Outbox status label. Spaces and colons are replaced with `_`
            before recording. The helper does not validate a fixed vocabulary.
        duration_seconds: Processing duration in seconds. Negative values are
            recorded as `0.0`.

    The helper is a no-op when `prometheus_client` is unavailable and does not
    catch exceptions raised by the installed metrics backend.

    Raises:
        TypeError: If `status` is not a string at runtime and metrics are
            enabled.
    """
    if not _METRICS_AVAILABLE or _outbox_process_duration_seconds is None:
        return
    _outbox_process_duration_seconds.labels(_safe_label(status)).observe(
        max(0.0, duration_seconds)
    )


def increment_outbox_status(status: str) -> None:
    """
    Increment the workflow outbox status-transition counter.

    Parameters:
        status: Outbox status label. Spaces and colons are replaced with `_`
            before recording. The helper does not validate a fixed vocabulary.

    The helper is a no-op when `prometheus_client` is unavailable and does not
    catch exceptions raised by the installed metrics backend.

    Raises:
        TypeError: If `status` is not a string at runtime and metrics are
            enabled.
    """
    if not _METRICS_AVAILABLE or _outbox_status_total is None:
        return
    _outbox_status_total.labels(_safe_label(status)).inc()


def increment_delivery_attempt(*, status: str) -> None:
    """
    Increment the workflow delivery-attempt status counter.

    Parameters:
        status: Delivery-attempt status label. Spaces and colons are replaced
            with `_` before recording. The helper does not validate a fixed
            vocabulary.

    The helper is a no-op when `prometheus_client` is unavailable and does not
    catch exceptions raised by the installed metrics backend.

    Raises:
        TypeError: If `state` is not a string at runtime and metrics are
            enabled.
    """
    if not _METRICS_AVAILABLE or _delivery_attempt_total is None:
        return
    _delivery_attempt_total.labels(_safe_label(status)).inc()


def increment_execution_state(state: str) -> None:
    """
    Increment the workflow execution state counter.

    Parameters:
        state: Execution state label. Spaces and colons are replaced with `_`
            before recording. The helper does not validate a fixed vocabulary.

    The helper is a no-op when `prometheus_client` is unavailable and does not
    catch exceptions raised by the installed metrics backend.
    """
    if not _METRICS_AVAILABLE or _execution_state_total is None:
        return
    _execution_state_total.labels(_safe_label(state)).inc()


def increment_duplicate_suppression() -> None:
    """
    Increment the suppressed duplicate workflow delivery-attempt counter.

    The helper is a no-op when `prometheus_client` is unavailable and does not
    catch exceptions raised by the installed metrics backend.
    """
    if not _METRICS_AVAILABLE or _duplicate_suppression_total is None:
        return
    _duplicate_suppression_total.inc()


def _coerce_snapshot_int(value: object) -> int:
    if not value:
        return 0
    if isinstance(value, str | bytes | bytearray | int | float | SupportsInt):
        return int(value)
    raise TypeError(_PENDING_COUNT_TYPE_ERROR)


def _coerce_snapshot_float(value: object) -> float:
    if not value:
        return 0.0
    if isinstance(value, str | bytes | bytearray | int | float | SupportsFloat):
        return float(value)
    raise TypeError(_OLDEST_AGE_TYPE_ERROR)


def extract_outbox_snapshot_payload(
    snapshot: Mapping[str, object],
) -> tuple[int, float]:
    """
    Extract typed outbox snapshot values from a serialized mapping.

    Parameters:
        snapshot: Mapping that may contain `pending_count` and
            `oldest_pending_age_seconds`.

    Returns:
        tuple[int, float]: Pending count and oldest pending age. Missing or
        falsey values are returned as `(0, 0.0)`. The falsey check uses normal
        Python truth-value testing, so `None`, zero values, `False`, empty
        strings/bytes, and empty containers are treated as absent; exceptions
        from custom truth-value methods propagate. Parsed negative values are
        returned unchanged; recording helpers clamp when they emit gauges or
        observations.

    Coercion:
        `pending_count` is parsed with `int(...)`, so floats are truncated,
        `True` becomes `1`, `"1.2"` raises `ValueError`, and non-finite floats
        raise the exception that `int(...)` raises. `oldest_pending_age_seconds`
        is parsed with `float(...)`, so `"nan"`/`"inf"` and non-finite floats
        are accepted by Python's normal float conversion rules.

    Raises:
        TypeError: If a present truthy value is not coercible to the expected
            numeric type, including errors raised by the underlying conversion.
        ValueError: Propagated from `int(...)` or `float(...)` for values that
            have the right broad shape but invalid numeric content.
        OverflowError: Propagated from `int(...)` for non-finite float values.
    """
    pending = _coerce_snapshot_int(snapshot.get("pending_count"))
    oldest_age = _coerce_snapshot_float(snapshot.get("oldest_pending_age_seconds"))
    return pending, oldest_age
