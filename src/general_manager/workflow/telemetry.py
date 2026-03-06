"""Workflow runtime telemetry helpers with optional Prometheus support."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:  # pragma: no cover - optional dependency boundary
    from prometheus_client import Counter, Gauge, Histogram  # type: ignore[import-not-found]

    _METRICS_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency boundary
    _METRICS_AVAILABLE = False
    Counter = Gauge = Histogram = object  # type: ignore[assignment,misc]


if _METRICS_AVAILABLE:
    _outbox_pending_count = Gauge(
        "workflow_outbox_pending_count",
        "Current pending workflow outbox rows.",
    )
    _outbox_oldest_pending_age_seconds = Gauge(
        "workflow_outbox_oldest_pending_age_seconds",
        "Age in seconds of the oldest pending outbox row.",
    )
    _outbox_process_duration_seconds = Histogram(
        "workflow_outbox_process_duration_seconds",
        "Outbox row processing duration.",
        ["status"],
    )
    _outbox_claim_batch_size = Histogram(
        "workflow_outbox_claim_batch_size",
        "Number of outbox rows claimed per batch.",
    )
    _outbox_status_total = Counter(
        "workflow_outbox_status_total",
        "Workflow outbox status transitions.",
        ["status"],
    )
    _delivery_attempt_total = Counter(
        "workflow_delivery_attempt_total",
        "Workflow delivery attempt outcomes.",
        ["status"],
    )
    _execution_state_total = Counter(
        "workflow_execution_state_total",
        "Workflow execution states written.",
        ["state"],
    )
    _duplicate_suppression_total = Counter(
        "workflow_duplicate_suppression_total",
        "Duplicate workflow delivery attempts suppressed.",
    )


def _safe_label(label_value: str) -> str:
    return label_value.replace(" ", "_").replace(":", "_")


def set_outbox_snapshot(
    *, pending_count: int, oldest_pending_age_seconds: float
) -> None:
    if not _METRICS_AVAILABLE:
        return
    _outbox_pending_count.set(max(0, pending_count))
    _outbox_oldest_pending_age_seconds.set(max(0.0, oldest_pending_age_seconds))


def observe_outbox_claim_batch(size: int) -> None:
    if not _METRICS_AVAILABLE:
        return
    _outbox_claim_batch_size.observe(max(0, size))


def observe_outbox_process_duration(*, status: str, duration_seconds: float) -> None:
    if not _METRICS_AVAILABLE:
        return
    _outbox_process_duration_seconds.labels(_safe_label(status)).observe(
        max(0.0, duration_seconds)
    )


def increment_outbox_status(status: str) -> None:
    if not _METRICS_AVAILABLE:
        return
    _outbox_status_total.labels(_safe_label(status)).inc()


def increment_delivery_attempt(*, status: str) -> None:
    if not _METRICS_AVAILABLE:
        return
    _delivery_attempt_total.labels(_safe_label(status)).inc()


def increment_execution_state(state: str) -> None:
    if not _METRICS_AVAILABLE:
        return
    _execution_state_total.labels(_safe_label(state)).inc()


def increment_duplicate_suppression() -> None:
    if not _METRICS_AVAILABLE:
        return
    _duplicate_suppression_total.inc()


def extract_outbox_snapshot_payload(snapshot: Mapping[str, Any]) -> tuple[int, float]:
    pending = int(snapshot.get("pending_count") or 0)
    oldest_age = float(snapshot.get("oldest_pending_age_seconds") or 0.0)
    return pending, oldest_age
