from __future__ import annotations

import math
from typing import cast

import pytest
from _pytest.monkeypatch import MonkeyPatch

from general_manager.workflow import telemetry


class RecordingMetric:
    def __init__(self) -> None:
        self.set_values: list[float] = []
        self.observations: list[float] = []
        self.inc_count = 0
        self.label_calls: list[tuple[str, ...]] = []

    def set(self, value: float) -> None:
        self.set_values.append(value)

    def observe(self, value: float) -> None:
        self.observations.append(value)

    def inc(self) -> None:
        self.inc_count += 1

    def labels(self, *label_values: str) -> RecordingMetric:
        self.label_calls.append(label_values)
        return self


def _install_metrics(monkeypatch: MonkeyPatch) -> dict[str, RecordingMetric]:
    metrics = {
        "pending": RecordingMetric(),
        "oldest_age": RecordingMetric(),
        "process_duration": RecordingMetric(),
        "claim_batch": RecordingMetric(),
        "outbox_status": RecordingMetric(),
        "delivery_attempt": RecordingMetric(),
        "execution_state": RecordingMetric(),
        "duplicate_suppression": RecordingMetric(),
    }
    monkeypatch.setattr(telemetry, "_METRICS_AVAILABLE", True)
    monkeypatch.setattr(telemetry, "_outbox_pending_count", metrics["pending"])
    monkeypatch.setattr(
        telemetry,
        "_outbox_oldest_pending_age_seconds",
        metrics["oldest_age"],
    )
    monkeypatch.setattr(
        telemetry,
        "_outbox_process_duration_seconds",
        metrics["process_duration"],
    )
    monkeypatch.setattr(telemetry, "_outbox_claim_batch_size", metrics["claim_batch"])
    monkeypatch.setattr(telemetry, "_outbox_status_total", metrics["outbox_status"])
    monkeypatch.setattr(
        telemetry,
        "_delivery_attempt_total",
        metrics["delivery_attempt"],
    )
    monkeypatch.setattr(
        telemetry,
        "_execution_state_total",
        metrics["execution_state"],
    )
    monkeypatch.setattr(
        telemetry,
        "_duplicate_suppression_total",
        metrics["duplicate_suppression"],
    )
    return metrics


def test_telemetry_helpers_noop_when_prometheus_is_unavailable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(telemetry, "_METRICS_AVAILABLE", False)
    monkeypatch.setattr(telemetry, "_outbox_pending_count", None)
    monkeypatch.setattr(telemetry, "_outbox_oldest_pending_age_seconds", None)
    monkeypatch.setattr(telemetry, "_outbox_claim_batch_size", None)
    monkeypatch.setattr(telemetry, "_outbox_process_duration_seconds", None)
    monkeypatch.setattr(telemetry, "_outbox_status_total", None)
    monkeypatch.setattr(telemetry, "_delivery_attempt_total", None)
    monkeypatch.setattr(telemetry, "_execution_state_total", None)
    monkeypatch.setattr(telemetry, "_duplicate_suppression_total", None)

    telemetry.set_outbox_snapshot(pending_count=3, oldest_pending_age_seconds=4.5)
    telemetry.observe_outbox_claim_batch(2)
    telemetry.observe_outbox_process_duration(status="processed", duration_seconds=1.2)
    telemetry.increment_outbox_status("processed")
    telemetry.increment_delivery_attempt(status="completed")
    telemetry.increment_execution_state("running")
    telemetry.increment_duplicate_suppression()


def test_telemetry_helpers_clamp_values_and_normalize_labels(
    monkeypatch: MonkeyPatch,
) -> None:
    metrics = _install_metrics(monkeypatch)

    telemetry.set_outbox_snapshot(pending_count=-3, oldest_pending_age_seconds=-1.5)
    telemetry.observe_outbox_claim_batch(-2)
    telemetry.observe_outbox_process_duration(
        status="dead: letter",
        duration_seconds=-0.5,
    )
    telemetry.increment_outbox_status("failed: pending")
    telemetry.increment_delivery_attempt(status="running: duplicate")
    telemetry.increment_execution_state("waiting: signal")
    telemetry.increment_duplicate_suppression()

    assert metrics["pending"].set_values == [0]
    assert metrics["oldest_age"].set_values == [0.0]
    assert metrics["claim_batch"].observations == [0]
    assert metrics["process_duration"].label_calls == [("dead__letter",)]
    assert metrics["process_duration"].observations == [0.0]
    assert metrics["outbox_status"].label_calls == [("failed__pending",)]
    assert metrics["outbox_status"].inc_count == 1
    assert metrics["delivery_attempt"].label_calls == [("running__duplicate",)]
    assert metrics["delivery_attempt"].inc_count == 1
    assert metrics["execution_state"].label_calls == [("waiting__signal",)]
    assert metrics["execution_state"].inc_count == 1
    assert metrics["duplicate_suppression"].inc_count == 1


def test_telemetry_label_helpers_reject_non_string_labels_when_enabled(
    monkeypatch: MonkeyPatch,
) -> None:
    _install_metrics(monkeypatch)

    with pytest.raises(TypeError):
        telemetry.increment_outbox_status(cast(str, 1))

    with pytest.raises(TypeError):
        telemetry.increment_delivery_attempt(status=cast(str, 1))

    with pytest.raises(TypeError):
        telemetry.increment_execution_state(cast(str, 1))

    with pytest.raises(TypeError):
        telemetry.observe_outbox_process_duration(
            status=cast(str, 1),
            duration_seconds=1.0,
        )


def test_extract_outbox_snapshot_payload_defaults_and_coerces_values() -> None:
    assert telemetry.extract_outbox_snapshot_payload({}) == (0, 0.0)
    assert telemetry.extract_outbox_snapshot_payload(
        {
            "pending_count": "7",
            "oldest_pending_age_seconds": "2.5",
        }
    ) == (7, 2.5)
    assert telemetry.extract_outbox_snapshot_payload(
        {
            "pending_count": None,
            "oldest_pending_age_seconds": None,
        }
    ) == (0, 0.0)
    assert telemetry.extract_outbox_snapshot_payload(
        {
            "pending_count": False,
            "oldest_pending_age_seconds": b"",
        }
    ) == (0, 0.0)
    assert telemetry.extract_outbox_snapshot_payload(
        {
            "pending_count": [],
            "oldest_pending_age_seconds": {},
        }
    ) == (0, 0.0)
    assert telemetry.extract_outbox_snapshot_payload(
        {
            "pending_count": -1,
            "oldest_pending_age_seconds": -2.5,
        }
    ) == (-1, -2.5)
    assert telemetry.extract_outbox_snapshot_payload(
        {
            "pending_count": 1.9,
            "oldest_pending_age_seconds": True,
        }
    ) == (1, 1.0)
    pending_count, oldest_age = telemetry.extract_outbox_snapshot_payload(
        {
            "pending_count": True,
            "oldest_pending_age_seconds": "nan",
        }
    )
    assert pending_count == 1
    assert math.isnan(oldest_age)


def test_extract_outbox_snapshot_payload_reports_uncoercible_values() -> None:
    with pytest.raises(TypeError):
        telemetry.extract_outbox_snapshot_payload({"pending_count": object()})

    with pytest.raises(TypeError):
        telemetry.extract_outbox_snapshot_payload(
            {"oldest_pending_age_seconds": object()}
        )

    with pytest.raises(ValueError):
        telemetry.extract_outbox_snapshot_payload({"pending_count": "not-a-number"})

    with pytest.raises(ValueError):
        telemetry.extract_outbox_snapshot_payload({"pending_count": "1.2"})

    with pytest.raises(OverflowError):
        telemetry.extract_outbox_snapshot_payload({"pending_count": float("inf")})
