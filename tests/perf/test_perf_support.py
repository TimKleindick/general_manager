from __future__ import annotations

import tracemalloc
from dataclasses import FrozenInstanceError

import pytest

from tests.perf.support import (
    Counter,
    CountingIterable,
    DiagnosticObservation,
    PerfBudgets,
    capture_diagnostics,
)

pytestmark = pytest.mark.perf


def test_counter_increments_by_one_by_default() -> None:
    counter = Counter()

    counter.increment()

    assert counter.value == 1


def test_counter_increments_by_a_requested_amount() -> None:
    counter = Counter(2)

    counter.increment(3)

    assert counter.value == 5


def test_counter_resets_to_zero() -> None:
    counter = Counter(4)

    counter.reset()

    assert counter.value == 0


def test_counting_iterable_counts_each_yield() -> None:
    counter = Counter()
    values = CountingIterable(range(3), counter)

    assert list(values) == [0, 1, 2]
    assert counter.value == 3


def test_diagnostic_observation_is_frozen() -> None:
    observation = DiagnosticObservation(
        result="result",
        elapsed_seconds=0.0,
        peak_bytes=0,
    )

    result_attribute = "result"
    with pytest.raises(FrozenInstanceError):
        setattr(observation, result_attribute, "changed")


def test_budget_rejects_an_observation_above_its_ceiling() -> None:
    budgets = PerfBudgets({"CASE_QUERIES": 1})

    with pytest.raises(AssertionError, match=r"CASE_QUERIES.*observed=2.*ceiling=1"):
        budgets.assert_observation("CASE_QUERIES", 2)

    assert budgets.observations == {"CASE_QUERIES": 2}


def test_record_mode_collects_and_prints_without_enforcing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    budgets = PerfBudgets({"CASE_CALLBACKS": 0}, record=True)

    budgets.assert_observation("CASE_CALLBACKS", 7)

    assert budgets.observations == {"CASE_CALLBACKS": 7}
    assert capsys.readouterr().out == "PERF_OBSERVATION CASE_CALLBACKS=7\n"


def test_budget_rejects_a_missing_name() -> None:
    budgets = PerfBudgets({})

    with pytest.raises(AssertionError, match="missing performance budget: MISSING"):
        budgets.assert_observation("MISSING", 1)


@pytest.mark.parametrize("ceiling", ["1", True, 1.5, -1])
def test_budget_rejects_an_invalid_ceiling(ceiling: object) -> None:
    budgets = PerfBudgets({"INVALID": ceiling})

    with pytest.raises(AssertionError, match="invalid performance budget: INVALID"):
        budgets.assert_observation("INVALID", 1)

    assert budgets.observations == {}


@pytest.mark.parametrize("ceiling", ["1", True, 1.5, -1])
def test_record_mode_rejects_an_invalid_ceiling_before_recording(
    ceiling: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    budgets = PerfBudgets({"INVALID": ceiling}, record=True)

    with pytest.raises(AssertionError, match="invalid performance budget: INVALID"):
        budgets.assert_observation("INVALID", 1)

    assert budgets.observations == {}
    assert capsys.readouterr().out == ""


def test_manifest_validation_reports_sorted_missing_unused_and_invalid_names() -> None:
    budgets = PerfBudgets(
        {
            "USED": 1,
            "UNUSED": 2,
            "BOOLEAN": True,
            "FLOAT": 1.5,
            "NEGATIVE": -1,
            "STRING": "1",
        }
    )
    budgets.assert_observation("USED", 1)

    with pytest.raises(AssertionError) as exc_info:
        budgets.validate_manifest({"USED", "MISSING"})

    message = str(exc_info.value)
    assert "missing=['MISSING']" in message
    assert "unused=['BOOLEAN', 'FLOAT', 'NEGATIVE', 'STRING', 'UNUSED']" in message
    assert "invalid=['BOOLEAN', 'FLOAT', 'NEGATIVE', 'STRING']" in message


def test_integer_zero_is_a_valid_budget() -> None:
    budgets = PerfBudgets({"ZERO": 0})

    budgets.assert_observation("ZERO", 0)
    budgets.validate_manifest({"ZERO"})

    assert budgets.observations == {"ZERO": 0}


def test_capture_diagnostics_returns_result_elapsed_and_peak_bytes() -> None:
    allocation_size = 64 * 1024

    def build_values() -> bytearray:
        return bytearray(allocation_size)

    observation = capture_diagnostics(build_values)

    assert len(observation.result) == allocation_size
    assert observation.elapsed_seconds >= 0
    assert observation.peak_bytes > 0


def test_capture_diagnostics_restores_inactive_tracing_after_success() -> None:
    tracing_was_active = tracemalloc.is_tracing()
    if tracing_was_active:
        tracemalloc.stop()

    try:
        observation = capture_diagnostics(lambda: "result")

        assert observation.result == "result"
        assert not tracemalloc.is_tracing()
    finally:
        if tracing_was_active:
            tracemalloc.start()
        elif tracemalloc.is_tracing():
            tracemalloc.stop()


def test_capture_diagnostics_preserves_active_tracing_after_success() -> None:
    tracing_was_active = tracemalloc.is_tracing()
    if not tracing_was_active:
        tracemalloc.start()

    try:
        observation = capture_diagnostics(lambda: "result")

        assert observation.result == "result"
        assert observation.peak_bytes >= 0
        assert tracemalloc.is_tracing()
    finally:
        if tracing_was_active and not tracemalloc.is_tracing():
            tracemalloc.start()
        elif not tracing_was_active and tracemalloc.is_tracing():
            tracemalloc.stop()


def test_capture_diagnostics_propagates_exception_and_restores_tracing() -> None:
    tracing_was_active = tracemalloc.is_tracing()
    if not tracing_was_active:
        tracemalloc.start()

    def fail() -> None:
        message = "callback failed"
        raise RuntimeError(message)

    try:
        with pytest.raises(RuntimeError, match="callback failed"):
            capture_diagnostics(fail)

        assert tracemalloc.is_tracing()
    finally:
        if tracing_was_active and not tracemalloc.is_tracing():
            tracemalloc.start()
        elif not tracing_was_active and tracemalloc.is_tracing():
            tracemalloc.stop()
