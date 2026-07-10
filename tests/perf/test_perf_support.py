from __future__ import annotations

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

    assert budgets.observations == {"INVALID": 1}


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
    def build_values() -> list[int]:
        return list(range(10))

    observation = capture_diagnostics(build_values)

    assert observation.result == list(range(10))
    assert observation.elapsed_seconds >= 0
    assert observation.peak_bytes >= 0
